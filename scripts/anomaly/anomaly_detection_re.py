#!/usr/bin/env python3
"""
EMS 이상탐지 스크립트 with Optuna - RE
- 계량기: V.Z81, V.Z82, H2.Z35+Z351(병합), H2.Z36+Z361(병합) → 4개
- 변수: P, U1, PF
- 모델: LSTM-AE (kurtosis) + IF (dip test) + Bollinger Bands (3σ, window Optuna 튜닝)
- 튜닝: 변수별 50 trial, 같은 변수 계량기 4개 공유
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
from torch.utils.data import DataLoader, TensorDataset
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

# 병합 계량기: (대표명, [쿼리할 URN 목록 - 시간순])
METER_GROUPS = {
    "V.Z81":      ["V.Z81"],
    "V.Z82":      ["V.Z82"],
    "H2.Z35_351": ["H2.Z35", "H2.Z351"],
    "H2.Z36_361": ["H2.Z36", "H2.Z361"],
}
METER_KEYS = list(METER_GROUPS.keys())
MEASUREMENTS = ["P", "U1", "PF"]
N_TRIALS = 50
TUNE_METER = "V.Z81"
BATCH_SIZE = 512

OUT_DIR = Path("outputs/anomaly")
MODEL_DIR = OUT_DIR / "models_re"
PARAM_DIR = OUT_DIR / "params_re"
RESULT_DIR = OUT_DIR / "results_re"

for d in [MODEL_DIR, PARAM_DIR, RESULT_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────
# 1. 데이터 로드 (병합 지원)
# ─────────────────────────────────────────
def load_data(meter_key: str, measurement: str):
    urns = METER_GROUPS[meter_key]
    dfs = []
    for urn in urns:
        query = """
            SELECT ts, value
            FROM ems.cr_measurement_1h
            WHERE meter_urn = %s
              AND measurement = %s
            ORDER BY ts
        """
        conn = psycopg2.connect(**DB_CONFIG)
        df = pd.read_sql(query, conn, params=(urn, measurement))
        conn.close()
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df = df.set_index("ts").sort_index()
        df = df.dropna()
        dfs.append(df)

    if len(dfs) == 1:
        merged = dfs[0]
    else:
        # 병합: 첫 번째 URN 마지막 시각 이후로 두 번째 URN 이어 붙이기
        cutoff = dfs[0].index[-1]
        second = dfs[1][dfs[1].index > cutoff]
        merged = pd.concat([dfs[0], second]).sort_index()

    return merged["value"].values, merged.index


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


def compute_lstm_errors(series, hidden_size, window_size, epochs, lr, batch_size=BATCH_SIZE):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    scaler = StandardScaler()
    scaled = scaler.fit_transform(series.reshape(-1, 1)).flatten()
    X = np.array([scaled[i:i+window_size] for i in range(len(scaled) - window_size)])
    X_tensor = torch.FloatTensor(X).unsqueeze(-1)

    dataset = TensorDataset(X_tensor)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    model = LSTMAutoencoder(hidden_size=hidden_size, num_layers=2).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()

    model.train()
    for _ in range(epochs):
        for (batch,) in loader:
            batch = batch.to(device)
            optimizer.zero_grad()
            loss = criterion(model(batch), batch)
            loss.backward()
            optimizer.step()
            del batch

    model.eval()
    recon_list = []
    infer_loader = DataLoader(TensorDataset(X_tensor), batch_size=batch_size, shuffle=False)
    with torch.no_grad():
        for (batch,) in infer_loader:
            batch = batch.to(device)
            out = model(batch).cpu().numpy().squeeze(-1)
            recon_list.append(out)
            del batch
    recon = np.concatenate(recon_list, axis=0)

    errors = np.zeros(len(series))
    counts = np.zeros(len(series))
    for i in range(len(X)):
        err = np.abs(X[i] - recon[i])
        errors[i:i+window_size] += err
        counts[i:i+window_size] += 1

    if torch.cuda.is_available():
        torch.cuda.empty_cache()

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
    df = df.bfill().ffill()
    scaler = StandardScaler()
    return scaler.fit_transform(df.values), scaler


def train_if(series, params, save_path=None):
    feat, scaler = build_if_features(series)
    clf = IsolationForest(
        contamination=params["contamination"],
        n_estimators=params["n_estimators"],
        max_samples=params["max_samples"],
        random_state=42, n_jobs=-1
    )
    clf.fit(feat)
    if save_path:
        joblib.dump({"model": clf, "scaler": scaler, "params": params}, save_path)
    scores = clf.score_samples(feat)
    threshold = np.percentile(scores, params["contamination"] * 100)
    return (scores <= threshold).astype(int), scores


# ─────────────────────────────────────────
# 4. Bollinger Bands (rolling 평균 ± 3σ)
# ─────────────────────────────────────────
def run_bollinger(series, window_size):
    s = pd.Series(series)
    roll_mean = s.rolling(window=window_size, min_periods=1).mean()
    roll_std = s.rolling(window=window_size, min_periods=1).std().fillna(0)
    upper = roll_mean + 3 * roll_std
    lower = roll_mean - 3 * roll_std
    return ((s > upper) | (s < lower)).astype(int).values


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
        n_estimators = trial.suggest_int("n_estimators", 50, 300)
        max_samples = trial.suggest_categorical("max_samples", [64, 128, 256, 512])
        feat, _ = build_if_features(series)
        clf = IsolationForest(
            contamination=contamination,
            n_estimators=n_estimators,
            max_samples=max_samples,
            random_state=42, n_jobs=-1
        )
        clf.fit(feat)
        scores = clf.score_samples(feat)
        return dip_test_score(scores)

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    return study.best_params


def tune_bollinger(series, n_trials):
    def objective(trial):
        window_size = trial.suggest_int("window_size", 24, 720)
        s = pd.Series(series)
        roll_mean = s.rolling(window=window_size, min_periods=1).mean()
        roll_std = s.rolling(window=window_size, min_periods=1).std().fillna(0)
        upper = roll_mean + 3 * roll_std
        lower = roll_mean - 3 * roll_std
        flags = ((s > upper) | (s < lower)).astype(int).values
        # 이상 비율이 1~5% 범위에 들도록
        rate = flags.mean()
        return -abs(rate - 0.02)  # 2% 목표, 최대화 방향

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

    for meas in MEASUREMENTS:
        print(f"\n[튜닝] {TUNE_METER} / {meas}")
        series, _ = load_data(TUNE_METER, meas)

        print(f"  LSTM-AE Optuna ({n_trials} trials)...")
        lstm_params = tune_lstm_ae(series, n_trials)
        print(f"  IF Optuna ({n_trials} trials)...")
        if_params = tune_if(series, n_trials)
        print(f"  Bollinger Optuna ({n_trials} trials)...")
        bb_params = tune_bollinger(series, n_trials)

        best_params[meas] = {"lstm": lstm_params, "if": if_params, "bollinger": bb_params}

        param_path = PARAM_DIR / f"best_params_{meas}.json"
        with open(param_path, "w") as f:
            json.dump(best_params[meas], f, indent=2)
        print(f"  파라미터 저장: {param_path}")
        print(f"  LSTM: {lstm_params}")
        print(f"  IF: {if_params}")
        print(f"  Bollinger: {bb_params}")

    total = len(METER_KEYS) * len(MEASUREMENTS)
    done = 0

    for meter_key in METER_KEYS:
        for meas in MEASUREMENTS:
            done += 1
            print(f"[{done}/{total}] {meter_key} / {meas}")
            series, ts = load_data(meter_key, meas)
            params = best_params[meas]

            meter_key_safe = meter_key.replace(".", "_")
            lstm_model_path = MODEL_DIR / f"lstm_ae_{meter_key_safe}_{meas}.pt"
            if_model_path = MODEL_DIR / f"if_{meter_key_safe}_{meas}.joblib"

            l_flags, lstm_errors = train_lstm_ae(series, params["lstm"], save_path=lstm_model_path)
            i_flags, if_scores = train_if(series, params["if"], save_path=if_model_path)
            b_flags = run_bollinger(series, params["bollinger"]["window_size"])
            score = l_flags + i_flags + b_flags

            for i, t in enumerate(ts):
                if score[i] > 0:
                    all_rows.append({
                        "ts": str(t),
                        "meter_urn": meter_key,
                        "measurement": meas,
                        "value": float(series[i]),
                        "lstm_flag": int(l_flags[i]),
                        "if_flag": int(i_flags[i]),
                        "bollinger_flag": int(b_flags[i]),
                        "score": int(score[i]),
                        "label": ["", "주의", "경고", "위험"][int(score[i])]
                    })

            all_results[f"{meter_key}__{meas}"] = {
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

    csv_path = RESULT_DIR / "anomaly_results.csv"
    pd.DataFrame(all_rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n결과 CSV 저장: {csv_path} ({len(all_rows)}개 이상점)")

    return all_results


# ─────────────────────────────────────────
# 8. HTML 대시보드
# ─────────────────────────────────────────
def make_dashboard(all_results, out_path):
    data_json = json.dumps(all_results)
    meters_json = json.dumps(METER_KEYS)
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
            border-radius:6px; font-size:14px; cursor:pointer; min-width:160px; }}
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
    <strong>IF:</strong> contamination=${{params.if.contamination?.toFixed(4)}}, n_est=${{params.if.n_estimators}}, max_s=${{params.if.max_samples}} &nbsp;|&nbsp;
    <strong>Bollinger:</strong> window=${{params.bollinger.window_size}}h
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
    parser.add_argument("--out", default=str(OUT_DIR / "anomaly_dashboard_re.html"))
    parser.add_argument("--trials", type=int, default=N_TRIALS)
    args = parser.parse_args()

    print("=== EMS 이상탐지 RE 시작 ===")
    all_results = run_all(n_trials=args.trials)
    make_dashboard(all_results, args.out)
    print("=== 완료 ===")
    print(f"\n저장 위치:")
    print(f"  모델:      {MODEL_DIR}/")
    print(f"  파라미터:  {PARAM_DIR}/")
    print(f"  결과 CSV:  {RESULT_DIR}/anomaly_results.csv")
    print(f"  HTML:      {args.out}")
