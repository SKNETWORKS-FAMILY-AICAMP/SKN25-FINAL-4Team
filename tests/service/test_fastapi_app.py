from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
testclient = pytest.importorskip("fastapi.testclient")

from cms.service import api


def test_create_app_registers_real_fastapi_routes() -> None:
    app = api.create_app()

    assert isinstance(app, fastapi.FastAPI)
    route_paths = {getattr(route, "path", None) for route in app.routes}
    assert {
        "/",
        "/health",
        "/contracts",
        "/live-replay/plan",
        "/latency/probe",
        "/query/plan",
        "/reports/email/dry-run",
        "/chat/route",
        "/ops/jobs/{job_id}",
        "/ops/jobs/{job_id}/run",
        "/ops/approvals/{job_id}",
    }.issubset(route_paths)


def test_fastapi_routes_return_dry_run_payloads() -> None:
    client = testclient.TestClient(api.create_app())

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["writes_allowed"] is False

    index = client.get("/")
    assert index.status_code == 200
    assert index.json()["docs"] == "/docs"
    assert {route["path"] for route in index.json()["routes"]} >= {"/health", "/query/plan"}

    plan = client.post("/live-replay/plan", json={"table": "canonical.measurement_15min", "limit": 2})
    assert plan.status_code == 200
    assert plan.json()["result"]["plan"]["writes_allowed"] is False

    query = client.post("/query/plan", json={"text": "H2.Z64의 2023년 8월 평균 전력 사용량"})
    assert query.status_code == 200
    query_payload = query.json()
    assert query_payload["route"] == "evidence_answer"
    assert query_payload["table"] == "canonical.measurement_15min"
    assert query_payload["params"]["meter_urns"] == ["H2.Z64"]
    assert query_payload["writes_allowed"] is False
    assert "H2.Z64" not in query_payload["sql"]

    email = client.post(
        "/reports/email/dry-run",
        json={"recipients": ["ops@example.com"], "subject": "Daily report", "body": "No anomalies."},
    )
    assert email.status_code == 200
    assert email.json()["send_attempted"] is False


def test_fastapi_routes_map_contract_errors_to_http_statuses() -> None:
    client = testclient.TestClient(api.create_app())

    invalid_plan = client.post("/live-replay/plan", json={"table": "reference.corrected_resampled_15min"})
    assert invalid_plan.status_code == 400
    assert "unsupported canonical table" in invalid_plan.json()["detail"]

    invalid_query = client.post("/query/plan", json={"text": "delete canonical rows for H2.Z64 in 2023"})
    assert invalid_query.status_code == 400
    assert "approval_required" in invalid_query.json()["detail"]

    missing_job = client.get("/ops/jobs/unknown_job")
    assert missing_job.status_code == 404


def test_fastapi_review_job_lifecycle_stays_dry_run() -> None:
    client = testclient.TestClient(api.create_app())

    submitted = client.post("/chat/route", json={"text": "show H2.Z64 power usage", "context": {"coverage_ratio": 0.95}})
    assert submitted.status_code == 200
    submitted_payload = submitted.json()
    assert submitted_payload["mode"] == "job"
    assert submitted_payload["route"] == "evidence_answer"

    job_id = submitted_payload["job_id"]
    processed = client.post(f"/ops/jobs/{job_id}/run")
    assert processed.status_code == 200
    processed_payload = processed.json()
    assert processed_payload["dry_run"] is True
    assert processed_payload["side_effects_executed"] is False
    assert processed_payload["response"]["route"] == "evidence_answer"
