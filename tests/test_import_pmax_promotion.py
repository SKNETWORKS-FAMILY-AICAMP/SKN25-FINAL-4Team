from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import joblib
import pandas as pd

from src.forecasting.import_pmax import inference, promotion, training, validation


def summary_rows(rmse_offset: float = 0.0) -> list[dict]:
    rows = []
    for index, meter in enumerate(training.LOGICAL_METERS):
        model_rmse = 100.0 + index + rmse_offset
        model_mae = 70.0 + index + rmse_offset
        rows.append(
            {
                "meter": meter,
                "model_mae": model_mae,
                "model_rmse": model_rmse,
                "persistence_mae": model_mae + 20,
                "persistence_rmse": model_rmse + 30,
                "rmse_improvement_pct": 20.0,
            }
        )
    return rows


def write_artifact_root(
    root: Path,
    marker: str,
    *,
    rmse_offset: float = 0.0,
    deployment_run_id: str | None = None,
) -> None:
    for meter in training.LOGICAL_METERS:
        model_dir, manifest_path, weights_path = inference.artifact_paths(root, meter)
        model_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        for version in inference.EXPECTED_CANDIDATES:
            joblib.dump(
                {"marker": marker, "version": version},
                model_dir / f"{version}.joblib",
            )
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

    rows = summary_rows(rmse_offset)
    pd.DataFrame(rows).to_csv(root / validation.SUMMARY_FILENAME, index=False)
    if deployment_run_id:
        (root / validation.DEPLOYMENT_METRICS_FILENAME).write_text(
            json.dumps({"run_id": deployment_run_id, "meters": rows}),
            encoding="utf-8",
        )


def passing_smoke(_: Path) -> dict:
    return {
        "result": "pass",
        "tested_at": "2026-06-12T00:00:00+00:00",
        "requested_as_of": "2026-06-11T00:00:00+00:00",
        "logical_meter_count": 4,
        "prediction_row_count": 16,
    }


class PromotionTest(unittest.TestCase):
    def test_validation_does_not_change_deployed_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            deployed = root / "deployed"
            write_artifact_root(candidate, "candidate")
            write_artifact_root(deployed, "deployed", deployment_run_id="active")

            result = promotion.validate_candidate(
                candidate,
                deployed_root=deployed,
                write_result=True,
            )

            self.assertEqual(result["logical_meter_count"], 4)
            self.assertEqual(result["result"], "pass")
            model_dir, _, _ = inference.artifact_paths(deployed, "V.Z81")
            self.assertEqual(
                joblib.load(model_dir / "v20.joblib")["marker"],
                "deployed",
            )

    def test_validation_warns_when_active_rmse_degrades_over_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            deployed = root / "deployed"
            write_artifact_root(candidate, "candidate", rmse_offset=10)
            write_artifact_root(deployed, "deployed", deployment_run_id="active")

            result = validation.validate_candidate(
                candidate,
                deployed,
                run_id="warn-run",
                write_result=True,
            )

            self.assertEqual(result["result"], "warn")
            self.assertEqual(result["degraded_count"], 4)

    def test_promotion_replaces_deployed_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            deployed = root / "deployed"
            archives = root / "archives"
            write_artifact_root(candidate, "candidate")
            write_artifact_root(deployed, "deployed", deployment_run_id="active")
            validation.validate_candidate(
                candidate,
                deployed,
                run_id="candidate-run",
                write_result=True,
            )

            result = promotion.promote_candidate(
                candidate,
                deployed,
                approval_note="test",
                archives_root=archives,
                smoke_runner=passing_smoke,
            )

            deployed_models, _, _ = inference.artifact_paths(deployed, "V.Z81")
            backup_models, _, _ = inference.artifact_paths(
                Path(result["backup_root"]),
                "V.Z81",
            )
            self.assertEqual(
                joblib.load(deployed_models / "v20.joblib")["marker"],
                "candidate",
            )
            self.assertEqual(
                joblib.load(backup_models / "v20.joblib")["marker"],
                "deployed",
            )
            self.assertTrue((deployed / "promotion.json").is_file())
            self.assertTrue((deployed / validation.DEPLOYMENT_METRICS_FILENAME).is_file())
            self.assertEqual(result["inference_smoke"]["result"], "pass")
            self.assertTrue(result["candidate_deleted"])
            self.assertFalse(candidate.exists())

    def test_promotion_rejects_candidate_changed_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            deployed = root / "deployed"
            write_artifact_root(candidate, "candidate")
            write_artifact_root(deployed, "deployed", deployment_run_id="active")
            validation.validate_candidate(
                candidate,
                deployed,
                run_id="candidate-run",
                write_result=True,
            )
            model_dir, _, _ = inference.artifact_paths(candidate, "V.Z81")
            joblib.dump(
                {"marker": "changed"},
                model_dir / "v20.joblib",
            )

            with self.assertRaises(validation.CandidateValidationError):
                promotion.promote_candidate(
                    candidate,
                    deployed,
                    archives_root=root / "archives",
                )

    def test_failed_swap_restores_previous_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            deployed = root / "deployed"
            archives = root / "archives"
            write_artifact_root(candidate, "candidate")
            write_artifact_root(deployed, "deployed", deployment_run_id="active")
            validation.validate_candidate(
                candidate,
                deployed,
                run_id="candidate-run",
                write_result=True,
            )

            original_rename = Path.rename

            def fail_staging_swap(path: Path, target: Path):
                if path.name.startswith(".deployed.staging"):
                    raise OSError("simulated staging swap failure")
                return original_rename(path, target)

            with mock.patch.object(Path, "rename", autospec=True, side_effect=fail_staging_swap):
                with self.assertRaises(OSError):
                    promotion.promote_candidate(
                        candidate,
                        deployed,
                        archives_root=archives,
                    )

            model_dir, _, _ = inference.artifact_paths(deployed, "V.Z81")
            self.assertEqual(
                joblib.load(model_dir / "v20.joblib")["marker"],
                "deployed",
            )
            self.assertFalse(any(archives.iterdir()))

    def test_failed_promotion_metadata_write_restores_previous_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            deployed = root / "deployed"
            archives = root / "archives"
            write_artifact_root(candidate, "candidate")
            write_artifact_root(deployed, "deployed", deployment_run_id="active")
            validation.validate_candidate(
                candidate,
                deployed,
                run_id="candidate-run",
                write_result=True,
            )

            original_write_text = Path.write_text

            def fail_promotion_metadata(path: Path, *args, **kwargs):
                if path.name == "promotion.json":
                    raise OSError("simulated promotion metadata failure")
                return original_write_text(path, *args, **kwargs)

            with mock.patch.object(
                Path,
                "write_text",
                autospec=True,
                side_effect=fail_promotion_metadata,
            ):
                with self.assertRaises(OSError):
                    promotion.promote_candidate(
                        candidate,
                        deployed,
                        archives_root=archives,
                    )

            model_dir, _, _ = inference.artifact_paths(deployed, "V.Z81")
            self.assertEqual(
                joblib.load(model_dir / "v20.joblib")["marker"],
                "deployed",
            )
            self.assertFalse(any(archives.iterdir()))

    def test_smoke_failure_restores_previous_deployment_and_keeps_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            deployed = root / "deployed"
            archives = root / "archives"
            write_artifact_root(candidate, "candidate")
            write_artifact_root(deployed, "deployed", deployment_run_id="active")
            validation.validate_candidate(
                candidate,
                deployed,
                run_id="candidate-run",
                write_result=True,
            )

            def failing_smoke(_: Path) -> dict:
                raise ValueError("simulated inference failure")

            with self.assertRaises(promotion.PromotionSmokeError) as raised:
                promotion.promote_candidate(
                    candidate,
                    deployed,
                    archives_root=archives,
                    smoke_runner=failing_smoke,
                )

            model_dir, _, _ = inference.artifact_paths(deployed, "V.Z81")
            self.assertEqual(
                joblib.load(model_dir / "v20.joblib")["marker"],
                "deployed",
            )
            self.assertTrue(candidate.exists())
            self.assertTrue((candidate / "promotion_failure.json").is_file())
            self.assertTrue(any(archives.iterdir()))
            self.assertTrue(
                raised.exception.result["automatic_rollback"][
                    "restored_previous_deployment"
                ]
            )

    def test_candidate_cleanup_failure_does_not_reverse_completed_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            deployed = root / "deployed"
            archives = root / "archives"
            write_artifact_root(candidate, "candidate")
            write_artifact_root(deployed, "deployed", deployment_run_id="active")
            validation.validate_candidate(
                candidate,
                deployed,
                run_id="candidate-run",
                write_result=True,
            )

            original_rmtree = shutil.rmtree

            def fail_candidate_cleanup(path, *args, **kwargs):
                if Path(path) == candidate:
                    raise OSError("simulated candidate cleanup failure")
                return original_rmtree(path, *args, **kwargs)

            with mock.patch.object(
                shutil,
                "rmtree",
                side_effect=fail_candidate_cleanup,
            ):
                result = promotion.promote_candidate(
                    candidate,
                    deployed,
                    archives_root=archives,
                    smoke_runner=passing_smoke,
                )

            model_dir, _, _ = inference.artifact_paths(deployed, "V.Z81")
            self.assertEqual(
                joblib.load(model_dir / "v20.joblib")["marker"],
                "candidate",
            )
            self.assertFalse(result["candidate_deleted"])
            self.assertIn("candidate_cleanup_warning", result)
            self.assertTrue(candidate.exists())
            self.assertTrue((deployed / "promotion.json").is_file())

    def test_inference_smoke_validates_all_four_meter_predictions(self) -> None:
        predictions = [
            {
                "predicted_import_p_max": float(index),
                "raw_model_prediction": float(index),
            }
            for index in range(4)
        ]
        batch = {
            "status": "success",
            "requested_as_of": "2026-06-11T00:00:00+00:00",
            "logical_meter_count": 4,
            "prediction_row_count": 16,
            "results": [
                {
                    "logical_meter": meter,
                    "predictions": predictions,
                }
                for meter in training.LOGICAL_METERS
            ],
        }

        with mock.patch.object(
            promotion.batch_inference,
            "run_batch",
            return_value=batch,
        ) as run_batch:
            result = promotion.run_inference_smoke(Path("/models"))

        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["logical_meter_count"], 4)
        self.assertEqual(result["prediction_row_count"], 16)
        run_batch.assert_called_once_with(
            table_name=training.DEFAULT_TABLE,
            model_root=Path("/models"),
            requested_as_of=None,
            lookback_days=inference.DEFAULT_LOOKBACK_DAYS,
        )

    def test_explicit_rollback_restores_archive_and_backs_up_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_base = root / "archives"
            archive = archive_base / "previous"
            deployed = root / "deployed"
            write_artifact_root(
                archive,
                "previous",
                deployment_run_id="previous-run",
            )
            write_artifact_root(
                deployed,
                "current",
                deployment_run_id="current-run",
            )

            result = promotion.rollback_deployment(
                archive,
                deployed,
                approval_note="rollback test",
            )

            deployed_models, _, _ = inference.artifact_paths(deployed, "V.Z81")
            replaced_models, _, _ = inference.artifact_paths(
                Path(result["replaced_deployment_backup"]),
                "V.Z81",
            )
            self.assertEqual(
                joblib.load(deployed_models / "v20.joblib")["marker"],
                "previous",
            )
            self.assertEqual(
                joblib.load(replaced_models / "v20.joblib")["marker"],
                "current",
            )
            self.assertEqual(
                Path(result["replaced_deployment_backup"]).parent,
                archive_base,
            )
            self.assertTrue((deployed / "rollback.json").is_file())


if __name__ == "__main__":
    unittest.main()
