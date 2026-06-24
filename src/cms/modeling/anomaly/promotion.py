from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from . import validation


SUMMARY_TEMPLATE = "train_summary_{horizon}h.csv"
PROMOTION_TEMPLATE = "promotion_{horizon}h.json"


class PromotionSmokeError(RuntimeError):
    def __init__(self, result: dict[str, Any]):
        self.result = copy.deepcopy(result)
        super().__init__(f"Post-promotion inference smoke failed: {result['smoke_error']}")


def _summary_name(horizon: int) -> str:
    return SUMMARY_TEMPLATE.format(horizon=horizon)


def _promotion_name(horizon: int) -> str:
    return PROMOTION_TEMPLATE.format(horizon=horizon)


def _require_horizon(horizon: int) -> None:
    if horizon not in (1, 3):
        raise ValueError("anomaly horizon must be 1 or 3")


@contextlib.contextmanager
def deployment_lock(deployed_root: Path, horizon: int):
    deployed_root.mkdir(parents=True, exist_ok=True)
    lock_path = deployed_root / f".{horizon}h.operations.lock"
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _previous_run_id(deployed_root: Path, horizon: int) -> str | None:
    promotion_path = deployed_root / _promotion_name(horizon)
    if not promotion_path.is_file():
        return None
    try:
        payload = json.loads(promotion_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    run_id = payload.get("run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def _copy_horizon_artifacts(source_root: Path, target_root: Path, horizon: int) -> None:
    _require_horizon(horizon)
    source_horizon = source_root / f"{horizon}h"
    source_summary = source_root / _summary_name(horizon)
    target_horizon = target_root / f"{horizon}h"
    target_summary = target_root / _summary_name(horizon)

    if not source_horizon.is_dir():
        raise FileNotFoundError(f"source horizon directory not found: {source_horizon}")
    if not source_summary.is_file():
        raise FileNotFoundError(f"source summary not found: {source_summary}")

    if target_horizon.exists():
        shutil.rmtree(target_horizon)
    shutil.copytree(source_horizon, target_horizon)
    shutil.copy2(source_summary, target_summary)


def _backup_active_horizon(deployed_root: Path, backup_root: Path, horizon: int) -> bool:
    active_horizon = deployed_root / f"{horizon}h"
    active_summary = deployed_root / _summary_name(horizon)
    if not active_horizon.exists() and not active_summary.exists():
        return False
    if backup_root.exists():
        raise FileExistsError(f"Backup path already exists: {backup_root}")
    backup_root.mkdir(parents=True, exist_ok=False)
    if active_horizon.exists():
        shutil.copytree(active_horizon, backup_root / f"{horizon}h")
    if active_summary.exists():
        shutil.copy2(active_summary, backup_root / _summary_name(horizon))
    return True


def _cleanup_promoted_candidate(candidate_root: Path, horizon: int) -> dict[str, Any]:
    removed: list[str] = []
    warnings: list[str] = []
    targets = [
        candidate_root / f"{horizon}h",
        candidate_root / _summary_name(horizon),
        candidate_root / validation.VALIDATION_FILENAME,
    ]
    for target in targets:
        if not target.exists():
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append(str(target))
        except OSError as exc:
            warnings.append(f"{target}: {exc}")
    try:
        candidate_root.rmdir()
        removed.append(str(candidate_root))
    except OSError:
        pass
    return {
        "candidate_deleted": not candidate_root.exists(),
        "removed": removed,
        "warnings": warnings,
    }


def run_inference_smoke(deployed_root: Path, horizon: int) -> dict[str, Any]:
    from . import predictor
    from .mapping import ARTIFACTS_DIR, METER_MAP

    configured_root = Path(ARTIFACTS_DIR).resolve()
    if configured_root != deployed_root.resolve():
        raise ValueError(
            "anomaly predictor ARTIFACTS_DIR does not match deployed_root: "
            f"{configured_root} != {deployed_root.resolve()}"
        )

    timestamp = os.getenv("ANOMALY_SMOKE_TIMESTAMP", "2023-06-01T09:00:00Z")
    min_success = int(os.getenv("ANOMALY_SMOKE_MIN_SUCCESS", "50"))
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="anomaly_smoke_") as temporary:
        results = predictor.run_inference(
            horizon=horizon,
            timestamp=pd.Timestamp(timestamp),
            output_dir=Path(temporary),
        )
    success_count = sum(1 for row in results.values() if row.get("status") == "success")
    expected_count = sum(1 for item in METER_MAP.values() if item.get("action") != "skip")
    if success_count < min_success:
        raise ValueError(f"Smoke success count too low: {success_count}/{expected_count}, minimum={min_success}")
    return {
        "result": "pass",
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "horizon": horizon,
        "timestamp": timestamp,
        "success_count": success_count,
        "expected_count": expected_count,
        "elapsed_seconds": time.perf_counter() - started,
    }


def promote_candidate(
    candidate_root: Path,
    deployed_root: Path,
    *,
    horizon: int,
    approval_note: str | None = None,
    allow_warn: bool = False,
    archives_root: Path | None = None,
    smoke_runner: Callable[[Path, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _require_horizon(horizon)
    with deployment_lock(deployed_root, horizon):
        validation_result = validation.assert_validation_current(candidate_root, allow_warn=allow_warn)
        if validation_result.get("horizon") != horizon:
            raise ValueError(
                f"Validation horizon mismatch: validation={validation_result.get('horizon')}, requested={horizon}"
            )

        promoted_at = datetime.now(timezone.utc)
        stamp = promoted_at.strftime("%Y%m%dT%H%M%SZ")
        previous_run_id = _previous_run_id(deployed_root, horizon)
        archive_base = archives_root or (deployed_root.parent / "anomaly_archives")
        backup_root = archive_base / f"{stamp}_{previous_run_id or 'unknown'}_{horizon}h"

        staging_root = deployed_root / f".{horizon}h.staging"
        retired_horizon = deployed_root / f".{horizon}h.retired"
        retired_summary = deployed_root / f".{_summary_name(horizon)}.retired"
        active_horizon = deployed_root / f"{horizon}h"
        active_summary = deployed_root / _summary_name(horizon)

        if staging_root.exists():
            shutil.rmtree(staging_root)
        if retired_horizon.exists() or retired_summary.exists():
            raise FileExistsError("Unresolved previous anomaly deployment swap found")

        staging_root.mkdir(parents=True)
        _copy_horizon_artifacts(candidate_root, staging_root, horizon)
        validation.validate_runtime_artifacts(staging_root, horizon)

        result: dict[str, Any] = {
            "status": "promotion_pending_smoke",
            "model_kind": "anomaly",
            "run_id": validation_result["run_id"],
            "horizon": horizon,
            "promoted_at": promoted_at.isoformat(),
            "candidate_root": str(candidate_root),
            "candidate_digest": validation_result["candidate_digest"],
            "deployed_root": str(deployed_root),
            "backup_root": None,
            "approval_note": approval_note,
            "validation_result": validation_result["result"],
        }

        moved_active = False
        activated = False
        try:
            if _backup_active_horizon(deployed_root, backup_root, horizon):
                result["backup_root"] = str(backup_root)
            if active_horizon.exists():
                active_horizon.rename(retired_horizon)
                moved_active = True
            if active_summary.exists():
                active_summary.rename(retired_summary)
            (staging_root / f"{horizon}h").rename(active_horizon)
            (staging_root / _summary_name(horizon)).rename(active_summary)
            shutil.rmtree(staging_root, ignore_errors=True)
            activated = True
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            if not active_horizon.exists() and retired_horizon.exists():
                retired_horizon.rename(active_horizon)
            if not active_summary.exists() and retired_summary.exists():
                retired_summary.rename(active_summary)
            if result["backup_root"]:
                shutil.rmtree(backup_root, ignore_errors=True)
            raise

        smoke_runner = smoke_runner or run_inference_smoke
        try:
            smoke_result = smoke_runner(deployed_root, horizon)
            if smoke_result.get("result") != "pass":
                raise ValueError("Inference smoke runner must return result='pass'")
        except Exception as exc:
            failure = {
                **result,
                "status": "promotion_smoke_failed",
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "smoke_error": f"{type(exc).__name__}: {exc}",
                "automatic_rollback": {
                    "attempted": True,
                    "restored_previous_deployment": False,
                },
            }
            rollback_error: Exception | None = None
            try:
                if moved_active:
                    failed_horizon = deployed_root / f".{horizon}h.failed-smoke"
                    failed_summary = deployed_root / f".{_summary_name(horizon)}.failed-smoke"
                    if failed_horizon.exists():
                        shutil.rmtree(failed_horizon)
                    if failed_summary.exists():
                        failed_summary.unlink()
                    if active_horizon.exists():
                        active_horizon.rename(failed_horizon)
                    if active_summary.exists():
                        active_summary.rename(failed_summary)
                    try:
                        retired_horizon.rename(active_horizon)
                        if retired_summary.exists():
                            retired_summary.rename(active_summary)
                        failure["automatic_rollback"]["restored_previous_deployment"] = True
                        if failed_horizon.exists():
                            shutil.rmtree(failed_horizon)
                        if failed_summary.exists():
                            failed_summary.unlink()
                    except Exception:
                        if not active_horizon.exists() and failed_horizon.exists():
                            failed_horizon.rename(active_horizon)
                        if not active_summary.exists() and failed_summary.exists():
                            failed_summary.rename(active_summary)
                        raise
                else:
                    failure["automatic_rollback"]["restored_previous_deployment"] = False
                    failure["automatic_rollback"]["reason"] = "no_previous_deployment"
            except Exception as restore_exc:
                rollback_error = restore_exc
                failure["automatic_rollback"]["error"] = f"{type(restore_exc).__name__}: {restore_exc}"

            try:
                (candidate_root / "promotion_failure.json").write_text(
                    json.dumps(failure, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            except OSError as marker_exc:
                failure["candidate_failure_marker_warning"] = str(marker_exc)
            if rollback_error is not None:
                failure["smoke_error"] += (
                    "; automatic rollback also failed: "
                    f"{type(rollback_error).__name__}: {rollback_error}"
                )
            raise PromotionSmokeError(failure) from exc

        for retired in (retired_horizon, retired_summary):
            if retired.exists():
                if retired.is_dir():
                    shutil.rmtree(retired)
                else:
                    retired.unlink()

        result["status"] = "promoted"
        result["inference_smoke"] = smoke_result
        cleanup = _cleanup_promoted_candidate(candidate_root, horizon)
        result["cleanup"] = cleanup
        (deployed_root / _promotion_name(horizon)).write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return result


def rollback_deployment(
    archive_root: Path,
    deployed_root: Path,
    *,
    horizon: int,
    approval_note: str | None = None,
) -> dict[str, Any]:
    _require_horizon(horizon)
    with deployment_lock(deployed_root, horizon):
        if not archive_root.is_dir():
            raise FileNotFoundError(f"Rollback archive not found: {archive_root}")
        validation.validate_runtime_artifacts(archive_root, horizon)

        rolled_back_at = datetime.now(timezone.utc)
        staging_root = deployed_root / f".{horizon}h.rollback-staging"
        retired_horizon = deployed_root / f".{horizon}h.retired"
        retired_summary = deployed_root / f".{_summary_name(horizon)}.retired"
        active_horizon = deployed_root / f"{horizon}h"
        active_summary = deployed_root / _summary_name(horizon)

        if staging_root.exists():
            shutil.rmtree(staging_root)
        if retired_horizon.exists() or retired_summary.exists():
            raise FileExistsError("Unresolved previous anomaly deployment swap found")

        staging_root.mkdir(parents=True)
        _copy_horizon_artifacts(archive_root, staging_root, horizon)
        validation.validate_runtime_artifacts(staging_root, horizon)

        current_run_id = _previous_run_id(deployed_root, horizon)
        archive_base = archive_root.parent
        stamp = rolled_back_at.strftime("%Y%m%dT%H%M%SZ")
        replaced_root = archive_base / f"{stamp}_rollback_replaced_{current_run_id or 'unknown'}_{horizon}h"

        result = {
            "status": "rolled_back",
            "model_kind": "anomaly",
            "horizon": horizon,
            "rolled_back_at": rolled_back_at.isoformat(),
            "source_archive": str(archive_root),
            "deployed_root": str(deployed_root),
            "replaced_deployment_backup": None,
            "approval_note": approval_note,
        }
        try:
            if _backup_active_horizon(deployed_root, replaced_root, horizon):
                result["replaced_deployment_backup"] = str(replaced_root)
            if active_horizon.exists():
                active_horizon.rename(retired_horizon)
            if active_summary.exists():
                active_summary.rename(retired_summary)
            (staging_root / f"{horizon}h").rename(active_horizon)
            (staging_root / _summary_name(horizon)).rename(active_summary)
            shutil.rmtree(staging_root, ignore_errors=True)
            (deployed_root / f"rollback_{horizon}h.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            if not active_horizon.exists() and retired_horizon.exists():
                retired_horizon.rename(active_horizon)
            if not active_summary.exists() and retired_summary.exists():
                retired_summary.rename(active_summary)
            if result["replaced_deployment_backup"]:
                shutil.rmtree(replaced_root, ignore_errors=True)
            raise
        for retired in (retired_horizon, retired_summary):
            if retired.exists():
                if retired.is_dir():
                    shutil.rmtree(retired)
                else:
                    retired.unlink()
        return result
