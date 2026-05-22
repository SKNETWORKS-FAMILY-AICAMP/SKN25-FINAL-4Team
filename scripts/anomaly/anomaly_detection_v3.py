#!/usr/bin/env python3
"""
EMS 이상탐지 스크립트 with Optuna
- 계량기: V.Z81, V.Z82, H2.Z35, H2.Z351, H2.Z36, H2.Z361
- 변수: P, U1, PF
- 모델: LSTM-AE (kurtosis) + IF (dip test) + IQR (3σ 고정)
- 튜닝: 변수별 50 trial, 같은 변수 계량기 6개 공유
- 출력: 계량기/변수 선택 드롭다운 HTML 대시보드
"""

import json
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
import os
from scipy.stats import kurtosis
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import torch
import torch.nn as nn
import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "dbname": os.getenv("DB_NAME")
}

METERS = ["V.Z81", "V.Z82", "H2.Z35", "H2.Z351", "H2.Z36", "H2.Z361"]
MEASUREMENTS = ["P", "U1", "PF"]
N_TRIALS = 50
# 튜닝용 대표 계량기 (변수별 1개로 튜닝)
TUNE_METER = "V.Z81"


# ─────────────────────────────────────────
# 1. 데이터 로드
# ─────────────────────────────────────────
def load_data(meter_urn: str, measurement: str) -> np.ndarray:
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
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").sort_index()
    df = df.dropna()
    return df["value"].values, df.index


# ─────────────────────────────────────────
# 2. LSTM Autoencoder
# ─────────────────────────────────────────
class LSTMAutoencoder(nn.Module):
    def __init__(self, hidden_size=32, num_layers=2):
        super().__init__()
        self.encoder = nn.LSTM(1, hidden_size, num_layers, batch_first=True)
        self.decoder = nn.LSTM(hidden_size, hidden_size, num_layers, batch_first=True)
        self.output_layer = nn.Linear(hidden_size, 1)

    def forward(self, x):
        _, (h, _) = self.encoder(x)
        dec_input = h[-1].unsqueeze(1).repeat(1, x.size(1), 1)
        dec_out, _ = self.decoder(dec_input)
        return self.output_layer(dec_out)


def compute_lstm_errors(series: np.ndarray, hidden_size: int, window_size: int,
                         epochs: int, lr: float) -> np.ndarray:
    scaler = StandardScaler()
    scaled = scaler.fit_transform(series.reshape(-1, 1)).flatten()
    X = np.array([scaled[i:i+window_size] for i in range(len(scaled) - window_size)])
    X_tensor = torch.FloatTensor(X).unsqueeze(-1)

    model = LSTMAutoencoder(hidden_size=hidden_size, num_layers=2)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()

    model.train()
    for _ in range(epochs):
        optimizer.zero_grad()
        loss = criterion(model(X_tensor), X_tensor)
        loss.backward()
        optimizer.step()

    model.eval()
    with torch.no_grad():
        recon = model(X_tensor).numpy().squeeze(-1)

    errors = np.zeros(len(series))
    counts = np.zeros(len(series))
    for i in range(len(X)):
        err = np.abs(X[i] - recon[i])
        errors[i:i+window_size] += err
        counts[i:i+window_size] += 1
    return errors / np.maximum(counts, 1)


def lstm_ae_flags(series: np.ndarray, params: dict) -> np.ndarray:
    errors = compute_lstm_errors(
        series,
        hidden_size=params["hidden_size"],
        window_size=params["window_size"],
        epochs=params["epochs"],
        lr=params["lr"]
    )
    mu, sigma = errors.mean(), errors.std()
    return (errors > mu + params["threshold_k"] * sigma).astype(int)


# ─────────────────────────────────────────
# 3. Isolation Forest
# ─────────────────────────────────────────
def compute_if_scores(series: np.ndarray, contamination: float) -> np.ndarray:
    scaler = StandardScaler()
    df = pd.DataFrame({"v": series})
    df["lag1"] = df["v"].shift(1)
    df["lag2"] = df["v"].shift(2)
    df["roll_mean"] = df["v"].rolling(24).mean()
    df["roll_std"] = df["v"].rolling(24).std()
    df = df.fillna(method="bfill").fillna(method="ffill")
    feat = scaler.fit_transform(df.values)
    clf = IsolationForest(contamination=contamination, random_state=42, n_jobs=-1)
    clf.fit(feat)
    return clf.score_samples(feat)  # anomaly score


def if_flags(series: np.ndarray, params: dict) -> np.ndarray:
    scores = compute_if_scores(series, params["contamination"])
    threshold = np.percentile(scores, params["contamination"] * 100)
    return (scores <= threshold).astype(int)


# ─────────────────────────────────────────
# 4. IQR (3σ 고정)
# ─────────────────────────────────────────
def iqr_flags(series: np.ndarray) -> np.ndarray:
    mu, sigma = series.mean(), series.std()
    return ((series < mu - 3 * sigma) | (series > mu + 3 * sigma)).astype(int)


# ─────────────────────────────────────────
# 5. Hartigan's dip test (bimodality)
# ─────────────────────────────────────────
def dip_test_score(scores: np.ndarray) -> float:
    """dip statistic: 클수록 bimodal (두 봉우리)"""
    try:
        from diptest import dipstat
        return float(dipstat(scores))
    except ImportError:
        # diptest 없으면 표준편차로 대체
        return float(scores.std())


# ─────────────────────────────────────────
# 6. Optuna 튜닝
# ─────────────────────────────────────────
def tune_lstm_ae(series: np.ndarray, n_trials: int) -> dict:
    def objective(trial):
        hidden_size = trial.suggest_categorical("hidden_size", [16, 32, 64, 128])
        window_size = trial.suggest_categorical("window_size", [12, 24, 48])
        epochs = trial.suggest_int("epochs", 20, 50)
        lr = trial.suggest_float("lr", 1e-4, 1e-2, log=True)
        threshold_k = trial.suggest_float("threshold_k", 2.0, 4.0)

        errors = compute_lstm_errors(series, hidden_size, window_size, epochs, lr)
        return float(kurtosis(errors))  # 최대화

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    return study.best_params


def tune_if(series: np.ndarray, n_trials: int) -> dict:
    def objective(trial):
        contamination = trial.suggest_float("contamination", 0.01, 0.1)
        scores = compute_if_scores(series, contamination)
        return dip_test_score(scores)  # 최대화

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    return study.best_params


# ─────────────────────────────────────────
# 7. 앙상블
# ─────────────────────────────────────────
def ensemble(l_flags, i_flags, q_flags):
    return l_flags + i_flags + q_flags  # 0~3


# ─────────────────────────────────────────
# 8. 전체 파이프라인
# ─────────────────────────────────────────
def run_all(n_trials: int = N_TRIALS):
    all_results = {}
    best_params = {}  # measurement별 최적 파라미터 저장

    # 변수별 튜닝 (대표 계량기 V.Z81 사용)
    for meas in MEASUREMENTS:
        print(f"\n[튜닝] {TUNE_METER} / {meas}")
        series, _ = load_data(TUNE_METER, meas)

        print(f"  LSTM-AE Optuna ({n_trials} trials)...")
        lstm_params = tune_lstm_ae(series, n_trials)
        print(f"  IF Optuna ({n_trials} trials)...")
        if_params = tune_if(series, n_trials)

        best_params[meas] = {"lstm": lstm_params, "if": if_params}
        print(f"  LSTM 최적: {lstm_params}")
        print(f"  IF 최적: {if_params}")

    # 18개 조합 전체 분석
    total = len(METERS) * len(MEASUREMENTS)
    done = 0
    for meter in METERS:
        for meas in MEASUREMENTS:
            done += 1
            print(f"[{done}/{total}] {meter} / {meas}")
            series, ts = load_data(meter, meas)

            params = best_params[meas]
            l_flags = lstm_ae_flags(series, params["lstm"])
            i_flags = if_flags(series, params["if"])
            q_flags = iqr_flags(series)
            score = ensemble(l_flags, i_flags, q_flags)

            all_results[f"{meter}__{meas}"] = {
                "ts": ts.astype(str).tolist(),
                "values": series.tolist(),
                "score": score.tolist(),
                "stats": {
                    "total": int(len(score)),
                    "caution": int((score == 1).sum()),
                    "warning": int((score == 2).sum()),
                    "danger": int((score == 3).sum()),
                },
                "params": params
            }

    return all_results


# ─────────────────────────────────────────
# 9. HTML 대시보드
# ─────────────────────────────────────────
def make_dashboard(all_results: dict, out_path: str):
    data_json = json.dumps(all_results)
    meters_json = json.dumps(METERS)
    meas_json = json.dumps(MEASUREMENTS)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>EMS 이상탐지 대시보드</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
  body {{ margin:0; background:#1e1e2e; color:#cdd6f4; font-family:sans-serif; padding:20px; }}
  h1 {{ color:#89b4fa; margin-bottom:16px; }}
  .controls {{ display:flex; gap:16px; margin-bottom:16px; align-items:flex-end; flex-wrap:wrap; }}
  .ctrl-group label {{ display:block; font-size:12px; color:#a6adc8; margin-bottom:4px; }}
  select {{ background:#313244; color:#cdd6f4; border:1px solid #45475a; padding:8px 12px;
            border-radius:6px; font-size:14px; cursor:pointer; min-width:140px; }}
  .stats {{ display:flex; gap:12px; margin-bottom:12px; flex-wrap:wrap; }}
  .stat-box {{ background:#313244; border-radius:8px; padding:10px 16px; min-width:110px; }}
  .stat-box span {{ display:block; font-size:11px; color:#a6adc8; margin-bottom:4px; }}
  .stat-box strong {{ font-size:22px; }}
  .caution {{ color:#f9e2af; }}
  .warning {{ color:#fab387; }}
  .danger {{ color:#f38ba8; }}
  .params {{ background:#313244; border-radius:8px; padding:10px 16px; margin-bottom:12px;
             font-size:12px; color:#a6adc8; }}
  #chart {{ background:#313244; border-radius:8px; }}
</style>
</head>
<body>
<h1>⚡ EMS 이상탐지 대시보드</h1>
<div class="controls">
  <div class="ctrl-group">
    <label>계량기</label>
    <select id="meterSelect" onchange="render()"></select>
  </div>
  <div class="ctrl-group">
    <label>변수</label>
    <select id="measSelect" onchange="render()"></select>
  </div>
</div>
<div class="stats" id="stats"></div>
<div class="params" id="params"></div>
<div id="chart"></div>

<script>
const ALL_DATA = {data_json};
const METERS = {meters_json};
const MEASUREMENTS = {meas_json};

// 드롭다운 초기화
METERS.forEach(m => {{
  const opt = document.createElement("option");
  opt.value = m; opt.text = m;
  document.getElementById("meterSelect").appendChild(opt);
}});
MEASUREMENTS.forEach(m => {{
  const opt = document.createElement("option");
  opt.value = m; opt.text = m;
  document.getElementById("measSelect").appendChild(opt);
}});

function render() {{
  const meter = document.getElementById("meterSelect").value;
  const meas = document.getElementById("measSelect").value;
  const key = meter + "__" + meas;
  const d = ALL_DATA[key];

  if (!d) {{
    document.getElementById("chart").innerHTML = "<p style='padding:20px;color:#f38ba8;'>데이터 없음</p>";
    return;
  }}

  const ts = d.ts;
  const vals = d.values;
  const score = d.score;
  const stats = d.stats;
  const params = d.params;

  // 통계 박스
  document.getElementById("stats").innerHTML = `
    <div class="stat-box"><span>전체</span><strong>${{stats.total.toLocaleString()}}</strong></div>
    <div class="stat-box"><span>주의 (1개)</span><strong class="caution">${{stats.caution.toLocaleString()}}</strong></div>
    <div class="stat-box"><span>경고 (2개)</span><strong class="warning">${{stats.warning.toLocaleString()}}</strong></div>
    <div class="stat-box"><span>위험 (3개)</span><strong class="danger">${{stats.danger.toLocaleString()}}</strong></div>
  `;

  // 파라미터 표시
  document.getElementById("params").innerHTML = `
    <strong>LSTM-AE:</strong> hidden=${{params.lstm.hidden_size}}, window=${{params.lstm.window_size}}, 
    epochs=${{params.lstm.epochs}}, lr=${{params.lstm.lr?.toFixed(5)}}, k=${{params.lstm.threshold_k?.toFixed(2)}} &nbsp;|&nbsp;
    <strong>IF:</strong> contamination=${{params.if.contamination?.toFixed(4)}} &nbsp;|&nbsp;
    <strong>IQR:</strong> 3σ 고정
  `;

  const idx1 = score.map((s,i) => s===1 ? i : -1).filter(i=>i>=0);
  const idx2 = score.map((s,i) => s===2 ? i : -1).filter(i=>i>=0);
  const idx3 = score.map((s,i) => s===3 ? i : -1).filter(i=>i>=0);

  const traces = [
    {{ x: ts, y: vals, mode: "lines", name: meas,
       line: {{color: "#89b4fa", width: 1}}, opacity: 0.8 }},
    {{ x: idx1.map(i=>ts[i]), y: idx1.map(i=>vals[i]),
       mode: "markers", name: "주의",
       marker: {{color: "#f9e2af", size: 5}} }},
    {{ x: idx2.map(i=>ts[i]), y: idx2.map(i=>vals[i]),
       mode: "markers", name: "경고",
       marker: {{color: "#fab387", size: 6}} }},
    {{ x: idx3.map(i=>ts[i]), y: idx3.map(i=>vals[i]),
       mode: "markers", name: "위험",
       marker: {{color: "#f38ba8", size: 8}} }}
  ];

  const layout = {{
    title: `${{meter}} — ${{meas}} 이상탐지 (2018~2023)`,
    paper_bgcolor: "#313244", plot_bgcolor: "#313244",
    font: {{color: "#cdd6f4"}},
    xaxis: {{gridcolor: "#45475a"}},
    yaxis: {{gridcolor: "#45475a", title: meas}},
    legend: {{orientation: "h", y: 1.08}},
    height: 550,
    hovermode: "x unified",
    margin: {{t: 60, b: 40, l: 60, r: 20}}
  }};

  Plotly.newPlot("chart", traces, layout, {{responsive: true}});
}}

render();
</script>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n저장 완료: {out_path}")


# ─────────────────────────────────────────
# 10. 메인
# ─────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="anomaly_dashboard.html")
    parser.add_argument("--trials", type=int, default=N_TRIALS)
    args = parser.parse_args()

    print("=== EMS 이상탐지 시작 ===")
    all_results = run_all(n_trials=args.trials)
    make_dashboard(all_results, args.out)
    print("=== 완료 ===")
