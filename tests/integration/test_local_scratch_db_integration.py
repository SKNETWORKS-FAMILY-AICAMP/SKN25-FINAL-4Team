from __future__ import annotations

import importlib.util
import os
import subprocess

import pytest

RUN_ENV = "CMS_RUN_LOCAL_SCRATCH_DB_INTEGRATION"


@pytest.mark.integration
def test_local_docker_scratch_db_integration_round_trips_real_isolated_rows() -> None:
    if os.environ.get(RUN_ENV) != "1":
        pytest.skip(f"set {RUN_ENV}=1 to run Docker-backed local scratch DB integration")
    if importlib.util.find_spec("psycopg") is None:
        pytest.skip("psycopg is required for the local PostgreSQL scratch integration")
    if subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=30).returncode != 0:
        pytest.skip("Docker daemon is required for the local scratch DB integration")

    from scripts.local_scratch_db_integration import run_local_scratch_db_integration

    report = run_local_scratch_db_integration(
        test_run_id="localdb_tdd_20260531",
        postgres_port=55432,
        mongo_port=27028,
        cleanup=True,
        cleanup_containers=True,
    )

    assert report["claims"] == {
        "scratch_db_integration": "local only",
        "production_ready": False,
        "paper_complete": False,
        "aws_untouched": True,
    }
    assert report["postgres_schema"] == "cms_scratch_localdb_tdd_20260531"
    assert report["mongo_collection"] == "test_measurement_raw_localdb_tdd_20260531"
    assert report["postgres_row_counts"] == {
        "measurement_1min": 60,
        "measurement_5min": 12,
        "measurement_15min": 4,
        "measurement_1h": 1,
    }
    assert report["mongo_raw_count"] == 60
    assert report["adapter_result"]["db_writes_executed"] is True
    assert report["adapter_result"]["real_db_writes_executed"] is True
    assert report["postgres_public_canonical_tables"] == []
    assert report["cleanup"]["postgres_schema_dropped"] is True
    assert report["cleanup"]["mongo_collection_dropped"] is True
    assert report["cleanup"]["postgres_container_removed"] is True
    assert report["cleanup"]["mongo_container_removed"] is True
