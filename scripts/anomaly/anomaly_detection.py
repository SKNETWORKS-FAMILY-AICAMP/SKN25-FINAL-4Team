#!/usr/bin/env python3
"""
EMS 이상탐지 스크립트
- 계량기: V.Z81, V.Z82, H2.Z35, H2.Z351, H2.Z36, H2.Z361
- 변수: P, U1, PF
- 모델: LSTM-AE + Isolation Forest + IQR 통계 앙상블
- 출력: HTML 시각화
"""

import argparse
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import psycopg2
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import torch
import torch.nn as nn
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# DB 설정
DB_CONFIG = {
    "host": "121.134.46.24",
    "port": 5432,
    "user": "team4",
    "password": "teamteam4",
    "dbname": "SKN25"
}

METERS = ["V.Z81", "V.Z82", "H2.Z35", "H2.Z351", "H2.Z36", "H2.Z361"]
MEASUREMENTS = ["P", "U1", "PF"]


# ─────────────────────────────────────────
# 1. 데이터 로드
# ─────────────────────────────────────────
def load_data(meter_urn: str, measurement: str) -> pd.DataFrame:
    query = """
        SELECT ts, value
        FROM ems.cr_measurement_1h
        WHERE meter_urn = %s
          AND measurement = %s
        ORDER BY ts
    """
    conn = psycopg2.connect(**DB_CONFIG)
    df = pd.read_sql(query, conn, params=(meter_urn, measurement))
    conn.close()
    df = df.rename(columns={"value": measurement})
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").sort_index()
    # 결측치 선형 보간 (최대 6시간)
    df = df.interpolate(method="time", limit=6)
    df = df.dropna()
    return df


# ─────────────────────────────────────────
# 2. LSTM Autoencoder
# ─────────────────────────────────────────
class LSTMAutoencoder(nn.Module):
    def __init__(self, input_size=1, hidden_size=32, num_layers=2):
        super().__init__()
        self.encoder = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.decoder = nn.LSTM(hidden_size, hidden_size, num_layers, batch_first=True)
        self.output_layer = nn.Linear(hidden_size, input_size)

    def forward(self, x):
        _, (h, c) = self.encoder(x)
        # decoder input: repeat hidden state
        seq_len = x.size(1)
        dec_input = h[-1].unsqueeze(1).repeat(1, seq_len, 1)
        dec_out, _ = self.decoder(dec_input)
        return self.output_layer(dec_out)


def run_lstm_ae(series: np.ndarray, window_size: int = 24, epochs: int = 30) -> np.ndarray:
    """LSTM-AE 재구성 오차 반환"""
    scaler = StandardScaler()
    scaled = scaler.fit_transform(series.reshape(-1, 1)).flatten()

    # 윈도우 생성
    X = np.array([scaled[i:i+window_size] for i in range(len(scaled) - window_size)])
    X_tensor = torch.FloatTensor(X).unsqueeze(-1)  # (N, seq, 1)

    model = LSTMAutoencoder(input_size=1, hidden_size=32, num_layers=2)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.MSELoss()

    model.train()
    for _ in range(epochs):
        optimizer.zero_grad()
        out = model(X_tensor)
        loss = criterion(out, X_tensor)
        loss.backward()
        optimizer.step()

    model.eval()
    with torch.no_grad():
        recon = model(X_tensor).numpy().squeeze(-1)

    # 재구성 오차 (점별)
    errors = np.zeros(len(series))
    counts = np.zeros(len(series))
    for i in range(len(X)):
        err = np.abs(X[i] - recon[i])
        errors[i:i+window_size] += err
        counts[i:i+window_size] += 1
    counts = np.maximum(counts, 1)
    errors = errors / counts

    return errors


# ─────────────────────────────────────────
# 3. Isolation Forest
# ─────────────────────────────────────────
def run_isolation_forest(series: np.ndarray, contamination: float = 0.05) -> np.ndarray:
    """IF 이상 여부 반환 (1: 이상, 0: 정상)"""
    scaler = StandardScaler()
    X = scaler.fit_transform(series.reshape(-1, 1))
    # 시계열 컨텍스트: 현재값 + lag1 + lag2
    df = pd.DataFrame({"v": series})
    df["lag1"] = df["v"].shift(1)
    df["lag2"] = df["v"].shift(2)
    df["roll_mean"] = df["v"].rolling(24).mean()
    df["roll_std"] = df["v"].rolling(24).std()
    df = df.fillna(method="bfill").fillna(method="ffill")

    feat = scaler.fit_transform(df.values)
    clf = IsolationForest(contamination=contamination, random_state=42, n_jobs=-1)
    pred = clf.fit_predict(feat)
    return (pred == -1).astype(int)


# ─────────────────────────────────────────
# 4. IQR 통계
# ─────────────────────────────────────────
def run_iqr(series: np.ndarray, k: float = 3.0) -> np.ndarray:
    """IQR 기반 이상 여부 반환 (1: 이상, 0: 정상)"""
    # 시간대별(hour) IQR로 계절성 고려
    s = pd.Series(series)
    anomaly = np.zeros(len(series), dtype=int)

    for h in range(24):
        idx = np.arange(h, len(series), 24)
        vals = series[idx]
        q1, q3 = np.percentile(vals, 25), np.percentile(vals, 75)
        iqr = q3 - q1
        lower = q1 - k * iqr
        upper = q3 + k * iqr
        anomaly[idx] = ((vals < lower) | (vals > upper)).astype(int)

    return anomaly


# ─────────────────────────────────────────
# 5. 앙상블
# ─────────────────────────────────────────
def ensemble(lstm_errors: np.ndarray, if_flags: np.ndarray, iqr_flags: np.ndarray,
             lstm_threshold_k: float = 3.0) -> np.ndarray:
    """
    3개 모델 결과 앙상블
    반환: 0=정상, 1=주의, 2=경고, 3=위험
    """
    # LSTM-AE: 오차가 mean + k*std 초과 시 이상
    mu, sigma = lstm_errors.mean(), lstm_errors.std()
    lstm_flags = (lstm_errors > mu + lstm_threshold_k * sigma).astype(int)

    score = lstm_flags + if_flags + iqr_flags
    return score  # 0~3


# ─────────────────────────────────────────
# 6. HTML 시각화
# ─────────────────────────────────────────
def make_html(df: pd.DataFrame, series: np.ndarray, score: np.ndarray,
              meter_urn: str, measurement: str, out_path: str):
    ts = df.index

    label_map = {0: "정상", 1: "주의", 2: "경고", 3: "위험"}
    color_map = {1: "yellow", 2: "orange", 3: "red"}

    fig = go.Figure()

    # 시계열 전체
    fig.add_trace(go.Scatter(
        x=ts, y=series,
        mode="lines",
        name=measurement,
        line=dict(color="#4a90d9", width=1),
        opacity=0.8
    ))

    # 이상점 레이어별
    for level, color in color_map.items():
        mask = score == level
        if mask.any():
            fig.add_trace(go.Scatter(
                x=ts[mask], y=series[mask],
                mode="markers",
                name=label_map[level],
                marker=dict(color=color, size=6, symbol="circle"),
            ))

    fig.update_layout(
        title=f"{meter_urn} — {measurement} 이상탐지 (2018~2023)",
        xaxis_title="시간",
        yaxis_title=measurement,
        template="plotly_dark",
        legend=dict(orientation="h", y=1.05),
        height=600,
        hovermode="x unified"
    )

    # 통계 요약
    n_total = len(score)
    n_caution = (score == 1).sum()
    n_warning = (score == 2).sum()
    n_danger = (score == 3).sum()

    summary_html = f"""
    <div style="font-family:sans-serif; padding:16px; background:#1e1e2e; color:#cdd6f4; border-radius:8px; margin-bottom:12px;">
        <h2 style="margin:0 0 8px 0">{meter_urn} · {measurement}</h2>
        <p style="margin:4px 0">전체: {n_total:,}개 | 
           <span style="color:yellow">주의: {n_caution:,}</span> | 
           <span style="color:orange">경고: {n_warning:,}</span> | 
           <span style="color:red">위험: {n_danger:,}</span>
        </p>
    </div>
    """

    html = fig.to_html(full_html=True, include_plotlyjs="cdn")
    # summary 삽입
    html = html.replace("<body>", f"<body>{summary_html}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"저장 완료: {out_path}")


# ─────────────────────────────────────────
# 7. 메인
# ─────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--meter", default="V.Z81", choices=METERS)
    parser.add_argument("--measurement", default="P", choices=MEASUREMENTS)
    parser.add_argument("--out", default="anomaly_result.html")
    parser.add_argument("--epochs", type=int, default=30)
    args = parser.parse_args()

    print(f"[1/5] 데이터 로드: {args.meter} / {args.measurement}")
    df = load_data(args.meter, args.measurement)
    series = df[args.measurement].values
    print(f"      {len(series):,}개 행 로드 완료")

    print("[2/5] LSTM-AE 실행 중...")
    lstm_errors = run_lstm_ae(series, window_size=24, epochs=args.epochs)

    print("[3/5] Isolation Forest 실행 중...")
    if_flags = run_isolation_forest(series)

    print("[4/5] IQR 통계 실행 중...")
    iqr_flags = run_iqr(series)

    print("[5/5] 앙상블 + 시각화 생성 중...")
    score = ensemble(lstm_errors, if_flags, iqr_flags)
    make_html(df, series, score, args.meter, args.measurement, args.out)

    print("완료.")


if __name__ == "__main__":
    main()
