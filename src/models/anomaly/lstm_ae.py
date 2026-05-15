"""
LSTM Autoencoder 기반 이상탐지 (3단계).
시계열 윈도우를 인코딩·복원해 재구성 오차(MSE)로 이상 판단.
계절성·시간대 맥락을 자동으로 반영한다.

학습: fit(df)
추론: detect(df)
"""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

MODEL_PATH  = Path(__file__).parent / "saved" / "lstm_ae.pt"
META_PATH   = Path(__file__).parent / "saved" / "lstm_ae_meta.pkl"

FEATURE_COLS  = ["grid_P", "chp_P", "cool_elec_P", "Ta", "cop"]
WINDOW        = 24      # 24h 윈도우
HIDDEN_DIM    = 64
NUM_LAYERS    = 2
EPOCHS        = 30
BATCH_SIZE    = 64
LR            = 1e-3
# 이상 임계값: 정상 재구성 오차의 95 퍼센타일
THRESHOLD_PCT = 95


class LSTMAutoEncoder(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int = HIDDEN_DIM,
                 num_layers: int = NUM_LAYERS):
        super().__init__()
        self.encoder = nn.LSTM(input_dim, hidden_dim, num_layers,
                               batch_first=True, dropout=0.2)
        self.decoder = nn.LSTM(hidden_dim, hidden_dim, num_layers,
                               batch_first=True, dropout=0.2)
        self.output  = nn.Linear(hidden_dim, input_dim)

    def forward(self, x):                        # x: (B, T, F)
        _, (h, c) = self.encoder(x)
        # 디코더 입력: 마지막 hidden을 T 타임스텝으로 반복
        dec_in = h[-1].unsqueeze(1).repeat(1, x.size(1), 1)
        out, _ = self.decoder(dec_in, (h, c))
        return self.output(out)                  # (B, T, F)


def _make_windows(arr: np.ndarray, window: int) -> np.ndarray:
    """(N, F) → (N-window+1, window, F) 슬라이딩 윈도우."""
    n = len(arr) - window + 1
    if n <= 0:
        return np.empty((0, window, arr.shape[1]))
    return np.stack([arr[i:i+window] for i in range(n)])


def _prepare(df: pd.DataFrame):
    """피처 정규화 + NaN 행 채우기 (forward fill)."""
    cols = [c for c in FEATURE_COLS if c in df.columns]
    sub  = df[cols].copy()
    # 발전량 미수집 → 0, 나머지 ffill/bfill 후 0으로 최종 처리
    for c in ["pv_P", "chp_P"]:
        if c in sub.columns:
            sub[c] = sub[c].fillna(0.0)
    sub  = sub.ffill().bfill().fillna(0.0)
    mu   = sub.mean().values
    sigma = sub.std().values
    sigma = np.where(sigma == 0, 1.0, sigma)
    arr  = ((sub - mu) / sigma).values.astype(np.float32)
    return arr, mu, sigma, cols


def fit(df: pd.DataFrame) -> None:
    """LSTM AE 학습 및 저장."""
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    arr, mu, sigma, cols = _prepare(df)
    windows = _make_windows(arr, WINDOW)
    if len(windows) < BATCH_SIZE:
        raise ValueError(f"학습 데이터 부족: 윈도우 {len(windows)}개")

    X = torch.tensor(windows)
    loader = DataLoader(TensorDataset(X), batch_size=BATCH_SIZE, shuffle=True)

    model = LSTMAutoEncoder(input_dim=len(cols)).to(device)
    opt   = torch.optim.Adam(model.parameters(), lr=LR)
    loss_fn = nn.MSELoss()

    print(f"LSTM AE 학습 시작: {len(windows)}개 윈도우, {EPOCHS}에폭, {device}")
    model.train()
    for epoch in range(1, EPOCHS + 1):
        total = 0.0
        for (batch,) in loader:
            batch = batch.to(device)
            opt.zero_grad()
            recon = model(batch)
            loss  = loss_fn(recon, batch)
            loss.backward()
            opt.step()
            total += loss.item()
        if epoch % 5 == 0:
            print(f"  에폭 {epoch:3d}/{EPOCHS} | 손실 {total/len(loader):.5f}")

    # 임계값: 학습 데이터 재구성 오차 95 퍼센타일
    model.eval()
    with torch.no_grad():
        recon = model(X.to(device)).cpu().numpy()
    errors = np.mean((windows - recon) ** 2, axis=(1, 2))
    threshold = float(np.percentile(errors, THRESHOLD_PCT))

    torch.save(model.state_dict(), MODEL_PATH)
    meta = {"mu": mu, "sigma": sigma, "cols": cols,
            "input_dim": len(cols), "threshold": threshold}
    with open(META_PATH, "wb") as f:
        pickle.dump(meta, f)
    print(f"LSTM AE 저장 완료 → {MODEL_PATH} | 임계값: {threshold:.6f}")


def detect(df: pd.DataFrame) -> pd.DataFrame:
    """
    저장된 모델로 이상 탐지.
    모델 없으면 자동 학습.

    반환: 원본 df에 아래 컬럼 추가
      - anomaly_lstm : bool
      - score_lstm   : float (재구성 오차; 높을수록 이상)
    윈도우 크기보다 앞의 행은 NaN으로 남겨두지 않고 False/0으로 처리.
    """
    result = df.copy()
    result["anomaly_lstm"] = False
    result["score_lstm"]   = 0.0

    if not MODEL_PATH.exists():
        print("[LSTM AE] 저장된 모델 없음 → 현재 데이터로 학습")
        fit(df)

    with open(META_PATH, "rb") as f:
        meta = pickle.load(f)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model  = LSTMAutoEncoder(input_dim=meta["input_dim"]).to(device)
    model.load_state_dict(torch.load(MODEL_PATH, map_location=device, weights_only=True))
    model.eval()

    cols  = [c for c in meta["cols"] if c in df.columns]
    sub   = df[cols].copy().ffill().bfill()
    arr   = ((sub - meta["mu"][:len(cols)]) / meta["sigma"][:len(cols)]).values.astype(np.float32)
    windows = _make_windows(arr, WINDOW)

    if len(windows) == 0:
        return result

    with torch.no_grad():
        X     = torch.tensor(windows).to(device)
        recon = model(X).cpu().numpy()

    errors = np.mean((windows - recon) ** 2, axis=(1, 2))
    threshold = meta["threshold"]

    # 윈도우 마지막 타임스텝에 점수 할당 (보수적 — 윈도우 전체에서 오차 반영)
    start_idx = WINDOW - 1
    end_idx   = start_idx + len(errors)
    result.iloc[start_idx:end_idx, result.columns.get_loc("score_lstm")]   = errors
    result.iloc[start_idx:end_idx, result.columns.get_loc("anomaly_lstm")] = errors > threshold

    return result


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from data.loader import load_range

    df_train = load_range("2021-01-01", "2023-01-01")
    print(f"학습 데이터: {len(df_train)}행")
    fit(df_train)

    df_test = load_range("2022-07-01", "2022-08-01")
    out = detect(df_test)
    n = out["anomaly_lstm"].sum()
    print(f"탐지된 이상: {n}건 / {len(out)}행 ({n/len(out):.1%})")
