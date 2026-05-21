"""
LSTM 중장기 예측 모델 — 계통 전력 소비 (grid_P) 24~168시간 예측.
다변량 입력: grid_P, Ta, Igm, hour_sin/cos, dayofweek_sin/cos.
MLflow 실험 추적.
"""

import pickle
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error

SAVE_DIR  = Path(__file__).parent / "saved"
MODEL_DIR = SAVE_DIR / "lstm_forecast"
MLFLOW_URI = str(Path(__file__).parent / "saved" / "mlruns")

WINDOW     = 168    # 7일 입력
HIDDEN_DIM = 64
NUM_LAYERS = 1
EPOCHS     = 8
BATCH_SIZE = 256
LR         = 1e-3
HORIZON    = 24     # 기본 예측 시간


FEATURE_COLS = ["grid_P_kw", "Ta", "Igm",
                "hour_sin", "hour_cos",
                "dow_sin",  "dow_cos",
                "is_weekend"]


class LSTMForecaster(nn.Module):
    def __init__(self, input_dim, hidden_dim, num_layers, output_dim):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers,
                            batch_first=True, dropout=0.2)
        self.fc   = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :])   # 마지막 타임스텝 출력


def _prepare(df: pd.DataFrame, horizon: int = HORIZON) -> pd.DataFrame:
    d = df[["ts", "grid_P", "Ta", "Igm"]].copy()
    d["ts"] = pd.to_datetime(d["ts"]).dt.tz_localize(None)
    d = d.sort_values("ts").set_index("ts")
    d["grid_P_kw"] = d["grid_P"] / 1000
    d["Ta"]  = d["Ta"].ffill().bfill()
    d["Igm"] = d["Igm"].ffill().fillna(0)

    # 순환 인코딩
    d["hour_sin"]  = np.sin(2 * np.pi * d.index.hour / 24)
    d["hour_cos"]  = np.cos(2 * np.pi * d.index.hour / 24)
    d["dow_sin"]   = np.sin(2 * np.pi * d.index.dayofweek / 7)
    d["dow_cos"]   = np.cos(2 * np.pi * d.index.dayofweek / 7)
    d["is_weekend"]= (d.index.dayofweek >= 5).astype(float)

    return d[FEATURE_COLS].dropna().reset_index()


def _make_sequences(arr: np.ndarray, window: int, horizon: int):
    n = len(arr) - window - horizon + 1
    # stride_tricks로 Python 루프 없이 (n, window, features) 배열 생성
    sx, sf = arr.strides
    X = np.lib.stride_tricks.as_strided(
        arr, shape=(n, window, arr.shape[1]), strides=(sx, sx, sf)
    ).astype(np.float32)
    y_col = np.ascontiguousarray(arr[window:, 0])
    y = np.lib.stride_tricks.as_strided(
        y_col, shape=(n, horizon), strides=(y_col.strides[0], y_col.strides[0])
    ).astype(np.float32)
    return X, y


def train(df: pd.DataFrame, horizon: int = HORIZON, status_file: "Path | None" = None) -> dict:
    """LSTM 학습 + MLflow 기록. 반환: {"model", "scaler", "horizon"}"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    d    = _prepare(df, horizon)
    vals = d[FEATURE_COLS].values

    scaler = StandardScaler()
    vals_s = scaler.fit_transform(vals)

    # 학습 / 검증 분할 (마지막 10%)
    split  = int(len(vals_s) * 0.9)
    X_tr, y_tr = _make_sequences(vals_s[:split], WINDOW, horizon)
    X_val,y_val= _make_sequences(vals_s[split:], WINDOW, horizon)

    tr_ds  = torch.utils.data.TensorDataset(torch.from_numpy(X_tr), torch.from_numpy(y_tr))
    val_ds = torch.utils.data.TensorDataset(torch.from_numpy(X_val),torch.from_numpy(y_val))
    tr_dl  = torch.utils.data.DataLoader(tr_ds,  batch_size=BATCH_SIZE, shuffle=True)
    val_dl = torch.utils.data.DataLoader(val_ds, batch_size=BATCH_SIZE)

    model     = LSTMForecaster(len(FEATURE_COLS), HIDDEN_DIM, NUM_LAYERS, horizon).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    criterion = nn.MSELoss()
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=3, factor=0.5)

    params = dict(window=WINDOW, hidden_dim=HIDDEN_DIM, num_layers=NUM_LAYERS,
                  epochs=EPOCHS, batch_size=BATCH_SIZE, lr=LR, horizon=horizon)

    mlflow.set_tracking_uri(MLFLOW_URI)
    mlflow.set_experiment("forecasting_lstm")

    with mlflow.start_run(run_name=f"lstm_grid_P_h{horizon}"):
        mlflow.log_params(params)
        mlflow.log_param("device", device)

        best_val_loss = float("inf")
        for epoch in range(1, EPOCHS + 1):
            model.train()
            tr_loss = 0
            for xb, yb in tr_dl:
                xb, yb = xb.to(device), yb.to(device)
                optimizer.zero_grad()
                loss = criterion(model(xb), yb)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                tr_loss += loss.item() * len(xb)
            tr_loss /= len(tr_ds)

            model.eval()
            val_loss = 0
            with torch.no_grad():
                for xb, yb in val_dl:
                    xb, yb = xb.to(device), yb.to(device)
                    val_loss += criterion(model(xb), yb).item() * len(xb)
            val_loss /= len(val_ds)
            scheduler.step(val_loss)

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                torch.save(model.state_dict(), MODEL_DIR / "best.pt")

            if epoch % 2 == 0 or epoch == 1:
                print(f"  Epoch {epoch:3d}/{EPOCHS}  tr={tr_loss:.4f}  val={val_loss:.4f}")
            if status_file:
                status_file.write_text(f"running: epoch {epoch}/{EPOCHS}")
            mlflow.log_metrics({"train_loss": tr_loss, "val_loss": val_loss}, step=epoch)

        # 검증 MAE (원래 스케일 복원)
        model.load_state_dict(torch.load(MODEL_DIR / "best.pt", weights_only=True))
        model.eval()
        preds_s, trues_s = [], []
        with torch.no_grad():
            for xb, yb in val_dl:
                preds_s.append(model(xb.to(device)).cpu().numpy())
                trues_s.append(yb.numpy())
        preds_s = np.concatenate(preds_s)
        trues_s = np.concatenate(trues_s)

        # 역정규화 (grid_P_kw = 컬럼 0)
        dummy  = np.zeros((len(preds_s.ravel()), len(FEATURE_COLS)))
        dummy[:, 0] = preds_s.ravel()
        preds_kw = scaler.inverse_transform(dummy)[:, 0].reshape(preds_s.shape)
        dummy[:, 0] = trues_s.ravel()
        trues_kw = scaler.inverse_transform(dummy)[:, 0].reshape(trues_s.shape)

        mae  = mean_absolute_error(trues_kw.ravel(), preds_kw.ravel())
        rmse = np.sqrt(mean_squared_error(trues_kw.ravel(), preds_kw.ravel()))
        mape = np.mean(np.abs((trues_kw.ravel() - preds_kw.ravel()) / (np.abs(trues_kw.ravel()) + 1e-6)))
        mlflow.log_metrics({"val_mae": mae, "val_rmse": rmse, "val_mape": mape})
        print(f"LSTM 학습 완료 — MAE={mae:.1f} kW  RMSE={rmse:.1f} kW  MAPE={mape:.3f}")

        with open(MODEL_DIR / "scaler.pkl", "wb") as f:
            pickle.dump(scaler, f)
        with open(MODEL_DIR / "meta.pkl", "wb") as f:
            pickle.dump({"horizon": horizon, "feature_cols": FEATURE_COLS,
                         "window": WINDOW, "hidden_dim": HIDDEN_DIM, "num_layers": NUM_LAYERS}, f)

    return {"model": model, "scaler": scaler, "horizon": horizon}


def predict(df: pd.DataFrame, horizon: int = HORIZON) -> pd.DataFrame:
    """저장된 모델로 예측. 반환: ts, yhat (kW)"""
    device = "mps" if torch.backends.mps.is_available() else "cpu"

    with open(MODEL_DIR / "scaler.pkl", "rb") as f:
        scaler = pickle.load(f)
    with open(MODEL_DIR / "meta.pkl", "rb") as f:
        meta = pickle.load(f)

    saved_horizon = meta["horizon"]
    model = LSTMForecaster(len(FEATURE_COLS), meta["hidden_dim"], meta["num_layers"], saved_horizon).to(device)
    model.load_state_dict(torch.load(MODEL_DIR / "best.pt", weights_only=True))
    model.eval()

    d    = _prepare(df)
    vals = d[FEATURE_COLS].values
    vals_s = scaler.transform(vals)

    if len(vals_s) < WINDOW:
        raise ValueError(f"입력 데이터 부족 (최소 {WINDOW}행 필요)")

    window_data = vals_s[-WINDOW:]
    x = torch.from_numpy(window_data.astype(np.float32)).unsqueeze(0).to(device)

    with torch.no_grad():
        out_s = model(x).cpu().numpy().flatten()[:horizon]

    # 역정규화
    dummy = np.zeros((len(out_s), len(FEATURE_COLS)))
    dummy[:, 0] = out_s
    yhat_kw = scaler.inverse_transform(dummy)[:, 0]

    last_ts = pd.to_datetime(d["ts"].iloc[-1])
    ts_list = [last_ts + pd.Timedelta(hours=i+1) for i in range(horizon)]
    return pd.DataFrame({"ts": ts_list, "yhat": np.round(yhat_kw, 2)})


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from data.loader import load_range

    print("데이터 로드 중...")
    df = load_range("2018-01-01", "2024-01-01")
    print(f"  {len(df)}행 로드")

    print("LSTM 학습 중 (horizon=24h)...")
    train(df, horizon=24)

    print("24시간 예측...")
    fc = predict(df, horizon=24)
    print(fc.head(10).to_string(index=False))
