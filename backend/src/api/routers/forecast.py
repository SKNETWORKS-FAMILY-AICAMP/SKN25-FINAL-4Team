"""예측 API — 학습 트리거 + 예측 결과 조회."""
import sys
from pathlib import Path

from fastapi import APIRouter, Query

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # project root

router = APIRouter(prefix="/forecast", tags=["forecast"])

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
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
_VENV_PYTHON = str(_PROJECT_ROOT / ".venv-train" / "bin" / "python")


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
        now    = pd.Timestamp.now(tz="UTC")
        raw_df = fetch_meter_window(engine, spec, end_ts=now, window_hours=200)

        df = predict_meter(meter_urn, horizon, raw_df, spec)
        records = [
            {"ts": str(row["ts"])[:16], "yhat_kw": round(float(row["pred_t_plus_1"]) / 1000, 3)}
            for _, row in df.iterrows()
        ]
        return {"model": model_name, "meter_urn": meter_urn, "horizon": horizon, "forecast": records}
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}
