from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import math
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import batch_inference, inference, operations, training, validation


DEFAULT_CANDIDATE_ROOT = (
    inference.PROJECT_ROOT / "artifacts" / "import_pmax_v29_60min_candidate"
)
DEFAULT_DEPLOYED_ROOT = operations.DEPLOYED_ROOT
RUNTIME_README = """# Import P-Max 운영 모델 산출물

4개 논리 계량기의 운영 추론에 사용하는 모델 산출물이다.

- 입력: 15분 간격 최근 24시간, 총 96행
- 출력: 향후 60분에 대한 15분 단위 예측 4개
- 피처: 기상 정보를 제외한 22개
- 앙상블: LightGBM 2개, XGBoost 1개, CatBoost 1개

필수 폴더 구조:

```text
input_24h/predict_60min/{logical_meter}/
  _candidate_models/*.joblib
  v29/manifest.json
  v29/ensemble_weights.csv
```

추론 코드는 이 폴더에서 모델과 가중치를 불러와 최종 예측값을 생성한다.
`deployment_metrics.json`과 `promotion.json`은 배포 이력을 기록한다.
"""


class PromotionSmokeError(RuntimeError):
    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__(
            f"Post-promotion inference smoke failed: {result['smoke_error']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and promote Import P-Max candidate artifacts."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--run-id",
        help="Candidate run under artifacts/import_pmax_candidates/.",
    )
    source.add_argument(
        "--candidate-root",
        type=Path,
        help="Explicit candidate artifact root.",
    )
    parser.add_argument(
        "--deployed-root",
        type=Path,
        default=DEFAULT_DEPLOYED_ROOT,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Promote a previously validated candidate.",
    )
    parser.add_argument(
        "--allow-warn",
        action="store_true",
        help="Allow promotion when validation result is warn.",
    )
    parser.add_argument(
        "--approval-note",
        help="Human or LLM approval reference recorded in promotion.json.",
    )
    return parser.parse_args()


def resolve_candidate_root(args: argparse.Namespace) -> Path:
    if args.run_id:
        return operations.candidate_root(args.run_id)
    if args.candidate_root:
        return args.candidate_root.resolve()
    return DEFAULT_CANDIDATE_ROOT


def validate_candidate(
    candidate_root: Path,
    *,
    deployed_root: Path = DEFAULT_DEPLOYED_ROOT,
    load_models: bool = True,
    write_result: bool = False,
) -> dict[str, Any]:
    # load_models is kept for compatibility with existing callers. Operational
    # validation always loads every model because deserialization is a gate.
    del load_models
    return validation.validate_candidate(
        candidate_root,
        deployed_root,
        write_result=write_result,
    )


def copy_runtime_artifacts(
    candidate_root: Path,
    staging_root: Path,
    validation_result: dict[str, Any],
) -> None:
    if staging_root.exists():
        shutil.rmtree(staging_root)
    for logical_meter in training.LOGICAL_METERS:
        source_model_dir, source_manifest, source_weights = inference.artifact_paths(
            candidate_root,
            logical_meter,
        )
        target_model_dir, target_manifest, target_weights = inference.artifact_paths(
            staging_root,
            logical_meter,
        )
        target_model_dir.mkdir(parents=True, exist_ok=True)
        target_manifest.parent.mkdir(parents=True, exist_ok=True)
        for version in inference.EXPECTED_CANDIDATES:
            shutil.copy2(
                source_model_dir / f"{version}.joblib",
                target_model_dir / f"{version}.joblib",
            )
        shutil.copy2(source_manifest, target_manifest)
        shutil.copy2(source_weights, target_weights)

    metrics = validation.deployment_metrics_payload(
        candidate_root, validation_result
    )
    (staging_root / validation.DEPLOYMENT_METRICS_FILENAME).write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (staging_root / "README.md").write_text(RUNTIME_README, encoding="utf-8")


def backup_path(
    deployed_root: Path,
    promoted_at: datetime,
    previous_run_id: str | None = None,
) -> Path:
    stamp = promoted_at.strftime("%Y%m%dT%H%M%SZ")
    suffix = previous_run_id or "unknown"
    return operations.ARCHIVES_ROOT / f"{stamp}_{suffix}"


def _previous_run_id(deployed_root: Path) -> str | None:
    metrics_path = deployed_root / validation.DEPLOYMENT_METRICS_FILENAME
    if metrics_path.is_file():
        try:
            run_id = json.loads(metrics_path.read_text(encoding="utf-8")).get("run_id")
            return operations.validate_run_id(run_id) if isinstance(run_id, str) else None
        except Exception:
            return None
    promotion_path = deployed_root / "promotion.json"
    if promotion_path.is_file():
        try:
            run_id = json.loads(promotion_path.read_text(encoding="utf-8")).get("run_id")
            return operations.validate_run_id(run_id) if isinstance(run_id, str) else None
        except Exception:
            return None
    return None


@contextlib.contextmanager
def deployment_lock(deployed_root: Path):
    deployed_root.parent.mkdir(parents=True, exist_ok=True)
    lock_path = deployed_root.with_name(f".{deployed_root.name}.operations.lock")
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _backup_runtime(source_root: Path, backup_root: Path) -> None:
    if backup_root.exists():
        raise FileExistsError(f"Backup path already exists: {backup_root}")
    backup_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_root, backup_root)
    try:
        validation.validate_runtime_artifacts(backup_root)
    except Exception:
        shutil.rmtree(backup_root, ignore_errors=True)
        raise


def _smoke_lookback_days() -> int:
    raw = os.getenv(
        "IMPORT_PMAX_SMOKE_LOOKBACK_DAYS",
        str(inference.DEFAULT_LOOKBACK_DAYS),
    )
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "IMPORT_PMAX_SMOKE_LOOKBACK_DAYS must be an integer"
        ) from exc
    if value < 4:
        raise ValueError(
            "IMPORT_PMAX_SMOKE_LOOKBACK_DAYS must be at least 4"
        )
    return value


def run_inference_smoke(deployed_root: Path) -> dict[str, Any]:
    table_name = (
        os.getenv("IMPORT_PMAX_SMOKE_TABLE", "").strip()
        or training.DEFAULT_TABLE
    )
    requested_as_of = (
        os.getenv("IMPORT_PMAX_SMOKE_AS_OF", "").strip() or None
    )
    started = time.perf_counter()
    batch = batch_inference.run_batch(
        table_name=table_name,
        model_root=deployed_root,
        requested_as_of=requested_as_of,
        lookback_days=_smoke_lookback_days(),
    )

    if batch.get("status") != "success":
        raise ValueError(
            f"Inference smoke returned unexpected status: {batch.get('status')}"
        )
    results = batch.get("results")
    if not isinstance(results, list):
        raise ValueError("Inference smoke results must be a list")
    if batch.get("logical_meter_count") != len(training.LOGICAL_METERS):
        raise ValueError(
            "Inference smoke must return all logical meters: "
            f"{batch.get('logical_meter_count')}"
        )
    if len(results) != len(training.LOGICAL_METERS):
        raise ValueError(
            "Inference smoke result count mismatch: "
            f"expected={len(training.LOGICAL_METERS)}, actual={len(results)}"
        )
    if batch.get("prediction_row_count") != (
        len(training.LOGICAL_METERS) * inference.EXPECTED_HORIZON_STEPS
    ):
        raise ValueError(
            "Inference smoke returned an unexpected prediction row count: "
            f"{batch.get('prediction_row_count')}"
        )

    actual_meters: list[str] = []
    for meter_result in results:
        logical_meter = meter_result.get("logical_meter")
        if not isinstance(logical_meter, str):
            raise ValueError(
                f"Inference smoke returned an invalid meter: {logical_meter!r}"
            )
        actual_meters.append(logical_meter)
        predictions = meter_result.get("predictions")
        if not isinstance(predictions, list) or len(predictions) != (
            inference.EXPECTED_HORIZON_STEPS
        ):
            raise ValueError(
                f"Inference smoke returned an invalid horizon for {logical_meter}"
            )
        for prediction in predictions:
            clipped = float(prediction["predicted_import_p_max"])
            raw = float(prediction["raw_model_prediction"])
            if not math.isfinite(clipped) or not math.isfinite(raw):
                raise ValueError(
                    f"Inference smoke returned a non-finite value for {logical_meter}"
                )
            if clipped < 0:
                raise ValueError(
                    f"Inference smoke returned a negative final value for {logical_meter}"
                )

    if set(actual_meters) != set(training.LOGICAL_METERS):
        raise ValueError(
            "Inference smoke meter set mismatch: "
            f"expected={sorted(training.LOGICAL_METERS)}, "
            f"actual={sorted(actual_meters)}"
        )

    return {
        "result": "pass",
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "table_name": table_name,
        "requested_as_of": batch["requested_as_of"],
        "logical_meter_count": batch["logical_meter_count"],
        "prediction_row_count": batch["prediction_row_count"],
        "logical_meters": actual_meters,
        "elapsed_seconds": time.perf_counter() - started,
    }


def _write_promotion_failure(
    candidate_root: Path,
    result: dict[str, Any],
) -> str | None:
    try:
        (candidate_root / "promotion_failure.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return str(exc)
    return None


def promote_candidate(
    candidate_root: Path,
    deployed_root: Path,
    *,
    approval_note: str | None = None,
    allow_warn: bool = False,
    archives_root: Path | None = None,
    smoke_runner: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    with deployment_lock(deployed_root):
        validation_result = validation.assert_validation_current(
            candidate_root,
            allow_warn=allow_warn,
        )
        promoted_at = datetime.now(timezone.utc)
        staging_root = deployed_root.with_name(f".{deployed_root.name}.staging")
        retired_root = deployed_root.with_name(f".{deployed_root.name}.retired")
        previous_run_id = _previous_run_id(deployed_root)
        if archives_root is None:
            backup_root = backup_path(
                deployed_root,
                promoted_at,
                previous_run_id,
            )
        else:
            stamp = promoted_at.strftime("%Y%m%dT%H%M%SZ")
            backup_root = archives_root / f"{stamp}_{previous_run_id or 'unknown'}"
        if backup_root.exists():
            raise FileExistsError(f"Backup path already exists: {backup_root}")
        if retired_root.exists():
            raise FileExistsError(
                f"Unresolved previous deployment swap found: {retired_root}"
            )

        copy_runtime_artifacts(candidate_root, staging_root, validation_result)
        validation.validate_runtime_artifacts(staging_root)

        result = {
            "status": "promotion_pending_smoke",
            "run_id": validation_result["run_id"],
            "promoted_at": promoted_at.isoformat(),
            "candidate_root": str(candidate_root),
            "candidate_digest": validation_result["candidate_digest"],
            "deployed_root": str(deployed_root),
            "backup_root": None,
            "approval_note": approval_note,
            "validation_result": validation_result["result"],
        }
        moved_deployed = False
        activated = False
        try:
            if deployed_root.exists():
                _backup_runtime(deployed_root, backup_root)
                result["backup_root"] = str(backup_root)
                deployed_root.rename(retired_root)
                moved_deployed = True
            (staging_root / "promotion.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            staging_root.rename(deployed_root)
            activated = True
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            if moved_deployed and retired_root.exists() and not deployed_root.exists():
                retired_root.rename(deployed_root)
            if result["backup_root"]:
                shutil.rmtree(backup_root, ignore_errors=True)
            raise

        smoke_runner = smoke_runner or run_inference_smoke
        try:
            smoke_result = smoke_runner(deployed_root)
            if smoke_result.get("result") != "pass":
                raise ValueError(
                    "Inference smoke runner must return result='pass'"
                )
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
                if activated and deployed_root.exists():
                    shutil.rmtree(deployed_root)
                if moved_deployed:
                    retired_root.rename(deployed_root)
                failure["automatic_rollback"][
                    "restored_previous_deployment"
                ] = True
            except Exception as restore_exc:
                rollback_error = restore_exc
                failure["automatic_rollback"]["error"] = (
                    f"{type(restore_exc).__name__}: {restore_exc}"
                )

            marker_warning = _write_promotion_failure(candidate_root, failure)
            if marker_warning:
                failure["candidate_failure_marker_warning"] = marker_warning
            if rollback_error is not None:
                failure["smoke_error"] += (
                    "; automatic rollback also failed: "
                    f"{type(rollback_error).__name__}: {rollback_error}"
                )
            raise PromotionSmokeError(failure) from exc

        result["status"] = "promoted"
        result["inference_smoke"] = smoke_result
        if retired_root.exists():
            try:
                shutil.rmtree(retired_root)
            except OSError as exc:
                result["retired_cleanup_warning"] = str(exc)

        result["candidate_deleted"] = False
        try:
            (deployed_root / "promotion.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            result["promotion_metadata_warning"] = str(exc)
            return result

        try:
            shutil.rmtree(candidate_root)
        except OSError as exc:
            result["candidate_cleanup_warning"] = str(exc)
        result["candidate_deleted"] = not candidate_root.exists()

        try:
            (deployed_root / "promotion.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            result["candidate_cleanup_metadata_warning"] = str(exc)
        return result


def rollback_deployment(
    archive_root: Path,
    deployed_root: Path,
    *,
    approval_note: str | None = None,
    archives_root: Path | None = None,
) -> dict[str, Any]:
    with deployment_lock(deployed_root):
        if not archive_root.is_dir():
            raise FileNotFoundError(f"Rollback archive not found: {archive_root}")
        validation.validate_runtime_artifacts(archive_root)

        rolled_back_at = datetime.now(timezone.utc)
        staging_root = deployed_root.with_name(
            f".{deployed_root.name}.rollback-staging"
        )
        retired_root = deployed_root.with_name(f".{deployed_root.name}.retired")
        if staging_root.exists():
            shutil.rmtree(staging_root)
        if retired_root.exists():
            raise FileExistsError(
                f"Unresolved previous deployment swap found: {retired_root}"
            )
        shutil.copytree(archive_root, staging_root)
        validation.validate_runtime_artifacts(staging_root)

        current_run_id = _previous_run_id(deployed_root)
        archive_base = archives_root or archive_root.parent
        stamp = rolled_back_at.strftime("%Y%m%dT%H%M%SZ")
        replaced_root = archive_base / (
            f"{stamp}_rollback_replaced_{current_run_id or 'unknown'}"
        )
        if replaced_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)
            raise FileExistsError(
                f"Rollback backup path already exists: {replaced_root}"
            )

        result = {
            "status": "rolled_back",
            "rolled_back_at": rolled_back_at.isoformat(),
            "source_archive": str(archive_root),
            "deployed_root": str(deployed_root),
            "replaced_deployment_backup": None,
            "approval_note": approval_note,
        }
        moved_deployed = False
        try:
            if deployed_root.exists():
                _backup_runtime(deployed_root, replaced_root)
                result["replaced_deployment_backup"] = str(replaced_root)
                deployed_root.rename(retired_root)
                moved_deployed = True
            (staging_root / "rollback.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            staging_root.rename(deployed_root)
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            if moved_deployed and retired_root.exists() and not deployed_root.exists():
                retired_root.rename(deployed_root)
            if result["replaced_deployment_backup"]:
                shutil.rmtree(replaced_root, ignore_errors=True)
            raise
        finally:
            if deployed_root.exists() and retired_root.exists():
                shutil.rmtree(retired_root, ignore_errors=True)

        return result


def main() -> None:
    args = parse_args()
    candidate_root = resolve_candidate_root(args)
    if args.execute:
        result = promote_candidate(
            candidate_root,
            args.deployed_root,
            approval_note=args.approval_note,
            allow_warn=args.allow_warn,
        )
    else:
        result = validation.validate_candidate(
            candidate_root,
            args.deployed_root,
            run_id=args.run_id or candidate_root.name,
            write_result=True,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
