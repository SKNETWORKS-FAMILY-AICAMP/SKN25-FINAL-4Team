from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from api.main import app
from api.routers import model_artifacts, model_runs, model_training
from src.forecasting.import_pmax import operations, promotion, validation
from tests.test_import_pmax_promotion import write_artifact_root


TOKEN = "test-token"


def candidate_archive(source: Path, run_id: str) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        archive.add(source, arcname=run_id)
    return output.getvalue()


class ModelOperationsApiTest(unittest.TestCase):
    def test_upload_validate_and_promote_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            candidates = root / "candidates"
            incoming = root / "incoming"
            deployed = root / "deployed"
            archives = root / "archives"
            write_artifact_root(source, "candidate")
            write_artifact_root(
                deployed,
                "deployed",
                deployment_run_id="active-run",
            )
            archive = candidate_archive(source, "api-run")

            with (
                mock.patch.dict(os.environ, {"ARTIFACT_UPLOAD_TOKEN": TOKEN}),
                mock.patch.object(model_artifacts, "CANDIDATE_DIR", candidates),
                mock.patch.object(model_artifacts, "INCOMING_DIR", incoming),
                mock.patch.object(model_artifacts, "PROJECT_ROOT", root),
                mock.patch.object(model_runs, "CANDIDATE_DIR", candidates),
                mock.patch.object(model_runs, "DEPLOYED_ROOT", deployed),
                mock.patch.object(model_runs, "ARCHIVES_DIR", archives),
                mock.patch.object(operations, "ARCHIVES_ROOT", archives),
                mock.patch.object(promotion.operations, "ARCHIVES_ROOT", archives),
                mock.patch.object(
                    promotion,
                    "run_inference_smoke",
                    return_value={
                        "result": "pass",
                        "logical_meter_count": 4,
                        "prediction_row_count": 16,
                    },
                ),
            ):
                client = TestClient(app)
                headers = {"Authorization": f"Bearer {TOKEN}"}
                upload = client.post(
                    "/model-artifacts/upload",
                    headers=headers,
                    data={"run_id": "api-run", "overwrite": "false"},
                    files={"file": ("api-run.tar.gz", archive, "application/gzip")},
                )
                self.assertEqual(upload.status_code, 200, upload.text)
                self.assertEqual(upload.json()["next_action"], "validate")

                checked = client.post(
                    "/model-runs/api-run/validate",
                    headers=headers,
                )
                self.assertEqual(checked.status_code, 200, checked.text)
                self.assertEqual(checked.json()["status"], "pass")

                promoted = client.post(
                    "/model-runs/api-run/promote",
                    headers=headers,
                    params={
                        "confirm": "true",
                        "approval_note": "api test",
                    },
                )
                self.assertEqual(promoted.status_code, 200, promoted.text)
                self.assertEqual(promoted.json()["status"], "promoted")
                self.assertTrue((deployed / "promotion.json").is_file())
                self.assertTrue(
                    (deployed / validation.DEPLOYMENT_METRICS_FILENAME).is_file()
                )
                self.assertTrue(promoted.json()["candidate_deleted"])
                self.assertFalse((candidates / "api-run").exists())

                status = client.get(
                    "/model-runs/api-run",
                    headers=headers,
                )
                self.assertEqual(status.status_code, 200, status.text)
                self.assertEqual(status.json()["next_action"], "completed")
                self.assertEqual(status.json()["promoted"]["run_id"], "api-run")

                backup_root = Path(promoted.json()["backup_root"])
                rolled_back = client.post(
                    "/model-runs/rollback",
                    headers=headers,
                    params={
                        "archive_name": backup_root.name,
                        "confirm": "true",
                        "approval_note": "api rollback test",
                    },
                )
                self.assertEqual(rolled_back.status_code, 200, rolled_back.text)
                self.assertEqual(rolled_back.json()["status"], "rolled_back")
                self.assertTrue((deployed / "rollback.json").is_file())

    def test_upload_rejects_path_traversal_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = root / "candidates"
            incoming = root / "incoming"
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                info = tarfile.TarInfo("../escape.txt")
                payload = b"bad"
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

            with (
                mock.patch.dict(os.environ, {"ARTIFACT_UPLOAD_TOKEN": TOKEN}),
                mock.patch.object(model_artifacts, "CANDIDATE_DIR", candidates),
                mock.patch.object(model_artifacts, "INCOMING_DIR", incoming),
            ):
                client = TestClient(app)
                response = client.post(
                    "/model-artifacts/upload",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                    data={"run_id": "unsafe-run"},
                    files={
                        "file": (
                            "unsafe-run.tar.gz",
                            output.getvalue(),
                            "application/gzip",
                        )
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertFalse((root / "escape.txt").exists())

    def test_model_routes_require_bearer_token(self) -> None:
        with mock.patch.dict(os.environ, {"ARTIFACT_UPLOAD_TOKEN": TOKEN}):
            response = TestClient(app).get("/model-runs/missing")
        self.assertEqual(response.status_code, 401)

    def test_training_start_and_status_use_runpod_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            calls: list[tuple[str, str, dict | None, str | None]] = []

            def fake_runpod(
                method: str,
                path: str,
                payload: dict | None = None,
                *,
                endpoint_id: str | None = None,
            ) -> dict:
                calls.append((method, path, payload, endpoint_id))
                if method == "POST":
                    return {"id": "job-123", "status": "IN_QUEUE"}
                return {
                    "id": "job-123",
                    "status": "COMPLETED",
                    "output": {
                        "status": "uploaded",
                        "run_id": "api-training-run",
                        "upload": {
                            "status": "uploaded",
                            "run_id": "api-training-run",
                        },
                    },
                }

            env = {
                "ARTIFACT_UPLOAD_TOKEN": TOKEN,
                "RUNPOD_ENDPOINT_ID": "endpoint-123",
                "RUNPOD_API_KEY": "runpod-key",
                "RUNPOD_ARTIFACT_UPLOAD_URL": (
                    "https://pmax.example/model-artifacts/upload"
                ),
            }
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch.object(model_training, "TRAINING_JOBS_DIR", jobs),
                mock.patch.object(
                    model_training,
                    "_call_runpod",
                    side_effect=fake_runpod,
                ),
            ):
                client = TestClient(app)
                headers = {"Authorization": f"Bearer {TOKEN}"}
                started = client.post(
                    "/training/start",
                    headers=headers,
                    json={
                        "run_id": "api-training-run",
                        "seed": 7,
                    },
                )
                self.assertEqual(started.status_code, 200, started.text)
                self.assertEqual(started.json()["job_id"], "job-123")
                self.assertTrue((jobs / "job-123.json").is_file())

                status = client.get(
                    "/training/job-123/status",
                    headers=headers,
                )
                self.assertEqual(status.status_code, 200, status.text)
                self.assertEqual(status.json()["next_action"], "validate")

            start_payload = calls[0][2]
            self.assertEqual(calls[0][:2], ("POST", "/run"))
            self.assertEqual(calls[0][3], "endpoint-123")
            self.assertEqual(
                start_payload["input"]["upload_url"],
                "https://pmax.example/model-artifacts/upload",
            )
            self.assertEqual(calls[1][:2], ("GET", "/status/job-123"))


if __name__ == "__main__":
    unittest.main()
