from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import inference, training


DEFAULT_CANDIDATE_ROOT = (
    inference.PROJECT_ROOT / "artifacts" / "import_pmax_v29_60min_candidate"
)
DEFAULT_DEPLOYED_ROOT = inference.DEFAULT_MODEL_ROOT
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
평가 보고서와 저장된 test 예측은 candidate 폴더에 보관한다.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and promote import P-max candidate artifacts."
    )
    parser.add_argument(
        "--candidate-root",
        type=Path,
        default=DEFAULT_CANDIDATE_ROOT,
    )
    parser.add_argument(
        "--deployed-root",
        type=Path,
        default=DEFAULT_DEPLOYED_ROOT,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform promotion. Without this flag, validation only is performed.",
    )
    parser.add_argument(
        "--approval-note",
        help="Optional human or LLM approval reference recorded in the result.",
    )
    return parser.parse_args()


def validate_candidate(candidate_root: Path, *, load_models: bool = True) -> dict[str, Any]:
    if not candidate_root.is_dir():
        raise FileNotFoundError(f"Candidate root not found: {candidate_root}")

    meters = []
    for logical_meter in training.LOGICAL_METERS:
        model_dir, manifest_path, weights_path = inference.artifact_paths(
            candidate_root,
            logical_meter,
        )
        required_paths = [manifest_path, weights_path] + [
            model_dir / f"{version}.joblib"
            for version in inference.EXPECTED_CANDIDATES
        ]
        missing = [str(path) for path in required_paths if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                f"Candidate artifact is incomplete for {logical_meter}: {missing}"
            )

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("feature_columns") != training.FEATURE_COLUMNS:
            raise ValueError(
                f"Candidate feature mismatch for {logical_meter}: "
                f"expected {len(training.FEATURE_COLUMNS)} operational features, "
                f"got {len(manifest.get('feature_columns', []))}"
            )
        if load_models:
            artifacts = inference.load_artifacts(candidate_root, logical_meter)
            weights = artifacts.weights
        else:
            weights = {}
        meters.append(
            {
                "logical_meter": logical_meter,
                "feature_count": len(manifest["feature_columns"]),
                "candidate_versions": manifest["candidate_versions"],
                "weights": weights,
            }
        )
    return {
        "candidate_root": str(candidate_root),
        "logical_meter_count": len(meters),
        "meters": meters,
    }


def copy_runtime_artifacts(candidate_root: Path, staging_root: Path) -> None:
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
    (staging_root / "README.md").write_text(RUNTIME_README, encoding="utf-8")


def backup_path(deployed_root: Path, promoted_at: datetime) -> Path:
    stamp = promoted_at.strftime("%Y%m%dT%H%M%SZ")
    return deployed_root.with_name(f"{deployed_root.name}_backup_{stamp}")


def promote_candidate(
    candidate_root: Path,
    deployed_root: Path,
    *,
    approval_note: str | None = None,
) -> dict[str, Any]:
    validation = validate_candidate(candidate_root, load_models=True)
    promoted_at = datetime.now(timezone.utc)
    staging_root = deployed_root.with_name(f".{deployed_root.name}.staging")
    backup_root = backup_path(deployed_root, promoted_at)
    if backup_root.exists():
        raise FileExistsError(f"Backup path already exists: {backup_root}")

    copy_runtime_artifacts(candidate_root, staging_root)
    validate_candidate(staging_root, load_models=True)

    moved_deployed = False
    try:
        if deployed_root.exists():
            deployed_root.rename(backup_root)
            moved_deployed = True
        staging_root.rename(deployed_root)
    except Exception:
        if staging_root.exists():
            shutil.rmtree(staging_root)
        if moved_deployed and backup_root.exists() and not deployed_root.exists():
            backup_root.rename(deployed_root)
        raise

    result = {
        "status": "promoted",
        "promoted_at": promoted_at.isoformat(),
        "candidate_root": str(candidate_root),
        "deployed_root": str(deployed_root),
        "backup_root": str(backup_root) if moved_deployed else None,
        "approval_note": approval_note,
        "validation": validation,
    }
    result_path = deployed_root / "promotion.json"
    result_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    args = parse_args()
    if args.execute:
        result = promote_candidate(
            args.candidate_root,
            args.deployed_root,
            approval_note=args.approval_note,
        )
    else:
        result = {
            "status": "validated",
            "execute": False,
            "validation": validate_candidate(args.candidate_root, load_models=True),
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
