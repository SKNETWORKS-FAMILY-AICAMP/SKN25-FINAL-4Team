#!/usr/bin/env python3
"""
EMS 이상탐지 스크립트 with Optuna - FINAL
- 계량기: V.Z81, V.Z82, H2.Z35, H2.Z351, H2.Z36, H2.Z361
- 변수: P, U1, PF
- 모델: LSTM-AE (kurtosis) + IF (dip test) + IQR (3σ 고정)
- 튜닝: 변수별 50 trial, 같은 변수 계량기 6개 공유
- 저장: 모델(.pt/.joblib), 파라미터(JSON), 결과(CSV), HTML 대시보드
"""

import json
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
import os
import joblib
from pathlib import Path
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
TUNE_METER = "V.Z81"

OUT_DIR = Path("outputs/anomaly")
MODEL_DIR = OUT_DIR / "models"
PARAM_DIR = OUT_DIR / "params"
RESULT_DIR = OUT_DIR / "results"

for d in [MODEL_DIR, PARAM_DIR, RESULT_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────
# 1. 데이터 로드
# ─────────────────────────────────────────
def load_data(meter_urn: str, measurement: str):
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


def compute_lstm_errors(series, hidden_size, window_size, epochs, lr):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    scaler = StandardScaler()
    scaled = scaler.fit_transform(series.reshape(-1, 1)).flatten()
    X = np.array([scaled[i:i+window_size] for i in range(len(scaled) - window_size)])
    X_tensor = torch.FloatTensor(X).unsqueeze(-1).to(device)

    model = LSTMAutoencoder(hidden_size=hidden_size, num_layers=2).to(device)
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
        recon = model(X_tensor).cpu().numpy().squeeze(-1)

    errors = np.zeros(len(series))
    counts = np.zeros(len(series))
    for i in range(len(X)):
        err = np.abs(X[i] - recon[i])
        errors[i:i+window_size] += err
        counts[i:i+window_size] += 1

    return errors / np.maximum(counts, 1), model, scaler


def train_lstm_ae(series, params, save_path=None):
    errors, model, scaler = compute_lstm_errors(
        series,
        hidden_size=params["hidden_size"],
        window_size=params["window_size"],
        epochs=params["epochs"],
        lr=params["lr"]
    )
    if save_path:
        torch.save({
            "model_state": model.state_dict(),
            "params": params,
            "scaler_mean": scaler.mean_.tolist(),
            "scaler_scale": scaler.scale_.tolist()
        }, save_path)
    mu, sigma = errors.mean(), errors.std()
    return (errors > mu + params["threshold_k"] * sigma).astype(int), errors


# ─────────────────────────────────────────
# 3. Isolation Forest
# ─────────────────────────────────────────
def build_if_features(series):
    df = pd.DataFrame({"v": series})
    df["lag1"] = df["v"].shift(1)
    df["lag2"] = df["v"].shift(2)
    df["roll_mean"] = df["v"].rolling(24).mean()
    df["roll_std"] = df["v"].rolling(24).std()
    df = df.fillna(method="bfill").fillna(method="ffill")
    scaler = StandardScaler()
    return scaler.fit_transform(df.values), scaler


def train_if(series, params, save_path=None):
    feat, scaler = build_if_features(series)
    clf = IsolationForest(
        contamination=params["contamination"],
        random_state=42, n_jobs=-1
    )
    clf.fit(feat)
    if save_path:
        joblib.dump({"model": clf, "scaler": scaler, "params": params}, save_path)
    scores = clf.score_samples(feat)
    threshold = np.percentile(scores, params["contamination"] * 100)
    return (scores <= threshold).astype(int), scores


# ─────────────────────────────────────────
# 4. IQR 3σ (고정)
# ─────────────────────────────────────────
def run_iqr(series):
    anomaly = np.zeros(len(series), dtype=int)
    for h in range(24):
        idx = np.arange(h, len(series), 24)
        vals = series[idx]
        mu, sigma = vals.mean(), vals.std()
        anomaly[idx] = ((vals < mu - 3 * sigma) | (vals > mu + 3 * sigma)).astype(int)
    return anomaly


# ─────────────────────────────────────────
# 5. Hartigan's dip test
# ─────────────────────────────────────────
def dip_test_score(scores):
    try:
        from diptest import dipstat
        return float(dipstat(scores))
    except ImportError:
        return float(scores.std())


# ─────────────────────────────────────────
# 6. Optuna 튜닝
# ─────────────────────────────────────────
def tune_lstm_ae(series, n_trials):
    def objective(trial):
        hidden_size = trial.suggest_categorical("hidden_size", [16, 32, 64, 128])
        window_size = trial.suggest_categorical("window_size", [12, 24, 48])
        epochs = trial.suggest_int("epochs", 20, 50)
        lr = trial.suggest_float("lr", 1e-4, 1e-2, log=True)
        threshold_k = trial.suggest_float("threshold_k", 2.0, 4.0)
        errors, _, _ = compute_lstm_errors(series, hidden_size, window_size, epochs, lr)
        return float(kurtosis(errors))

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    return study.best_params


def tune_if(series, n_trials):
    def objective(trial):
        contamination = trial.suggest_float("contamination", 0.01, 0.1)
        feat, _ = build_if_features(series)
        clf = IsolationForest(contamination=contamination, random_state=42, n_jobs=-1)
        clf.fit(feat)
        scores = clf.score_samples(feat)
        return dip_test_score(scores)

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    return study.best_params


# ─────────────────────────────────────────
# 7. 전체 파이프라인
# ─────────────────────────────────────────
def run_all(n_trials=N_TRIALS):
    all_results = {}
    best_params = {}
    all_rows = []

    # 변수별 튜닝
    for meas in MEASUREMENTS:
        print(f"\n[튜닝] {TUNE_METER} / {meas}")
        series, _ = load_data(TUNE_METER, meas)

        print(f"  LSTM-AE Optuna ({n_trials} trials)...")
        lstm_params = tune_lstm_ae(series, n_trials)
        print(f"  IF Optuna ({n_trials} trials)...")
        if_params = tune_if(series, n_trials)

        best_params[meas] = {"lstm": lstm_params, "if": if_params}

        # 파라미터 저장
        param_path = PARAM_DIR / f"best_params_{meas}.json"
        with open(param_path, "w") as f:
            json.dump(best_params[meas], f, indent=2)
        print(f"  파라미터 저장: {param_path}")
        print(f"  LSTM: {lstm_params}")
        print(f"  IF: {if_params}")

    # 18개 조합 전체 분석
    total = len(METERS) * len(MEASUREMENTS)
    done = 0

    for meter in METERS:
        for meas in MEASUREMENTS:
            done += 1
            print(f"[{done}/{total}] {meter} / {meas}")
            series, ts = load_data(meter, meas)
            params = best_params[meas]

            # 모델 학습 + 저장
            meter_key = meter.replace(".", "_")
            lstm_model_path = MODEL_DIR / f"lstm_ae_{meter_key}_{meas}.pt"
            if_model_path = MODEL_DIR / f"if_{meter_key}_{meas}.joblib"

            l_flags, lstm_errors = train_lstm_ae(series, params["lstm"], save_path=lstm_model_path)
            i_flags, if_scores = train_if(series, params["if"], save_path=if_model_path)
            q_flags = run_iqr(series)
            score = l_flags + i_flags + q_flags

            # 이상점만 CSV rows에 추가
            for i, t in enumerate(ts):
                if score[i] > 0:
                    all_rows.append({
                        "ts": str(t),
                        "meter_urn": meter,
                        "measurement": meas,
                        "value": float(series[i]),
                        "lstm_flag": int(l_flags[i]),
                        "if_flag": int(i_flags[i]),
                        "iqr_flag": int(q_flags[i]),
                        "score": int(score[i]),
                        "label": ["", "주의", "경고", "위험"][int(score[i])]
                    })

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

    # 결과 CSV 저장
    csv_path = RESULT_DIR / "anomaly_results.csv"
    pd.DataFrame(all_rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n결과 CSV 저장: {csv_path} ({len(all_rows)}개 이상점)")

    return all_results


# ─────────────────────────────────────────
# 8. HTML 대시보드
# ─────────────────────────────────────────
def make_dashboard(all_results, out_path):
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
  if (!d) {{ document.getElementById("chart").innerHTML = "<p style='padding:20px;color:#f38ba8;'>데이터 없음</p>"; return; }}

  const ts = d.ts, vals = d.values, score = d.score, stats = d.stats, params = d.params;

  document.getElementById("stats").innerHTML = `
    <div class="stat-box"><span>전체</span><strong>${{stats.total.toLocaleString()}}</strong></div>
    <div class="stat-box"><span>주의 (1개)</span><strong class="caution">${{stats.caution.toLocaleString()}}</strong></div>
    <div class="stat-box"><span>경고 (2개)</span><strong class="warning">${{stats.warning.toLocaleString()}}</strong></div>
    <div class="stat-box"><span>위험 (3개)</span><strong class="danger">${{stats.danger.toLocaleString()}}</strong></div>
  `;

  document.getElementById("params").innerHTML = `
    <strong>LSTM-AE:</strong> hidden=${{params.lstm.hidden_size}}, window=${{params.lstm.window_size}},
    epochs=${{params.lstm.epochs}}, lr=${{params.lstm.lr?.toFixed(5)}}, k=${{params.lstm.threshold_k?.toFixed(2)}} &nbsp;|&nbsp;
    <strong>IF:</strong> contamination=${{params.if.contamination?.toFixed(4)}} &nbsp;|&nbsp;
    <strong>IQR:</strong> 3σ 고정
  `;

  const idx1 = score.map((s,i) => s===1?i:-1).filter(i=>i>=0);
  const idx2 = score.map((s,i) => s===2?i:-1).filter(i=>i>=0);
  const idx3 = score.map((s,i) => s===3?i:-1).filter(i=>i>=0);

  Plotly.newPlot("chart", [
    {{ x:ts, y:vals, mode:"lines", name:meas, line:{{color:"#89b4fa",width:1}}, opacity:0.8 }},
    {{ x:idx1.map(i=>ts[i]), y:idx1.map(i=>vals[i]), mode:"markers", name:"주의", marker:{{color:"#f9e2af",size:5}} }},
    {{ x:idx2.map(i=>ts[i]), y:idx2.map(i=>vals[i]), mode:"markers", name:"경고", marker:{{color:"#fab387",size:6}} }},
    {{ x:idx3.map(i=>ts[i]), y:idx3.map(i=>vals[i]), mode:"markers", name:"위험", marker:{{color:"#f38ba8",size:8}} }}
  ], {{
    title:`${{meter}} — ${{meas}} 이상탐지 (2018~2023)`,
    paper_bgcolor:"#313244", plot_bgcolor:"#313244",
    font:{{color:"#cdd6f4"}},
    xaxis:{{gridcolor:"#45475a"}},
    yaxis:{{gridcolor:"#45475a", title:meas}},
    legend:{{orientation:"h", y:1.08}},
    height:550, hovermode:"x unified",
    margin:{{t:60,b:40,l:60,r:20}}
  }}, {{responsive:true}});
}}

render();
</script>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML 저장: {out_path}")


# ─────────────────────────────────────────
# 9. 메인
# ─────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUT_DIR / "anomaly_dashboard.html"))
    parser.add_argument("--trials", type=int, default=N_TRIALS)
    args = parser.parse_args()

    print("=== EMS 이상탐지 시작 ===")
    all_results = run_all(n_trials=args.trials)
    make_dashboard(all_results, args.out)
    print("=== 완료 ===")
    print(f"\n저장 위치:")
    print(f"  모델:      {MODEL_DIR}/")
    print(f"  파라미터:  {PARAM_DIR}/")
    print(f"  결과 CSV:  {RESULT_DIR}/anomaly_results.csv")
    print(f"  HTML:      {args.out}")
