"""예측 API — 학습 트리거 + 예측 결과 조회."""
from api.errors import safe_err
import sys
from pathlib import Path

from fastapi import APIRouter, Query

_APP_ROOT = Path(__file__).resolve().parents[3]  # backend/
sys.path.insert(0, str(_APP_ROOT))

router = APIRouter(prefix="/forecast", tags=["forecast"])

_PROJECT_ROOT = _APP_ROOT  # ml/ is now at backend/ml/
_PROCS: dict[str, "subprocess.Popen"] = {}

# ── 모델 레지스트리 ──────────────────────────────────────────────────
MODEL_REGISTRY: dict[str, dict] = {
    "v84-ensemble": {
        "name":        "v84-ensemble",
        "label":       "v84 앙상블",
        "color":       "#a371f7",
        "description": "LSTM×6 + CatBoost + LightGBM + Ridge + Naive — 계량기별 잔차 앙상블",
        "trainable":   True,
        "max_hours":   None,
        "badges": [
            {"text": "주력 모델",     "color": "#a371f7", "bg": "#1e1034"},
            {"text": "계량기별 예측", "color": "#6e7681", "bg": "#161b22"},
        ],
    },
}


_STATUS_DIR = _PROJECT_ROOT / "ml" / "pipeline" / "artifacts"
_VENV_PYTHON = sys.executable

# Import P-Max(계통 인입 피크) 예측 모델 artifacts 경로
_PMAX_MODEL_ROOT = _APP_ROOT / "ml" / "forecasting" / "artifacts" / "import_pmax_v29_60min"
_PMAX_TABLE      = "mart.peak_feature_15min"


def _status_file(horizon: int) -> Path:
    _STATUS_DIR.mkdir(parents=True, exist_ok=True)
    return _STATUS_DIR / f"train_status_{horizon}h.txt"


def _get_status(horizon: int) -> str:
    f = _status_file(horizon)
    if not f.exists():
        return "idle"
    proc = _PROCS.get(f"v84-{horizon}h")
    alive = proc and proc.poll() is None
    text = f.read_text().strip()
    if alive:
        return text if text.startswith("running") else "running"
    if text.startswith("running"):
        return "idle"
    return text


@router.get("/models")
def get_models():
    """등록된 예측 모델 목록 및 메타데이터 반환."""
    from ml.pipeline.inference import is_available
    result = []
    for meta in MODEL_REGISTRY.values():
        entry = dict(meta)
        avail_1h = is_available("H2.Z66", 1)
        avail_3h = is_available("H2.Z66", 3)
        if avail_1h or avail_3h:
            entry["status"] = "done"
        else:
            entry["status"] = _get_status(1)
        entry["available"] = avail_1h or avail_3h
        result.append(entry)
    return {"models": result}


@router.post("/train/{model_name}")
def train_model(
    model_name: str,
    horizon: int = Query(1, ge=1, le=3),
    meters: str  = Query(None, description="쉼표 구분 계량기 URN (없으면 전체)"),
):
    """v84 앙상블 학습 시작 (별도 프로세스)."""
    import subprocess
    if model_name not in MODEL_REGISTRY:
        return {"error": f"지원하지 않는 모델: {model_name}"}

    proc_key = f"v84-{horizon}h"
    proc = _PROCS.get(proc_key)
    if proc and proc.poll() is None:
        return {"status": "already_running", "horizon": horizon}

    cmd = [_VENV_PYTHON, "-m", "ml.pipeline.train", "--horizon", str(horizon)]
    if meters:
        cmd += ["--meters"] + meters.split(",")
    # pass1(LSTM) 아티팩트가 이미 있으면 스킵해서 수십 분 → 수 분으로 단축
    sample_pt = _STATUS_DIR / f"{horizon}h" / "H2.Z66" / "lstm_v1.pt"
    if sample_pt.exists():
        cmd.append("--skip-pass1")

    _status_file(horizon).write_text("running")
    proc = subprocess.Popen(cmd, cwd=str(_PROJECT_ROOT))
    _PROCS[proc_key] = proc
    return {"status": "started", "model": model_name, "horizon": horizon}


@router.get("/train/status")
def training_status():
    """학습 상태 조회."""
    return {"status": {f"v84-{h}h": _get_status(h) for h in (1, 3)}}


@router.get("/predict/{model_name}")
def predict(
    model_name: str,
    meter_urn: str = Query("H2.Z66", description="계량기 URN"),
    horizon:   int = Query(1, ge=1, le=3),
):
    """v84 앙상블 추론 실행."""
    if model_name not in MODEL_REGISTRY:
        return {"error": f"지원하지 않는 모델: {model_name}"}
    try:
        import pandas as pd
        from ml.pipeline.inference import predict_meter, is_available
        from ml.pipeline.common.config import METER_SPECS_BY_URN
        from ml.pipeline.common.db import build_engine, fetch_meter_window

        if meter_urn not in METER_SPECS_BY_URN:
            return {"error": f"알 수 없는 계량기: {meter_urn}"}
        if not is_available(meter_urn, horizon):
            return {"error": f"artifacts 없음. POST /forecast/train/v84-ensemble 먼저 실행하세요"}

        engine = build_engine()
        spec   = METER_SPECS_BY_URN[meter_urn]
        # DB 최신 데이터 시각 기준으로 조회 (실시간 데이터 없을 때 대비)
        from sqlalchemy import text as sa_text
        with engine.connect() as conn:
            result = conn.execute(
                sa_text("SELECT MAX(ts) FROM reference.corrected_resampled_1h WHERE meter_urn = :urn"),
                {"urn": meter_urn},
            )
            max_ts = result.scalar()
        if max_ts:
            end_ts = pd.Timestamp(max_ts)
            end_ts = end_ts.tz_convert("UTC") if end_ts.tzinfo else end_ts.tz_localize("UTC")
        else:
            end_ts = pd.Timestamp.now(tz="UTC")
        # 24시간 예측 트렌드 구축을 위해 넉넉히 250시간 조회
        raw_df = fetch_meter_window(engine, spec, end_ts=end_ts, window_hours=250)
        raw_df["ts_dt"] = pd.to_datetime(raw_df["ts"], utc=True)

        records = []
        for i in range(23, -1, -1):
            target_ts = end_ts - pd.Timedelta(hours=i)
            sub_df = raw_df[raw_df["ts_dt"] <= target_ts].copy()
            if len(sub_df) < 50:
                continue
            try:
                pred_df = predict_meter(meter_urn, horizon, sub_df, spec)
                if not pred_df.empty:
                    row = pred_df.iloc[0]
                    records.append({
                        "ts": str(row["ts"])[:16],
                        "yhat_kw": round(float(row[f"pred_t_plus_{horizon}"]) / 1000, 3)
                    })
            except Exception:
                continue
        return {"model": model_name, "meter_urn": meter_urn, "horizon": horizon, "forecast": records}
    except FileNotFoundError as e:
        return {"error": safe_err(e)}
    except Exception as e:
        return {"error": safe_err(e)}


@router.get("/peak")
def predict_peak(
    as_of: str = Query(None, description="15분 경계 UTC 시각(예: 2026-06-08T10:45:00Z). 생략 시 시뮬레이터 가상 시각 기준."),
    lookback_days: int = Query(14, ge=4, le=60, description="입력 윈도우 조회 일수"),
):
    """계통 인입 피크(import P_max) 향후 60분(15분×4) 예측 — 4개 논리 계량기 일괄.

    Import P-Max v29 앙상블(LightGBM×2 + XGBoost + CatBoost). 단위 W → kW 변환 반환.
    as_of 미지정 시 시뮬레이터 가상 시각(15분 정렬)을 기준으로 하여 데모와 정합한다.
    """
    import os
    # pmax build_engine은 DB_PASS를 읽지만 프로젝트 .env는 DB_PASSWORD를 쓴다 → 브리지
    if not os.getenv("DB_PASS") and os.getenv("DB_PASSWORD"):
        os.environ["DB_PASS"] = os.getenv("DB_PASSWORD")

    # as_of 결정: 명시값 > 시뮬레이터 가상 시각 > (폴백) DB 최신 공통 시각
    basis = "query" if as_of else "latest"
    sim_based = False
    if not as_of:
        try:
            import pandas as pd
            from api.routers.simulator import effective_now
            as_of = pd.Timestamp(effective_now()).floor("15min").strftime("%Y-%m-%dT%H:%M:%SZ")
            basis, sim_based = "simulator", True
        except Exception:
            as_of = None

    try:
        from ml.forecasting.import_pmax import batch_inference

        def _run(_as_of):
            return batch_inference.run_batch(
                table_name=_PMAX_TABLE,
                model_root=_PMAX_MODEL_ROOT,
                requested_as_of=_as_of,
                lookback_days=lookback_days,
            )

        try:
            batch = _run(as_of)
        except Exception:
            # 시뮬 시각에 데이터가 부족하면 DB 최신 공통 시각으로 폴백
            if sim_based:
                batch, basis = _run(None), "latest"
            else:
                raise
        meters = []
        for r in batch["results"]:
            peak = r["next_60min_peak"]
            meters.append({
                "logical_meter":     r["logical_meter"],
                "input_end_ts":      r["input_end_ts"],
                "last_import_p_max_kw": round(float(r["last_import_p_max"]) / 1000, 2),
                "data_quality":      r["data_quality"]["status"],
                "peak_kw":           round(float(peak["predicted_import_p_max"]) / 1000, 2),
                "peak_at":           peak["timestamp"],
                "predictions": [
                    {
                        "horizon_minutes": p["horizon_minutes"],
                        "timestamp":       p["timestamp"],
                        "predicted_kw":    round(float(p["predicted_import_p_max"]) / 1000, 2),
                    }
                    for p in r["predictions"]
                ],
            })
        return {
            "model":           "import-pmax-v29",
            "requested_as_of": batch["requested_as_of"],
            "basis":           basis,
            "meter_count":     batch["logical_meter_count"],
            "meters":          meters,
        }
    except Exception as e:
        return {"error": safe_err(e)}
