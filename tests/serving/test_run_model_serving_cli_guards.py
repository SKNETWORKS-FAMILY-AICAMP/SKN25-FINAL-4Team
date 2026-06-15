from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_runner_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "serving" / "run_model_serving.py"
    spec = importlib.util.spec_from_file_location("run_model_serving_cli_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_anomaly_prediction_payload_and_reference_read_modes_are_mutually_exclusive(monkeypatch, capsys) -> None:
    module = _load_runner_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_model_serving.py",
            "--base-ts",
            "2024-01-01T00:00:00+00:00",
            "--pmax-artifact-root",
            "/tmp/pmax",
            "--anomaly-predictions-path",
            "/tmp/payload.json",
            "--enable-anomaly-reference-read",
            "--anomaly-artifact-root",
            "/tmp/anomaly",
            "--json",
        ],
    )

    exit_code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["blocked"] is True
    assert "choose either" in payload["error"]
    assert payload["write_attempted"] is False


def test_anomaly_reference_read_requires_artifact_root_before_db_connection(monkeypatch, capsys) -> None:
    module = _load_runner_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_model_serving.py",
            "--base-ts",
            "2024-01-01T00:00:00+00:00",
            "--pmax-artifact-root",
            "/tmp/pmax",
            "--enable-anomaly-reference-read",
            "--json",
        ],
    )

    exit_code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["blocked"] is True
    assert "--anomaly-artifact-root" in payload["error"]
    assert payload["write_attempted"] is False


def test_hybrid_pmax_mode_is_no_write_only_before_db_connection(monkeypatch, capsys) -> None:
    module = _load_runner_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_model_serving.py",
            "--base-ts",
            "2024-01-01T00:00:00+00:00",
            "--pmax-artifact-root",
            "/tmp/pmax",
            "--allow-harmonized-observed-input",
            "--execute-write",
            "--json",
        ],
    )

    exit_code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["blocked"] is True
    assert "--allow-nonprod-warm-start-write" in payload["error"]
    assert payload["pmax_source_mode"] == "hybrid_warm_start"
    assert payload["write_attempted"] is False


def test_anomaly_reference_mode_is_no_write_only_before_db_connection(monkeypatch, capsys) -> None:
    module = _load_runner_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_model_serving.py",
            "--base-ts",
            "2024-01-01T00:00:00+00:00",
            "--pmax-artifact-root",
            "/tmp/pmax",
            "--enable-anomaly-reference-read",
            "--anomaly-artifact-root",
            "/tmp/anomaly",
            "--execute-write",
            "--json",
        ],
    )

    exit_code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["blocked"] is True
    assert "--allow-nonprod-warm-start-write" in payload["error"]
    assert payload["anomaly_source_mode"] == "reference_backfill"
    assert payload["write_attempted"] is False


def test_nonprod_warm_start_write_override_requires_cli_env_and_nonprod_environment() -> None:
    module = _load_runner_module()
    flag = module.NONPROD_WARM_START_WRITE_ENV_FLAG

    allowed_args = SimpleNamespace(allow_nonprod_warm_start_write=True, environment="nonprod_reference_warm_start")
    production_args = SimpleNamespace(allow_nonprod_warm_start_write=True, environment="production")
    missing_cli_args = SimpleNamespace(allow_nonprod_warm_start_write=False, environment="nonprod_reference_warm_start")

    assert module._nonprod_warm_start_write_allowed(args=allowed_args, env={flag: "1"}) is True
    assert module._nonprod_warm_start_write_allowed(args=allowed_args, env={}) is False
    assert module._nonprod_warm_start_write_allowed(args=missing_cli_args, env={flag: "1"}) is False
    assert module._nonprod_warm_start_write_allowed(args=production_args, env={flag: "1"}) is False


def test_reference_backfill_anomaly_payload_preserves_source_mode_and_feature_count() -> None:
    module = _load_runner_module()
    payload = (
        {
            "meter_urn": "H1.K11",
            "source_mode": "reference_backfill",
            "source_input_refs": ["reference.corrected_resampled_1h:H1.K11:2024-01-01T00:00:00+09:00:2024-01-01T07:00:00+09:00"],
            "reference_input_row_count": "17",
        },
        {
            "meter_urn": "H1.K12",
            "source_mode": "reference_backfill",
            "source_input_refs": ["reference.corrected_resampled_1h:H1.K12:2024-01-01T00:00:00+09:00:2024-01-01T07:00:00+09:00"],
            "reference_input_row_count": 19,
        },
    )

    assert module._anomaly_payload_source_mode(payload, fallback="external_predictions_payload") == "reference_backfill"
    assert module._anomaly_payload_source_table(payload, source_mode="reference_backfill") == "reference.corrected_resampled_1h"
    assert module._anomaly_payload_reference_input_count(payload) == 36
