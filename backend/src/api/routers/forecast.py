"""예측 API — 학습 트리거 + 예측 결과 조회."""
import sys
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Query

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

router = APIRouter(prefix="/forecast", tags=["forecast"])

_STATUS_DIR = Path(__file__).parent.parent.parent / "models" / "forecasting" / "saved"
_PROCS: dict[str, "subprocess.Popen"] = {}

# ── 모델 레지스트리 ──────────────────────────────────────────────────
# 모델 추가/변경 시 이 dict만 수정하면 /forecast/models API와 프론트엔드에 자동 반영됨.
MODEL_REGISTRY: dict[str, dict] = {
    "vmd-lstm": {
        "name":        "vmd-lstm",
        "label":       "VMD-LSTM",
        "color":       "#a371f7",
        "description": "VMD 분해 + LSTM + Attention — 주력 모델",
        "trainable":   False,
        "max_hours":   None,
        "badges": [
            {"text": "주력 모델",     "color": "#a371f7", "bg": "#1e1034"},
            {"text": "이상탐지 연동", "color": "#6e7681", "bg": "#161b22"},
        ],
    },
    "xgboost": {
        "name":        "xgboost",
        "label":       "XGBoost",
        "color":       "#3fb950",
        "description": "Lag 피처 + 시간/날씨 — 폴백·백테스트",
        "trainable":   True,
        "max_hours":   None,
        "badges": [
            {"text": "MAPE 4.7%",   "color": "#3fb950", "bg": "#0f2d1a"},
            {"text": "폴백 모델",    "color": "#6e7681", "bg": "#161b22"},
        ],
    },
}


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


@router.get("/models")
def get_models():
    """등록된 예측 모델 목록 및 메타데이터 반환. 모델 추가/변경 시 MODEL_REGISTRY만 수정하면 됨."""
    from models.forecasting.vmd_lstm_model import is_available as vmd_available
    result = []
    for meta in MODEL_REGISTRY.values():
        entry = dict(meta)
        if meta["trainable"]:
            entry["status"] = _get_status(meta["name"])
        else:
            avail = vmd_available() if meta["name"] == "vmd-lstm" else False
            entry["status"] = "done" if avail else "idle"
            entry["available"] = avail
        result.append(entry)
    return {"models": result}


@router.post("/train/{model_name}")
def train_model(
    model_name: str,
    start:   str = Query("2018-01-01"),
    end:     str = Query("2024-01-01"),
    horizon: int = Query(24, ge=1, le=168),
):
    """학습 시작 (별도 프로세스)."""
    import subprocess
    if model_name not in MODEL_REGISTRY:
        return {"error": f"지원하지 않는 모델: {model_name}"}
    if not MODEL_REGISTRY[model_name]["trainable"]:
        if model_name == "vmd-lstm":
            from models.forecasting.vmd_lstm_model import is_available
            return {"status": "pretrained", "model": "vmd-lstm",
                    "message": "VMD-LSTM은 ML 팀 사전학습 모델을 사용합니다.",
                    "available": is_available()}
        return {"error": f"{model_name}은 사전학습 모델입니다."}
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
    if '{model_name}' == 'xgboost':
        from models.forecasting.xgboost_model import train
        train(df)
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
def training_status():
    """학습 상태 조회."""
    from models.forecasting.vmd_lstm_model import is_available
    status = {n: _get_status(n) for n, m in MODEL_REGISTRY.items() if m["trainable"]}
    for n, m in MODEL_REGISTRY.items():
        if not m["trainable"] and n == "vmd-lstm":
            status[n] = "done" if is_available() else "idle"
    return {"status": status}


@router.get("/predict/{model_name}")
def predict(
    model_name: str,
    hours: int = Query(24, ge=1, le=168),
    start: str = Query("2023-01-01"),
    end:   str = Query("2024-01-01"),
):
    """예측 실행. start/end는 컨텍스트 데이터 범위."""
    trainable = {n for n, m in MODEL_REGISTRY.items() if m["trainable"]}
    if model_name not in trainable:
        return {"error": f"지원하지 않는 모델: {model_name}"}
    try:
        from data.loader import load_range
        df = load_range(start, end)
        if model_name == "xgboost":
            from models.forecasting.xgboost_model import predict as _predict
            fc = _predict(df, hours=hours)
            records = [{"timestamp": str(r["ts"]), "yhat": r["yhat"]} for _, r in fc.iterrows()]
        return {"model": model_name, "hours": hours, "forecast": records}
    except FileNotFoundError as e:
        return {"error": str(e), "hint": f"POST /forecast/train/{model_name} 먼저 실행하세요"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/compare")
def compare_models(
    hours: int = Query(24, ge=1, le=168),
    start: str = Query(None, description="컨텍스트 시작 (없으면 sim_now-90일)"),
    end:   str = Query(None, description="컨텍스트 끝 (없으면 sim_now)"),
):
    """두 모델 예측 결과 비교 (vmd-lstm 주력 / xgboost 폴백). sim 활성 시 자동 cutoff."""
    import pandas as pd
    results = {}

    # 시뮬레이터 활성이면 sim_now를 컨텍스트 끝으로 사용 (data leakage 방지)
    if start is None or end is None:
        try:
            from api.routers.simulator import effective_now
            now_dt = effective_now()
            if end is None:
                end = now_dt.strftime("%Y-%m-%d")
            if start is None:
                start = (now_dt - __import__("datetime").timedelta(days=90)).strftime("%Y-%m-%d")
        except Exception:
            start = start or "2023-01-01"
            end   = end   or "2024-01-01"

    try:
        from data.loader import load_range
        df = load_range(start, end)
    except Exception as e:
        return {"error": f"데이터 로드 실패: {e}"}

    for name in ("vmd-lstm", "xgboost"):
        try:
            if name == "vmd-lstm":
                from models.forecasting.vmd_lstm_model import predict_future, is_available
                if not is_available():
                    results[name] = {"error": "VMD-LSTM 모델 파일 없음"}
                    continue
                fc = predict_future(df, hours=hours)
                results[name] = [{"ts": str(r["ts"])[:16], "yhat": float(r["predicted_kw"])} for _, r in fc.iterrows()]
            elif name == "xgboost":
                from models.forecasting.xgboost_model import predict as _p
                fc = _p(df, hours=hours)
                results[name] = [{"ts": str(r["ts"]), "yhat": r["yhat"]} for _, r in fc.iterrows()]
        except Exception as e:
            results[name] = {"error": str(e)}
    return {"hours": hours, "models": results}


@router.get("/backtest")
def backtest(
    train_end: str = Query("2021-12-31", description="학습 종료일 (4년 학습: 2018~2021)"),
    test_end:  str = Query("2022-12-31", description="검증 종료일 (1년 검증: 2022)"),
    freq:      str = Query("D", description="집계 단위: D=일별, W=주별, M=월별"),
):
    """
    train_end 이전으로 XGBoost 학습 → 이후 기간 예측 → 실제값과 비교.
    반환: [{ts, actual, xgboost}, ...]  (일/주/월 평균 kW)
    """
    import numpy as np
    import pandas as pd
    import xgboost as xgb
    from data.loader import load_range
    from models.forecasting.xgboost_model import _build_features, FEATURE_COLS, TARGET

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

    # ── XGBoost 백테스트 ───────────────────────────────────
    try:
        d_feat    = _build_features(df_all)
        split_ts2 = pd.Timestamp(train_end)
        d_tr_feat = d_feat[d_feat["ts"] <= split_ts2]
        d_te_feat = d_feat[d_feat["ts"] >  split_ts2]

        xgb_model = xgb.XGBRegressor(n_estimators=300, learning_rate=0.05,
                                      max_depth=6, subsample=0.8, n_jobs=-1)
        xgb_model.fit(d_tr_feat[FEATURE_COLS], d_tr_feat[TARGET], verbose=False)

        preds  = xgb_model.predict(d_te_feat[FEATURE_COLS])
        xgb_fc = pd.DataFrame({"ts": d_te_feat["ts"].values, "yhat": preds})
        xgb_fc["ts"] = pd.to_datetime(xgb_fc["ts"]).dt.tz_localize(None)
        xgb_fc = xgb_fc.set_index("ts").resample(freq)["yhat"].mean().dropna().reset_index()
        xgb_fc["ts"] = xgb_fc["ts"].astype(str).str[:10]
        results["xgboost"] = {r["ts"]: round(float(r["yhat"]), 1) for _, r in xgb_fc.iterrows()}
    except Exception as e:
        results["xgboost"] = {"error": str(e)}

    # ── 결과 병합 ──────────────────────────────────────────
    merged = []
    for _, row in actual.iterrows():
        ts = row["ts"]
        xt = results.get("xgboost")
        merged.append({
            "ts":      ts,
            "actual":  round(float(row["grid_kw"]), 1),
            "xgboost": xt.get(ts) if isinstance(xt, dict) and "error" not in xt else None,
        })

    errors = {k: v["error"] for k, v in results.items() if isinstance(v, dict) and "error" in v}
    mae = {}
    pairs = [(r["actual"], r["xgboost"]) for r in merged if r.get("xgboost") is not None]
    if pairs:
        mae["xgboost"] = round(float(np.mean([abs(a - p) for a, p in pairs])), 2)

    return {
        "train_period": f"2018-01-01 ~ {train_end}",
        "test_period":  f"{train_end} ~ {test_end}",
        "freq": freq,
        "mae_kw": mae,
        "data": merged,
        "errors": errors,
    }
