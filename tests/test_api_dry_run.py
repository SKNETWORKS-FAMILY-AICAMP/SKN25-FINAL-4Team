from __future__ import annotations

import pytest

from cms.service import api


def test_latency_probe_wraps_plan_without_writes() -> None:
    ticks = iter((10.0, 10.025))

    result = api.make_latency_probe_payload(
        {"mode": "live", "table": "canonical.measurement_15min", "limit": 2},
        monotonic=lambda: next(ticks),
    )

    assert result["route"] == "/latency/probe"
    assert result["dry_run"] is True
    assert result["side_effects_executed"] is False
    assert result["writes_allowed"] is False
    assert result["evidence_level"] == "api_dry_run"
    assert "reference.corrected_resampled" in result["source_boundary"]
    assert result["latency_ms"] == pytest.approx(25.0)
    assert result["plan"]["result"]["points"] == []
    assert result["plan"]["result"]["plan"]["writes_allowed"] is False


def test_report_email_dry_run_validates_and_returns_queue_metadata() -> None:
    result = api.make_report_email_dry_run_payload(
        {
            "recipients": ["ops@example.com", "qa@example.com"],
            "subject": "Daily CMS report",
            "body": "No anomalies detected.",
        }
    )

    assert result == {
        "route": "/reports/email/dry-run",
        "status": "queued",
        "dry_run": True,
        "side_effects_executed": False,
        "send_attempted": False,
        "writes_allowed": False,
        "evidence_level": "api_dry_run",
        "recipients": ["ops@example.com", "qa@example.com"],
        "recipient_count": 2,
        "subject": "Daily CMS report",
        "body_bytes": 22,
        "queue": "local-dry-run",
    }


def test_report_email_dry_run_rejects_invalid_payload() -> None:
    with pytest.raises(ValueError, match="valid recipient"):
        api.make_report_email_dry_run_payload({"recipients": ["not-email"], "subject": "Report", "body": "Body"})

    with pytest.raises(ValueError, match="subject"):
        api.make_report_email_dry_run_payload({"recipients": ["ops@example.com"], "subject": " ", "body": "Body"})

    with pytest.raises(ValueError, match="control"):
        api.make_report_email_dry_run_payload({"recipients": ["ops@example.com"], "subject": "Report\nBcc: x@example.com", "body": "Body"})

    with pytest.raises(ValueError, match="body"):
        api.make_report_email_dry_run_payload({"recipients": ["ops@example.com"], "subject": "Report", "body": ""})


def test_routes_and_fallback_include_dry_run_contracts() -> None:
    required_paths = {
        "/health",
        "/contracts",
        "/live-replay/plan",
        "/latency/probe",
        "/reports/email/dry-run",
    }

    assert required_paths.issubset({path for _, path, _ in api.ROUTES})
    skeleton = api.ApiSkeleton()
    assert required_paths.issubset(set(skeleton.route_paths()))
