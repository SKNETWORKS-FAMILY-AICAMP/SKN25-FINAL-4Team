"""LSTM 기반 Grid 전기 소비량 1시간 앞 예측 모델 학습.

슬라이딩 윈도우(24시간 입력 → 1시간 예측) 방식.

Usage:
    uv run --with torch --with pandas --with "psycopg[binary]" \
           --with python-dotenv --with scikit-learn --with mlflow \
           python scripts/ml/train_lstm.py
"""

from __future__ import annotations

import os
import pickle
from pathlib import Path

import mlflow
import numpy as np
import torch
import torch.nn as nn
from dotenv import load_dotenv
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import MinMaxScaler

from data_loader import FEATURE_COLS, TARGET_COL, add_features, get_splits, load_raw

load_dotenv()

OUT_DIR = Path("outputs/models")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SEQ_LEN    = 24
BATCH_SIZE = 256
EPOCHS     = 50
LR         = 1e-3
HIDDEN_DIM = 128
NUM_LAYERS = 2
DROPOUT    = 0.2
DEVICE     = "cuda" if torch.cuda.is_available() else \
             "mps"  if torch.backends.mps.is_available() else "cpu"


class LSTMForecaster(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, num_layers: int, dropout: float):
        super().__init__()
        self.lstm = nn.LSTM(
            input_dim, hidden_dim, num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :]).squeeze(-1)


def make_sequences(X: np.ndarray, y: np.ndarray, seq_len: int):
    Xs, ys = [], []
    for i in range(len(X) - seq_len):
        Xs.append(X[i: i + seq_len])
        ys.append(y[i + seq_len])
    return np.array(Xs, dtype=np.float32), np.array(ys, dtype=np.float32)


def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    mask = y_true > 1.0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def evaluate(model: nn.Module, loader, scaler_y: MinMaxScaler, y_seq: np.ndarray):
    model.eval()
    preds = []
    with torch.no_grad():
        for Xb, _ in loader:
            preds.append(model(Xb.to(DEVICE)).cpu().numpy())
    y_pred_s = np.concatenate(preds)
    y_true = scaler_y.inverse_transform(y_seq.reshape(-1, 1)).ravel()
    y_pred = np.maximum(scaler_y.inverse_transform(y_pred_s.reshape(-1, 1)).ravel(), 0)
    return (
        mean_absolute_error(y_true, y_pred),
        np.sqrt(mean_squared_error(y_true, y_pred)),
        mape(y_true, y_pred),
    )


def main() -> None:
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", ""))
    mlflow.set_experiment("SSA-IPSO-LSTM")

    print(f"▶ 디바이스: {DEVICE}")
    print("▶ 데이터 로드 중...")
    df = load_raw()
    df = add_features(df)
    train, val, test = get_splits(df)

    print(f"  학습  : {train.index[0].date()} ~ {train.index[-1].date()} ({len(train):,}행)")
    print(f"  검증  : {val.index[0].date()} ~ {val.index[-1].date()} ({len(val):,}행)")
    print(f"  테스트: {test.index[0].date()} ~ {test.index[-1].date()} ({len(test):,}행)")

    scaler_X = MinMaxScaler()
    scaler_y = MinMaxScaler()

    X_train = scaler_X.fit_transform(train[FEATURE_COLS])
    y_train = scaler_y.fit_transform(train[[TARGET_COL]]).ravel()
    X_val   = scaler_X.transform(val[FEATURE_COLS])
    y_val   = scaler_y.transform(val[[TARGET_COL]]).ravel()
    X_test  = scaler_X.transform(test[FEATURE_COLS])
    y_test  = scaler_y.transform(test[[TARGET_COL]]).ravel()

    X_train_s, y_train_s = make_sequences(X_train, y_train, SEQ_LEN)
    X_val_s,   y_val_s   = make_sequences(X_val,   y_val,   SEQ_LEN)
    X_test_s,  y_test_s  = make_sequences(X_test,  y_test,  SEQ_LEN)

    def make_loader(X, y, shuffle):
        ds = torch.utils.data.TensorDataset(torch.from_numpy(X), torch.from_numpy(y))
        return torch.utils.data.DataLoader(ds, batch_size=BATCH_SIZE, shuffle=shuffle)

    train_loader = make_loader(X_train_s, y_train_s, shuffle=True)
    val_loader   = make_loader(X_val_s,   y_val_s,   shuffle=False)
    test_loader  = make_loader(X_test_s,  y_test_s,  shuffle=False)

    model = LSTMForecaster(len(FEATURE_COLS), HIDDEN_DIM, NUM_LAYERS, DROPOUT).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, patience=5, factor=0.5, min_lr=1e-5)
    criterion = nn.HuberLoss()

    best_val_loss = float("inf")
    best_state    = None

    with mlflow.start_run(run_name="LSTM"):
        mlflow.log_params({
            "seq_len": SEQ_LEN, "hidden_dim": HIDDEN_DIM, "num_layers": NUM_LAYERS,
            "dropout": DROPOUT, "epochs": EPOCHS, "lr": LR, "batch_size": BATCH_SIZE,
            "n_features": len(FEATURE_COLS), "scaler": "MinMaxScaler",
        })

        print(f"▶ LSTM 학습 중 (epochs={EPOCHS})...")
        for epoch in range(1, EPOCHS + 1):
            model.train()
            t_losses = []
            for Xb, yb in train_loader:
                Xb, yb = Xb.to(DEVICE), yb.to(DEVICE)
                optimizer.zero_grad()
                loss = criterion(model(Xb), yb)
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                t_losses.append(loss.item())

            model.eval()
            v_losses = []
            with torch.no_grad():
                for Xb, yb in val_loader:
                    Xb, yb = Xb.to(DEVICE), yb.to(DEVICE)
                    v_losses.append(criterion(model(Xb), yb).item())

            t_loss = float(np.mean(t_losses))
            v_loss = float(np.mean(v_losses))
            scheduler.step(v_loss)
            mlflow.log_metrics({"train_loss": t_loss, "val_loss": v_loss}, step=epoch)

            if v_loss < best_val_loss:
                best_val_loss = v_loss
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

            if epoch % 10 == 0 or epoch == 1:
                print(f"  Epoch {epoch:3d} | train={t_loss:.6f} | val={v_loss:.6f}")

        model.load_state_dict(best_state)

        for split_name, loader, y_seq in [
            ("val",  val_loader,  y_val_s),
            ("test", test_loader, y_test_s),
        ]:
            mae_v, rmse_v, mape_v = evaluate(model, loader, scaler_y, y_seq)
            print(f"\n▶ {split_name} 결과")
            print(f"  MAE  : {mae_v:.2f} W  |  RMSE : {rmse_v:.2f} W  |  MAPE : {mape_v:.2f}%")
            mlflow.log_metrics({
                f"{split_name}_mae":  round(mae_v, 4),
                f"{split_name}_rmse": round(rmse_v, 4),
                f"{split_name}_mape": round(mape_v, 4),
            })

        # 저장
        model_path = OUT_DIR / "lstm_grid_electricity.pt"
        torch.save({
            "model_state":  best_state,
            "model_config": {
                "input_dim": len(FEATURE_COLS), "hidden_dim": HIDDEN_DIM,
                "num_layers": NUM_LAYERS, "dropout": DROPOUT, "seq_len": SEQ_LEN,
            },
        }, model_path)

        scaler_path = OUT_DIR / "lstm_scaler.pkl"
        with open(scaler_path, "wb") as f:
            pickle.dump({"scaler_X": scaler_X, "scaler_y": scaler_y}, f)

        mlflow.log_artifact(str(model_path))
        mlflow.log_artifact(str(scaler_path))
        print(f"\n▶ 모델 저장: {model_path}")


if __name__ == "__main__":
    main()
