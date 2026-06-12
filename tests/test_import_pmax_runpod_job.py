from __future__ import annotations

import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runpod_job import handler
from src.forecasting.import_pmax import training
from tests.test_import_pmax_promotion import write_artifact_root


class RunPodJobTest(unittest.TestCase):
    def test_build_train_command_uses_gpu_candidate_output(self) -> None:
        request = {
            "run_id": "run_test",
            "meters": ["V.Z81"],
            "seed": 7,
        }
        command = handler._build_train_command(
            request,
            Path("/tmp/candidates/run_test"),
        )
        self.assertIn("scripts.forecasting.train_import_pmax", command)
        self.assertIn("gpu", command)
        self.assertIn("/tmp/candidates/run_test", command)
        self.assertIn("V.Z81", command)

    def test_input_rejects_unapproved_upload_host(self) -> None:
        job = {
            "input": {
                "run_id": "run_test",
                "upload_url": "https://bad.example/model-artifacts/upload",
            }
        }
        env = {
            "ARTIFACT_UPLOAD_TOKEN": "token",
            "RUNPOD_ALLOWED_UPLOAD_HOSTS": "good.example",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(handler.JobInputError):
                handler._read_input(job)

    def test_input_accepts_full_training_request(self) -> None:
        job = {
            "input": {
                "run_id": "run_test",
                "upload_url": "https://good.example/model-artifacts/upload",
                "seed": 42,
            }
        }
        env = {
            "ARTIFACT_UPLOAD_TOKEN": "token",
            "RUNPOD_ALLOWED_UPLOAD_HOSTS": "good.example",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            request = handler._read_input(job)
        self.assertEqual(request["run_id"], "run_test")
        self.assertIsNone(request["meters"])
        self.assertEqual(request["seed"], 42)

    def test_handler_trains_archives_and_uploads_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = root / "candidates"
            logs = root / "logs"
            uploaded_members: list[str] = []

            def fake_training(
                command: list[str],
                stdout_path: Path,
                stderr_path: Path,
                timeout_seconds: int,
            ) -> int:
                del timeout_seconds
                output_dir = Path(command[command.index("--output-dir") + 1])
                write_artifact_root(output_dir, "runpod")
                stdout_path.write_text("training complete\n", encoding="utf-8")
                stderr_path.write_text("", encoding="utf-8")
                return 0

            def fake_upload(request: dict, archive_path: Path) -> dict:
                self.assertEqual(request["run_id"], "runpod-e2e")
                with tarfile.open(archive_path) as archive:
                    uploaded_members.extend(archive.getnames())
                return {
                    "status": "uploaded",
                    "run_id": request["run_id"],
                    "size_bytes": archive_path.stat().st_size,
                }

            job = {
                "input": {
                    "run_id": "runpod-e2e",
                    "upload_url": (
                        "https://good.example/model-artifacts/upload"
                    ),
                }
            }
            env = {
                "ARTIFACT_UPLOAD_TOKEN": "token",
                "RUNPOD_ALLOWED_UPLOAD_HOSTS": "good.example",
            }
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch.object(handler, "CANDIDATE_DIR", candidates),
                mock.patch.object(handler, "DEFAULT_LOG_DIR", logs),
                mock.patch.object(
                    handler,
                    "_run_process_to_logs",
                    side_effect=fake_training,
                ),
                mock.patch.object(
                    handler,
                    "_upload_archive",
                    side_effect=fake_upload,
                ),
            ):
                result = handler.handler(job)

            self.assertEqual(result["status"], "uploaded")
            self.assertEqual(result["meter_count"], 4)
            self.assertTrue(
                any(
                    member.endswith(
                        "input_24h/predict_60min/V.Z81/v29/manifest.json"
                    )
                    for member in uploaded_members
                )
            )

    def test_database_engine_is_forced_read_only(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {
                    "DB_HOST": "db.example",
                    "DB_PORT": "5432",
                    "DB_NAME": "ems",
                    "DB_USER": "reader",
                    "DB_PASS": "secret",
                },
                clear=False,
            ),
            mock.patch.object(training, "create_engine") as create_engine,
        ):
            training.build_engine()

        self.assertEqual(
            create_engine.call_args.kwargs["connect_args"]["options"],
            "-c default_transaction_read_only=on",
        )


if __name__ == "__main__":
    unittest.main()
