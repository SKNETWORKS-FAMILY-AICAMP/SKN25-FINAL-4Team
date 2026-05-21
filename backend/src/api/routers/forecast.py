"""예측 API — 학습 트리거 + 예측 결과 조회."""
import sys
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Query

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

router = APIRouter(prefix="/forecast", tags=["forecast"])

_STATUS_DIR = Path(__file__).parent.parent.parent / "models" / "forecasting" / "saved"
_PROCS: dict[str, "subprocess.Popen"] = {}


def _status_file(model_name: str) -> Path:
    (_STATUS_DIR / model_name).mkdir(parents=True, exist_ok=True)
    return _STATUS_DIR / model_name / "status.txt"


def _get_status(model_name: str) -> str:
    f = _status_file(model_name)
    if not f.exists():
        return "idle"
    proc = _PROCS.get(model_name)
    alive = proc and proc.poll() is None
    text = f.read_text().strip()
    if alive:
        # 프로세스 살아있음 → 파일의 epoch 진행률 그대로 반환
        return text if text.startswith("running") else "running"
    # 프로세스 없는데 "running"이면 컨테이너 재시작으로 인한 stale
    if text.startswith("running"):
        return "idle"
    return text


@router.post("/train/{model_name}")
async def train_model(
    model_name: str,
    start:   str = Query("2018-01-01"),
    end:     str = Query("2024-01-01"),
    horizon: int = Query(24, ge=1, le=168),
):
    """학습 시작 (별도 프로세스). model_name: prophet | xgboost | lstm | vmd-lstm"""
    import subprocess
    if model_name == "vmd-lstm":
        from models.forecasting.vmd_lstm_model import is_available
        return {"status": "pretrained", "model": "vmd-lstm",
                "message": "VMD-LSTM은 ML 팀 사전학습 모델을 사용합니다.",
                "available": is_available()}
    if model_name not in ("prophet", "xgboost", "lstm"):
        return {"error": f"지원하지 않는 모델: {model_name}"}
    proc = _PROCS.get(model_name)
    if proc and proc.poll() is None:
        return {"status": "already_running", "model": model_name}

    script = f"""
import sys
sys.path.insert(0, '{Path(__file__).parent.parent.parent}')
from pathlib import Path
status_file = Path('{_status_file(model_name)}')
try:
    from data.loader import load_range
    df = load_range('{start}', '{end}')
    if '{model_name}' == 'prophet':
        from models.forecasting.prophet_model import train
        train(df, run_cv=False)
    elif '{model_name}' == 'xgboost':
        from models.forecasting.xgboost_model import train
        train(df)
    elif '{model_name}' == 'lstm':
        from models.forecasting.lstm_model import train
        train(df, horizon={horizon}, status_file=status_file)
    status_file.write_text('done')
except Exception as e:
    import traceback
    status_file.write_text(f'error: {{e}}\\n{{traceback.format_exc()}}')
"""
    _status_file(model_name).write_text("running")
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).parent.parent.parent),
    )
    _PROCS[model_name] = proc
    return {"status": "started", "model": model_name, "data": f"{start} ~ {end}"}


@router.get("/train/status")
async def training_status():
    """학습 상태 조회."""
    from models.forecasting.vmd_lstm_model import is_available
    status = {m: _get_status(m) for m in ("prophet", "xgboost", "lstm")}
    status["vmd-lstm"] = "done" if is_available() else "idle"
    return {"status": status}


@router.get("/predict/{model_name}")
async def predict(
    model_name: str,
    hours: int = Query(24, ge=1, le=168),
    start: str = Query("2023-01-01"),
    end:   str = Query("2024-01-01"),
):
    """예측 실행. start/end는 컨텍스트 데이터 범위."""
    if model_name not in ("prophet", "xgboost", "lstm"):
        return {"error": f"지원하지 않는 모델: {model_name}"}
    try:
        from data.loader import load_range
        df = load_range(start, end)
        if model_name == "prophet":
            from models.forecasting.prophet_model import predict as _predict
            fc = _predict(df, hours=hours)
            records = fc.rename(columns={"ts": "timestamp"}).to_dict(orient="records")
            for r in records:
                if hasattr(r.get("timestamp"), "isoformat"):
                    r["timestamp"] = r["timestamp"].isoformat()
        elif model_name == "xgboost":
            from models.forecasting.xgboost_model import predict as _predict
            fc = _predict(df, hours=hours)
            records = [{"timestamp": str(r["ts"]), "yhat": r["yhat"]} for _, r in fc.iterrows()]
        elif model_name == "lstm":
            from models.forecasting.lstm_model import predict as _predict
            fc = _predict(df, horizon=hours)
            records = [{"timestamp": str(r["ts"]), "yhat": r["yhat"]} for _, r in fc.iterrows()]
        return {"model": model_name, "hours": hours, "forecast": records}
    except FileNotFoundError as e:
        return {"error": str(e), "hint": f"POST /forecast/train/{model_name} 먼저 실행하세요"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/compare")
async def compare_models(
    hours: int = Query(24, ge=1, le=168),
    start: str = Query("2023-01-01"),
    end:   str = Query("2024-01-01"),
):
    """네 모델 예측 결과 비교 (prophet / xgboost / lstm / vmd-lstm)."""
    import pandas as pd
    results = {}

    try:
        from data.loader import load_range
        df = load_range(start, end)
    except Exception as e:
        return {"error": f"데이터 로드 실패: {e}"}

    for name in ("prophet", "xgboost", "lstm", "vmd-lstm"):
        try:
            if name == "prophet":
                from models.forecasting.prophet_model import predict as _p
                fc = _p(df, hours=hours)
                results[name] = [{"ts": str(r["ts"]), "yhat": round(float(r["yhat"]), 2)} for _, r in fc.iterrows()]
            elif name == "xgboost":
                from models.forecasting.xgboost_model import predict as _p
                fc = _p(df, hours=hours)
                results[name] = [{"ts": str(r["ts"]), "yhat": r["yhat"]} for _, r in fc.iterrows()]
            elif name == "lstm":
                from models.forecasting.lstm_model import predict as _p
                fc = _p(df, horizon=hours)
                results[name] = [{"ts": str(r["ts"]), "yhat": r["yhat"]} for _, r in fc.iterrows()]
            elif name == "vmd-lstm":
                from models.forecasting.vmd_lstm_model import predict_future, is_available
                if not is_available():
                    results[name] = {"error": "VMD-LSTM 모델 파일 없음"}
                    continue
                fc = predict_future(df, hours=hours)
                results[name] = [{"ts": str(r["ts"])[:16], "yhat": float(r["predicted_kw"])} for _, r in fc.iterrows()]
        except Exception as e:
            results[name] = {"error": str(e)}
    return {"hours": hours, "models": results}


@router.get("/backtest")
async def backtest(
    train_end: str = Query("2020-12-31", description="학습 종료일 (이전 데이터로 학습)"),
    test_end:  str = Query("2023-12-31", description="검증 종료일"),
    freq:      str = Query("D", description="집계 단위: D=일별, W=주별, M=월별"),
):
    """
    train_end 이전으로 학습 → 이후 기간 예측 → 실제값과 비교.
    반환: [{ts, actual, prophet, xgboost}, ...]  (일/주/월 평균 kW)
    """
    import pickle
    import numpy as np
    import pandas as pd
    import xgboost as xgb
    from data.loader import load_range
    from models.forecasting.prophet_model import _prepare as prophet_prepare, MLFLOW_URI as P_URI
    from models.forecasting.xgboost_model import _build_features, FEATURE_COLS, TARGET

    # 전체 데이터를 한 번만 로드하고 메모리에서 분할
    df_all = load_range("2018-01-01", test_end + "T23:59:59")
    split_ts = pd.Timestamp(train_end + "T23:59:59")
    ts_col   = pd.to_datetime(df_all["ts"]).dt.tz_localize(None)
    df_train = df_all[ts_col <= split_ts].reset_index(drop=True)
    df_test  = df_all[ts_col >  split_ts].reset_index(drop=True)

    if df_train.empty or df_test.empty:
        return {"error": "데이터 없음"}

    results: dict[str, dict] = {}

    # ── 실제값 집계 ────────────────────────────────────────
    actual = df_test[["ts", "grid_P"]].copy()
    actual["ts"] = pd.to_datetime(actual["ts"]).dt.tz_localize(None)
    actual["grid_kw"] = actual["grid_P"] / 1000
    actual = actual.set_index("ts").resample(freq)["grid_kw"].mean().dropna().reset_index()
    actual["ts"] = actual["ts"].astype(str).str[:10]

    # ── Prophet 백테스트 ───────────────────────────────────
    try:
        import mlflow, warnings
        warnings.filterwarnings("ignore")
        from prophet import Prophet
        mlflow.set_tracking_uri(P_URI)

        d_tr = prophet_prepare(df_train)
        ta_mean = float(d_tr["Ta"].mean())
        ta_by_hour = d_tr.groupby(d_tr["ds"].dt.hour)["Ta"].mean()

        params = dict(changepoint_prior_scale=0.05, seasonality_prior_scale=10,
                      seasonality_mode="multiplicative")
        mlflow.set_experiment("backtest_prophet")
        with mlflow.start_run(run_name=f"bt_{train_end}"):
            m = Prophet(**params)
            m.add_regressor("Ta")
            m.add_seasonality("weekly", period=7,     fourier_order=5)
            m.add_seasonality("yearly", period=365.25, fourier_order=10)
            m.fit(d_tr)

        test_hours = int((pd.Timestamp(test_end) - pd.Timestamp(train_end)).total_seconds() / 3600)
        future = m.make_future_dataframe(periods=test_hours, freq="h", include_history=False)
        future["Ta"] = future["ds"].dt.hour.map(ta_by_hour).fillna(ta_mean)
        fc = m.predict(future)[["ds", "yhat"]]
        fc["yhat"] = fc["yhat"].clip(lower=0)
        fc = fc.set_index("ds").resample(freq)["yhat"].mean().dropna().reset_index()
        fc["ds"] = fc["ds"].astype(str).str[:10]
        results["prophet"] = {r["ds"]: round(r["yhat"], 1) for _, r in fc.iterrows()}
    except Exception as e:
        results["prophet"] = {"error": str(e)}

    # ── XGBoost 백테스트 (월별 롤링) ──────────────────────
    try:
        import mlflow
        d_feat = _build_features(df_all)   # 전체 피처 (lag 구성용)
        split_ts = pd.Timestamp(train_end)
        d_tr_feat = d_feat[d_feat["ts"] <= split_ts]
        d_te_feat = d_feat[d_feat["ts"] >  split_ts]

        X_tr = d_tr_feat[FEATURE_COLS]
        y_tr = d_tr_feat[TARGET]

        mlflow.set_tracking_uri(P_URI)
        mlflow.set_experiment("backtest_xgboost")
        with mlflow.start_run(run_name=f"bt_{train_end}"):
            xgb_model = xgb.XGBRegressor(n_estimators=300, learning_rate=0.05,
                                          max_depth=6, subsample=0.8, n_jobs=-1)
            xgb_model.fit(X_tr, y_tr, verbose=False)

        preds = xgb_model.predict(d_te_feat[FEATURE_COLS])
        xgb_fc = pd.DataFrame({"ts": d_te_feat["ts"].values, "yhat": preds})
        xgb_fc["ts"] = pd.to_datetime(xgb_fc["ts"]).dt.tz_localize(None)
        xgb_fc = xgb_fc.set_index("ts").resample(freq)["yhat"].mean().dropna().reset_index()
        xgb_fc["ts"] = xgb_fc["ts"].astype(str).str[:10]
        results["xgboost"] = {r["ts"]: round(float(r["yhat"]), 1) for _, r in xgb_fc.iterrows()}
    except Exception as e:
        results["xgboost"] = {"error": str(e)}

    # ── LSTM 백테스트 (저장된 모델 사용) ────────────────────
    try:
        import torch
        from models.forecasting.lstm_model import (
            LSTMForecaster, _prepare as lstm_prepare,
            FEATURE_COLS as LSTM_FEAT, WINDOW as LSTM_WIN,
        )
        LSTM_DIR = Path(__file__).parent.parent.parent / "models" / "forecasting" / "saved" / "lstm_forecast"
        with open(LSTM_DIR / "scaler.pkl", "rb") as f:
            lstm_scaler = pickle.load(f)
        with open(LSTM_DIR / "meta.pkl", "rb") as f:
            lstm_meta = pickle.load(f)

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        lstm_model = LSTMForecaster(
            len(LSTM_FEAT), lstm_meta["hidden_dim"],
            lstm_meta["num_layers"], lstm_meta["horizon"],
        ).to(device)
        lstm_model.load_state_dict(
            torch.load(LSTM_DIR / "best.pt", weights_only=True, map_location=device)
        )
        lstm_model.eval()

        d_lstm   = lstm_prepare(df_all)
        vals_all_lstm = lstm_scaler.transform(d_lstm[LSTM_FEAT].values)
        split_idx = d_lstm[d_lstm["ts"] >= pd.Timestamp(train_end)].index[0]
        step      = lstm_meta["horizon"]

        lstm_preds = []
        i = split_idx
        while i + step <= len(vals_all_lstm):
            if i < LSTM_WIN:
                i += step; continue
            w = vals_all_lstm[i - LSTM_WIN: i]
            x = torch.from_numpy(w.astype(np.float32)).unsqueeze(0).to(device)
            with torch.no_grad():
                out = lstm_model(x).cpu().numpy().flatten()
            dummy = np.zeros((len(out), len(LSTM_FEAT)))
            dummy[:, 0] = out
            yhat = lstm_scaler.inverse_transform(dummy)[:, 0]
            for j in range(step):
                if i + j < len(d_lstm):
                    lstm_preds.append({"ts": d_lstm.loc[i + j, "ts"], "yhat": float(yhat[j])})
            i += step

        lstm_fc = pd.DataFrame(lstm_preds)
        lstm_fc["ts"] = pd.to_datetime(lstm_fc["ts"]).dt.tz_localize(None)
        lstm_fc = lstm_fc.set_index("ts").resample(freq)["yhat"].mean().dropna().reset_index()
        lstm_fc["ts"] = lstm_fc["ts"].astype(str).str[:10]
        results["lstm"] = {r["ts"]: round(float(r["yhat"]), 1) for _, r in lstm_fc.iterrows()}
    except Exception as e:
        results["lstm"] = {"error": str(e)}

    # ── 결과 병합 ──────────────────────────────────────────
    merged = []
    for _, row in actual.iterrows():
        ts = row["ts"]
        pt = results.get("prophet")
        xt = results.get("xgboost")
        lt = results.get("lstm")
        merged.append({
            "ts":      ts,
            "actual":  round(float(row["grid_kw"]), 1),
            "prophet": pt.get(ts) if isinstance(pt, dict) and "error" not in pt else None,
            "xgboost": xt.get(ts) if isinstance(xt, dict) and "error" not in xt else None,
            "lstm":    lt.get(ts) if isinstance(lt, dict) and "error" not in lt else None,
        })

    errors = {k: v["error"] for k, v in results.items() if isinstance(v, dict) and "error" in v}
    mae = {}
    for model in ("prophet", "xgboost", "lstm"):
        pairs = [(r["actual"], r[model]) for r in merged if r.get(model) is not None]
        if pairs:
            mae[model] = round(float(np.mean([abs(a - p) for a, p in pairs])), 2)

    return {
        "train_period": f"2018-01-01 ~ {train_end}",
        "test_period":  f"{train_end} ~ {test_end}",
        "freq": freq,
        "mae_kw": mae,
        "data": merged,
        "errors": errors,
    }
