from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import joblib
import pandas as pd

from src.forecasting.import_pmax import inference, promotion, training


def write_artifact_root(root: Path, marker: str) -> None:
    for meter in training.LOGICAL_METERS:
        model_dir, manifest_path, weights_path = inference.artifact_paths(root, meter)
        model_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        for version in inference.EXPECTED_CANDIDATES:
            joblib.dump({"marker": marker, "version": version}, model_dir / f"{version}.joblib")
        manifest_path.write_text(
            json.dumps(
                {
                    "logical_meter": meter,
                    "output_range": "60min",
                    "horizon_steps": 4,
                    "input_hours": 24,
                    "method": "v29",
                    "feature_columns": training.FEATURE_COLUMNS,
                    "candidate_versions": inference.EXPECTED_CANDIDATES,
                }
            ),
            encoding="utf-8",
        )
        pd.DataFrame(
            {
                "candidate_version": inference.EXPECTED_CANDIDATES,
                "weight": [0.25, 0.25, 0.25, 0.25],
            }
        ).to_csv(weights_path, index=False)


class PromotionTest(unittest.TestCase):
    def test_validation_does_not_change_deployed_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            deployed = root / "deployed"
            write_artifact_root(candidate, "candidate")
            write_artifact_root(deployed, "deployed")

            result = promotion.validate_candidate(candidate)

            self.assertEqual(result["logical_meter_count"], 4)
            model_dir, _, _ = inference.artifact_paths(deployed, "V.Z81")
            self.assertEqual(joblib.load(model_dir / "v20.joblib")["marker"], "deployed")

    def test_promotion_replaces_deployed_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            deployed = root / "deployed"
            write_artifact_root(candidate, "candidate")
            write_artifact_root(deployed, "deployed")

            result = promotion.promote_candidate(candidate, deployed, approval_note="test")

            deployed_models, _, _ = inference.artifact_paths(deployed, "V.Z81")
            backup_models, _, _ = inference.artifact_paths(
                Path(result["backup_root"]),
                "V.Z81",
            )
            self.assertEqual(joblib.load(deployed_models / "v20.joblib")["marker"], "candidate")
            self.assertEqual(joblib.load(backup_models / "v20.joblib")["marker"], "deployed")
            self.assertTrue((deployed / "promotion.json").is_file())


if __name__ == "__main__":
    unittest.main()
