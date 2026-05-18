"""
SSA-IPSO-LSTM 전력 소비량 예측 모델
논문: Lai et al. (2025) "Deep Learning-Based Energy Consumption Prediction Model
      for Green Industrial Parks", Applied Artificial Intelligence

적용 대상: Honda R&D Europe GmbH 스마트빌딩 에너지 데이터
예측 대상: grid_P (부지 전체 그리드 소비량)
입력 변수: grid_P, pv_P, chp_P, Ta, Igm, 시간 피처 (sin/cos 인코딩)
해상도  : 1시간 단위
분할    : train 2018~2020 / validation 2021 / test 2022~2023

실행 방법:
    python run_ssa_ipso_lstm.py

환경 변수 (.env):
    DATABASE_URL=postgresql://user:password@host:5432/dbname
"""

import os
import math
import warnings
import numpy as np
import pandas as pd
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error
import matplotlib.pyplot as plt
import mlflow
import mlflow.pytorch

warnings.filterwarnings("ignore")
load_dotenv()

# ── 설정 ──────────────────────────────────────────────────────────

DB_URL      = os.getenv("DATABASE_URL")
TZ_LOCAL    = "Europe/Berlin"
DEVICE      = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# 논문 확정 파라미터
SSA_WINDOW  = 12       # SSA sliding window length (논문 windowlen=12)
TIMESTEP    = 24       # LSTM timestep (24시간 주기)
EPOCHS      = 100      # LSTM 학습 epoch
LR          = 0.001    # Adam learning rate
BATCH_SIZE_DEFAULT = 24

# IPSO 파라미터
IPSO_MAX_ITER  = 50
IPSO_N_PARTICLES = 10
W_MAX, W_MIN   = 0.9, 0.2
C1, C2         = 2.0, 2.0

# 파라미터 탐색 범위 (units = p*32, batch_size = q*12)
P_RANGE = list(range(1, 5))   # 1~4
Q_RANGE = list(range(1, 8))   # 1~7

# 데이터 분할 기간 (train 4년 / validation 1년 / test 1년)
TRAIN_START = "2018-01-01"
TRAIN_END   = "2022-01-01"
VAL_START   = "2022-01-01"
VAL_END     = "2023-01-01"
TEST_START  = "2023-01-01"
TEST_END    = "2024-01-01"

# ── DB 로더 ───────────────────────────────────────────────────────

_GRID_METERS      = ["V.Z81", "V.Z82", "H2.Z35", "H2.Z36", "H2.Z351", "H2.Z361"]
_PV_METERS        = ["H1.Z310", "H2.Z311", "H3.Z312", "V.Z84"]
_CHP_ELEC_PRIMARY = ["H1.ZE20"]
_CHP_ELEC_FALLBACK= ["H1.Z20"]
_WEATHER          = ["WeatherStation.Weather"]
_ALL_METERS = _GRID_METERS + _PV_METERS + _CHP_ELEC_PRIMARY + _CHP_ELEC_FALLBACK + _WEATHER


def load_range(start_str: str, end_str: str) -> pd.DataFrame:
    """ems.cr_measurement_1h에서 필요 계량기만 로드 후 피벗."""
    tz = timezone.utc
    start = datetime.fromisoformat(start_str).replace(tzinfo=tz)
    end   = datetime.fromisoformat(end_str).replace(tzinfo=tz)

    meters_sql = ",".join(f"'{m}'" for m in _ALL_METERS)
    sql = f"""
        SELECT ts, meter_urn, measurement, value
        FROM ems.cr_measurement_1h
        WHERE ts >= %s AND ts < %s
          AND meter_urn IN ({meters_sql})
          AND measurement IN ('P', 'Igm', 'Ta')
        ORDER BY ts;
    """
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute(sql, (start, end))
        cols = [d[0] for d in cur.description]
        raw  = pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()

    if raw.empty:
        return pd.DataFrame()

    raw["ts"]  = pd.to_datetime(raw["ts"], utc=True).dt.tz_convert(TZ_LOCAL)
    raw["col"] = raw["meter_urn"] + "__" + raw["measurement"]
    pivot = raw.pivot_table(index="ts", columns="col", values="value", aggfunc="mean")

    def _sum_abs(cols):
        ex = [c for c in cols if c in pivot.columns]
        return pivot[ex].abs().sum(axis=1, min_count=1) if ex else pd.Series(np.nan, index=pivot.index)

    def _sum(cols):
        ex = [c for c in cols if c in pivot.columns]
        return pivot[ex].sum(axis=1, min_count=1) if ex else pd.Series(np.nan, index=pivot.index)

    df = pd.DataFrame(index=pivot.index)
    df["grid_P"] = _sum([f"{m}__P" for m in _GRID_METERS])
    df["pv_P"]   = _sum_abs([f"{m}__P" for m in _PV_METERS])

    _ze20, _z20 = "H1.ZE20__P", "H1.Z20__P"
    if _ze20 in pivot.columns and _z20 in pivot.columns:
        df["chp_P"] = pivot[_ze20].fillna(pivot[_z20]).abs()
    elif _ze20 in pivot.columns:
        df["chp_P"] = pivot[_ze20].abs()
    elif _z20 in pivot.columns:
        df["chp_P"] = pivot[_z20].abs()
    else:
        df["chp_P"] = np.nan

    df["Ta"]  = pivot.get("WeatherStation.Weather__Ta",  np.nan)
    df["Igm"] = pivot.get("WeatherStation.Weather__Igm", np.nan)

    return df.reset_index()


def build_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """시간 피처 생성: sin/cos 인코딩 (hour, dayofweek, month)."""
    ts = pd.to_datetime(df["ts"])
    df["hour_sin"]  = np.sin(2 * np.pi * ts.dt.hour / 24)
    df["hour_cos"]  = np.cos(2 * np.pi * ts.dt.hour / 24)
    df["dow_sin"]   = np.sin(2 * np.pi * ts.dt.dayofweek / 7)
    df["dow_cos"]   = np.cos(2 * np.pi * ts.dt.dayofweek / 7)
    df["month_sin"] = np.sin(2 * np.pi * ts.dt.month / 12)
    df["month_cos"] = np.cos(2 * np.pi * ts.dt.month / 12)
    return df


# ── SSA ───────────────────────────────────────────────────────────

class SSA:
    """
    Singular Spectrum Analysis.
    논문 수식 (1)(2) 기반: Embedding → SVD → Grouping → Diagonal Averaging
    """
    def __init__(self, window_len: int = 12):
        self.L = window_len
        self.components_ = None

    def fit_transform(self, x: np.ndarray) -> list[np.ndarray]:
        """시계열 x를 SSA 분해하여 IMF 리스트 반환."""
        N = len(x)
        L = self.L
        K = N - L + 1

        # 1단계: Embedding → trajectory matrix Z (L x K)
        Z = np.array([x[i:i+L] for i in range(K)]).T  # shape: (L, K)

        # 2단계: SVD
        U, s, Vt = np.linalg.svd(Z, full_matrices=False)
        r = len(s)

        # 3단계: Grouping + Diagonal Averaging → 각 성분 복원
        components = []
        for i in range(r):
            Zi = s[i] * np.outer(U[:, i], Vt[i, :])  # rank-1 matrix
            # Diagonal Averaging
            comp = self._diagonal_average(Zi, N)
            components.append(comp)

        self.components_ = components
        return components

    @staticmethod
    def _diagonal_average(mat: np.ndarray, N: int) -> np.ndarray:
        """논문 수식 (2): 대각선 평균으로 시계열 복원."""
        L, K = mat.shape
        P_star = min(L, K)
        Q_star = max(L, K)
        result = np.zeros(N)

        for c in range(1, N + 1):
            vals = []
            if L <= K:
                T = mat
            else:
                T = mat.T
                L, K = K, L  # swap for formula

            for i in range(1, L + 1):
                j = c - i + 1
                if 1 <= j <= K:
                    if L <= K:
                        vals.append(mat[i-1, j-1])
                    else:
                        vals.append(mat.T[i-1, j-1])
            if vals:
                result[c-1] = np.mean(vals)

            # reset after swap
            if L > K:
                L, K = K, L
                mat = mat.T

        return result


def fuzzy_entropy(x: np.ndarray, m: int = 2, r: float = 0.2) -> float:
    """
    Fuzzy Entropy 계산.
    논문에서 IMF 복잡도 측정에 사용 (고주파/저주파 분류 기준).
    r: 허용 오차 (std의 비율)
    """
    N = len(x)
    r_val = r * np.std(x)
    if r_val == 0:
        return 0.0

    def phi(m_):
        count = 0
        total = 0
        for i in range(N - m_):
            xi = x[i:i + m_]
            for j in range(N - m_):
                if i == j:
                    continue
                xj = x[j:j + m_]
                d = np.max(np.abs(xi - xj))
                # fuzzy membership
                count += np.exp(-(d ** 2) / r_val)
                total += 1
        return count / total if total > 0 else 0

    phi_m   = phi(m)
    phi_m1  = phi(m + 1)
    if phi_m == 0:
        return 0.0
    return -np.log(phi_m1 / phi_m)


def split_high_low(components: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """
    Fuzzy Entropy로 고주파/저주파 분류.
    논문: 엔트로피 오름차순 정렬 후 앞 절반 = 고주파, 뒤 절반 = 저주파.
    """
    entropies = [fuzzy_entropy(c) for c in components]
    sorted_idx = np.argsort(entropies)
    mid = len(components) // 2

    high_idx = sorted_idx[:mid]
    low_idx  = sorted_idx[mid:]

    high = np.sum([components[i] for i in high_idx], axis=0)
    low  = np.sum([components[i] for i in low_idx],  axis=0)
    return high, low


# ── LSTM 모델 ─────────────────────────────────────────────────────

class LSTMModel(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int = 1):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, batch_first=True)
        self.fc   = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :])


def make_sequences(target: np.ndarray,
                   features: np.ndarray,
                   timestep: int) -> tuple[np.ndarray, np.ndarray]:
    """
    논문 수식 (3): 멀티팩터 입력 행렬 X' 생성.
    target: (N,) 예측 대상 시계열
    features: (N, m) 외생 변수 행렬
    """
    X, y = [], []
    for i in range(len(target) - timestep):
        t_seq  = target[i:i + timestep].reshape(-1, 1)
        f_seq  = features[i:i + timestep]
        x_seq  = np.concatenate([f_seq, t_seq], axis=1)
        X.append(x_seq)
        y.append(target[i + timestep])
    return np.array(X), np.array(y)


def train_lstm(X_train: np.ndarray,
               y_train: np.ndarray,
               X_val: np.ndarray,
               y_val: np.ndarray,
               hidden_dim: int,
               batch_size: int,
               epochs: int = EPOCHS,
               lr: float = LR) -> tuple[LSTMModel, float]:
    """LSTM 학습 후 validation MSE 반환."""
    input_dim = X_train.shape[2]

    Xt = torch.FloatTensor(X_train).to(DEVICE)
    yt = torch.FloatTensor(y_train).unsqueeze(1).to(DEVICE)
    Xv = torch.FloatTensor(X_val).to(DEVICE)
    yv = torch.FloatTensor(y_val).unsqueeze(1).to(DEVICE)

    ds     = TensorDataset(Xt, yt)
    loader = DataLoader(ds, batch_size=batch_size, shuffle=True)

    model = LSTMModel(input_dim, hidden_dim).to(DEVICE)
    opt   = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    model.train()
    for _ in range(epochs):
        for xb, yb in loader:
            opt.zero_grad()
            loss_fn(model(xb), yb).backward()
            opt.step()

    model.eval()
    with torch.no_grad():
        val_loss = loss_fn(model(Xv), yv).item()

    return model, val_loss


# ── IPSO ──────────────────────────────────────────────────────────

def ipso_optimize(X_train, y_train, X_val, y_val,
                  max_iter: int = IPSO_MAX_ITER,
                  n_particles: int = IPSO_N_PARTICLES) -> tuple[int, int]:
    """
    IPSO로 LSTM hidden_dim, batch_size 최적화.
    논문 수식 (4): 역사인 함수 기반 비선형 관성 가중치.
    탐색 공간: units = p*32 (p=1~4), batch_size = q*12 (q=1~7)
    """
    # 파티클 초기화 [p, q]
    positions  = np.array([[np.random.choice(P_RANGE),
                             np.random.choice(Q_RANGE)]
                            for _ in range(n_particles)], dtype=float)
    velocities = np.zeros_like(positions)
    pbest_pos  = positions.copy()
    pbest_fit  = np.full(n_particles, np.inf)
    gbest_pos  = positions[0].copy()
    gbest_fit  = np.inf

    for t in range(1, max_iter + 1):
        # 논문 수식 (4): 비선형 관성 가중치
        w = W_MAX - (W_MAX - W_MIN) * np.arcsin(t / max_iter) * (2 / np.pi)

        for i in range(n_particles):
            p = int(round(np.clip(positions[i, 0], 1, 4)))
            q = int(round(np.clip(positions[i, 1], 1, 7)))
            units      = p * 32
            batch_size = q * 12

            _, mse = train_lstm(X_train, y_train, X_val, y_val,
                                hidden_dim=units, batch_size=batch_size,
                                epochs=20)  # 빠른 평가를 위해 epoch 축소

            if mse < pbest_fit[i]:
                pbest_fit[i]  = mse
                pbest_pos[i]  = positions[i].copy()
            if mse < gbest_fit:
                gbest_fit = mse
                gbest_pos = positions[i].copy()

        for i in range(n_particles):
            r1, r2 = np.random.rand(2)
            velocities[i] = (w * velocities[i]
                             + C1 * r1 * (pbest_pos[i] - positions[i])
                             + C2 * r2 * (gbest_pos  - positions[i]))
            positions[i] += velocities[i]

    p_best = int(round(np.clip(gbest_pos[0], 1, 4)))
    q_best = int(round(np.clip(gbest_pos[1], 1, 7)))
    return p_best * 32, q_best * 12


# ── 메인 파이프라인 ───────────────────────────────────────────────

def main():
    print(f"Device: {DEVICE}")
    mlflow.set_experiment("SSA-IPSO-LSTM")

    with mlflow.start_run(run_name="ssa_ipso_lstm_grid_P"):
        mlflow.log_params({
            "ssa_window": SSA_WINDOW,
            "timestep":   TIMESTEP,
            "epochs":     EPOCHS,
            "lr":         LR,
            "ipso_iter":  IPSO_MAX_ITER,
        })

        # ── 1. 데이터 로드 ──
        print("데이터 로드 중...")
        df_train = load_range(TRAIN_START, TRAIN_END)
        df_val   = load_range(VAL_START,   VAL_END)
        df_test  = load_range(TEST_START,  TEST_END)

        for df in [df_train, df_val, df_test]:
            df.dropna(subset=["grid_P"], inplace=True)
            build_time_features(df)
            df.fillna(0, inplace=True)

        # ── 2. 피처 정의 ──
        feat_cols = ["pv_P", "chp_P", "Ta", "Igm",
                     "hour_sin", "hour_cos", "dow_sin", "dow_cos",
                     "month_sin", "month_cos"]

        # ── 3. 정규화 ──
        scaler_y = MinMaxScaler()
        scaler_X = MinMaxScaler()

        y_train = scaler_y.fit_transform(df_train[["grid_P"]]).flatten()
        y_val   = scaler_y.transform(df_val[["grid_P"]]).flatten()
        y_test  = scaler_y.transform(df_test[["grid_P"]]).flatten()

        X_train_raw = scaler_X.fit_transform(df_train[feat_cols])
        X_val_raw   = scaler_X.transform(df_val[feat_cols])
        X_test_raw  = scaler_X.transform(df_test[feat_cols])

        # ── 4. SSA 분해 ──
        print("SSA 분해 중...")
        ssa = SSA(window_len=SSA_WINDOW)
        components_train = ssa.fit_transform(y_train)

        high_train, low_train = split_high_low(components_train)

        # validation/test: SSA 미적용, 전체 시계열 사용
        high_val, low_val   = y_val, y_val
        high_test, low_test = y_test, y_test

        print(f"  SSA 성분 수: {len(components_train)}")
        print(f"  고주파 성분 range: [{high_train.min():.4f}, {high_train.max():.4f}]")
        print(f"  저주파 성분 range: [{low_train.min():.4f}, {low_train.max():.4f}]")

        # ── 5. 시퀀스 생성 ──
        X_seq_h_train, y_seq_h_train = make_sequences(high_train, X_train_raw, TIMESTEP)
        X_seq_l_train, y_seq_l_train = make_sequences(low_train,  X_train_raw, TIMESTEP)
        X_seq_h_val,   y_seq_h_val   = make_sequences(high_val,   X_val_raw,   TIMESTEP)
        X_seq_l_val,   y_seq_l_val   = make_sequences(low_val,    X_val_raw,   TIMESTEP)
        X_seq_h_test,  y_seq_h_test  = make_sequences(high_test,  X_test_raw,  TIMESTEP)
        X_seq_l_test,  y_seq_l_test  = make_sequences(low_test,   X_test_raw,  TIMESTEP)

        # ── 6. IPSO 최적화 ──
        print("IPSO 파라미터 최적화 중 (고주파 모델)...")
        units_h, batch_h = ipso_optimize(X_seq_h_train, y_seq_h_train,
                                          X_seq_h_val,   y_seq_h_val)
        print(f"  고주파 최적 파라미터: units={units_h}, batch_size={batch_h}")

        print("IPSO 파라미터 최적화 중 (저주파 모델)...")
        units_l, batch_l = ipso_optimize(X_seq_l_train, y_seq_l_train,
                                          X_seq_l_val,   y_seq_l_val)
        print(f"  저주파 최적 파라미터: units={units_l}, batch_size={batch_l}")

        mlflow.log_params({
            "units_high": units_h, "batch_high": batch_h,
            "units_low":  units_l, "batch_low":  batch_l,
        })

        # ── 7. 최종 학습 ──
        print("최종 모델 학습 중...")
        model_h, _ = train_lstm(X_seq_h_train, y_seq_h_train,
                                 X_seq_h_val,   y_seq_h_val,
                                 hidden_dim=units_h, batch_size=batch_h,
                                 epochs=EPOCHS)
        model_l, _ = train_lstm(X_seq_l_train, y_seq_l_train,
                                 X_seq_l_val,   y_seq_l_val,
                                 hidden_dim=units_l, batch_size=batch_l,
                                 epochs=EPOCHS)

        # ── 8. 예측 및 역정규화 ──
        model_h.eval()
        model_l.eval()
        with torch.no_grad():
            Xt_h = torch.FloatTensor(X_seq_h_test).to(DEVICE)
            Xt_l = torch.FloatTensor(X_seq_l_test).to(DEVICE)
            pred_h = model_h(Xt_h).cpu().numpy().flatten()
            pred_l = model_l(Xt_l).cpu().numpy().flatten()

        # Y_hat = H_hat + L_hat (논문 최종 합산)
        pred_combined = pred_h + pred_l

        # 역정규화
        n = min(len(pred_combined), len(y_seq_h_test))
        pred_inv = scaler_y.inverse_transform(pred_combined[:n].reshape(-1, 1)).flatten()
        true_inv = scaler_y.inverse_transform(y_seq_h_test[:n].reshape(-1, 1)).flatten()

        # ── 9. 평가 ──
        mae  = mean_absolute_error(true_inv, pred_inv)
        rmse = math.sqrt(mean_squared_error(true_inv, pred_inv))
        mape = np.mean(np.abs((true_inv - pred_inv) / (true_inv + 1e-8))) * 100

        print(f"\n[Test 결과]")
        print(f"  MAE  : {mae:,.1f} W")
        print(f"  RMSE : {rmse:,.1f} W")
        print(f"  MAPE : {mape:.2f}%")

        mlflow.log_metrics({"MAE": mae, "RMSE": rmse, "MAPE": mape})

        # ── 10. 시각화 ──
        plt.figure(figsize=(14, 5))
        plt.plot(true_inv[:500], label="Actual", alpha=0.8)
        plt.plot(pred_inv[:500], label="Predicted", alpha=0.8)
        plt.title("SSA-IPSO-LSTM: Grid Power Prediction (Test, first 500 steps)")
        plt.xlabel("Time step (1h)")
        plt.ylabel("Power (W)")
        plt.legend()
        plt.tight_layout()
        plt.savefig("ssa_ipso_lstm_result.png", dpi=150)
        mlflow.log_artifact("ssa_ipso_lstm_result.png")
        print("시각화 저장 완료: ssa_ipso_lstm_result.png")

        # ── 11. 모델 저장 ──
        torch.save(model_h.state_dict(), "lstm_model_high.pt")
        torch.save(model_l.state_dict(), "lstm_model_low.pt")
        mlflow.log_artifact("lstm_model_high.pt")
        mlflow.log_artifact("lstm_model_low.pt")
        print("모델 저장 완료")

    print("\n완료.")


if __name__ == "__main__":
    main()