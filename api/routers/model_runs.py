from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Header, HTTPException

from api.routers.model_auth import require_model_ops_token, validate_run_id
from api.routers.model_paths import ARCHIVES_DIR, CANDIDATE_DIR, DEPLOYED_ROOT
from src.forecasting.import_pmax import promotion, validation


router = APIRouter()


def _candidate_dir(run_id: str) -> Path:
    return CANDIDATE_DIR / run_id


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"{path.name} parse failed: {exc}",
        ) from exc


def _summary_preview(run_id: str) -> dict | None:
    path = _candidate_dir(run_id) / validation.SUMMARY_FILENAME
    if not path.is_file():
        return None
    try:
        frame = pd.read_csv(path)
        return {
            "rows": int(len(frame)),
            "unique_meters": (
                int(frame["meter"].nunique()) if "meter" in frame.columns else None
            ),
            "model_rmse_nan": (
                int(frame["model_rmse"].isna().sum())
                if "model_rmse" in frame.columns
                else None
            ),
        }
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/{run_id}")
def get_model_run(
    run_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    require_model_ops_token(authorization)
    validate_run_id(run_id)
    candidate = _candidate_dir(run_id)
    meter_root = candidate / "input_24h" / "predict_60min"
    marker = _read_json(candidate / validation.VALIDATION_FILENAME)
    promoted = _read_json(candidate / "promoted.json")
    active_promotion = _read_json(DEPLOYED_ROOT / "promotion.json")
    if (
        promoted is None
        and active_promotion is not None
        and active_promotion.get("run_id") == run_id
        and active_promotion.get("status") == "promoted"
    ):
        promoted = active_promotion
    return {
        "run_id": run_id,
        "candidate_exists": candidate.exists(),
        "runtime_layout_exists": meter_root.exists(),
        "meter_dir_count": (
            len([path for path in meter_root.iterdir() if path.is_dir()])
            if meter_root.exists()
            else 0
        ),
        "summary": _summary_preview(run_id),
        "validation": marker,
        "promoted": promoted,
        "next_action": (
            "completed"
            if promoted is not None
            else "validate"
            if candidate.exists() and marker is None
            else "review_validation"
            if marker is not None
            else "inspect_failure"
        ),
    }


@router.post("/{run_id}/validate")
def validate_model_run(
    run_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    require_model_ops_token(authorization)
    validate_run_id(run_id)
    candidate = _candidate_dir(run_id)
    if not candidate.exists():
        raise HTTPException(
            status_code=404,
            detail=f"candidate run not found: {run_id}",
        )
    try:
        result = validation.validate_candidate(
            candidate,
            DEPLOYED_ROOT,
            run_id=run_id,
            write_result=True,
        )
    except validation.CandidateValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"status": "fail", "run_id": run_id, "error": str(exc)},
        ) from exc
    return {
        "status": result["result"],
        "run_id": run_id,
        "validation": result,
        "next_action": "promote" if result["result"] == "pass" else "review_warning",
    }


@router.post("/{run_id}/promote")
def promote_model_run(
    run_id: str,
    confirm: bool = False,
    allow_warn: bool = False,
    approval_note: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    require_model_ops_token(authorization)
    validate_run_id(run_id)
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm=true is required for promotion",
        )
    try:
        result = promotion.promote_candidate(
            _candidate_dir(run_id),
            DEPLOYED_ROOT,
            approval_note=approval_note,
            allow_warn=allow_warn,
        )
    except promotion.PromotionSmokeError as exc:
        raise HTTPException(status_code=409, detail=exc.result) from exc
    except (
        validation.CandidateValidationError,
        FileNotFoundError,
        FileExistsError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **result,
        "next_action": "completed",
    }


@router.post("/rollback")
def rollback_model(
    archive_name: str,
    confirm: bool = False,
    approval_note: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    require_model_ops_token(authorization)
    validate_run_id(archive_name)
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm=true is required for rollback",
        )
    try:
        archive_root = (ARCHIVES_DIR / archive_name).resolve()
        if archive_root.parent != ARCHIVES_DIR.resolve():
            raise ValueError("invalid archive name")
        result = promotion.rollback_deployment(
            archive_root,
            DEPLOYED_ROOT,
            approval_note=approval_note,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**result, "next_action": "completed"}
