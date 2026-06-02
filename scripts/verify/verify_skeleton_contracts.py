"""Smoke verification for CMS pre-model skeleton contracts.

Run from repository root:

    PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from cms.contracts.core import CANONICAL_MEASUREMENT_1MIN as CORE_CANONICAL_MEASUREMENT_1MIN
from cms.contracts.core import AgentRequest, LivePoint, LiveReplayPlan
from cms.contracts.core import MeasurementWindow as ReplayWindow
from cms.contracts.job import ApiJob, DataSplit, MeasurementLoadRun
from cms.contracts.measurement import (
    CANONICAL_MEASUREMENT_1H,
    CANONICAL_MEASUREMENT_1MIN,
    CANONICAL_MEASUREMENT_15MIN,
    MONGO_COLLECTIONS,
    MONGO_DB_NAME,
    POSTGRES_DB_NAME,
    MeasurementEvent,
    MeasurementWindow,
    default_latency_budget,
)
from cms.contracts.qa import MeasurementCheckResult, MeasurementQuarantineEvent
from cms.data.live_replay import InMemoryRecentCache, build_request, read_live_replay
from cms.service import api
from cms.workflow import airflow_skeleton, langgraph_skeleton, review_jobs


def main() -> None:
    assert POSTGRES_DB_NAME == "cms"
    assert MONGO_DB_NAME == "cms"

    health = api.health()
    assert health["status"] == "ok"
    assert health["writes_allowed"] is False
    assert health["mongo_role"] == "recent live/replay cache only"

    contract_payload = api.contracts()
    assert contract_payload["airflow"] == "disabled skeleton; no scheduling from this module"
    assert (
        contract_payload["langgraph"]
        == "optional async evidence/report/job/approval review layer; FastAPI router does primary routing"
    )
    assert contract_payload["mart_generation"] == "deferred"
    assert CORE_CANONICAL_MEASUREMENT_1MIN in contract_payload["canonical_source_tables"]
    assert CANONICAL_MEASUREMENT_1MIN in contract_payload["canonical_source_tables"]
    assert "reference.corrected_resampled_1h" in contract_payload["reference_tables"]
    assert "reference.corrected_resampled" in contract_payload["anomaly_source"]
    assert "read-only parameterized SELECT" in contract_payload["query_planner"]

    required_api_paths = {"/health", "/contracts", "/live-replay/plan", "/latency/probe", "/query/plan", "/reports/email/dry-run"}
    assert required_api_paths.issubset({path for _, path, _ in api.ROUTES})
    app = api.create_app()
    if isinstance(app, api.ApiSkeleton):
        assert required_api_paths.issubset(set(app.route_paths()))
        assert app.fastapi_available is False
    else:
        assert app.title == api.API_TITLE
        route_paths = {getattr(route, "path", None) for route in getattr(app, "routes", ())}
        assert required_api_paths.issubset(route_paths)

    plan_payload = api.make_plan_payload({"mode": "live", "table": CANONICAL_MEASUREMENT_15MIN, "limit": 2})
    assert plan_payload["result"]["points"] == []
    assert plan_payload["result"]["plan"]["writes_allowed"] is False
    assert plan_payload["mongo_read_skeleton"]["filter"]["source_table"] == CANONICAL_MEASUREMENT_15MIN
    assert plan_payload["mongo_read_skeleton"]["limit"] == 2

    ticks = iter((1.0, 1.01))
    latency_payload = api.make_latency_probe_payload(
        {"mode": "live", "table": CANONICAL_MEASUREMENT_15MIN, "limit": 2},
        monotonic=lambda: next(ticks),
    )
    assert latency_payload["route"] == "/latency/probe"
    assert latency_payload["dry_run"] is True
    assert latency_payload["side_effects_executed"] is False
    assert latency_payload["writes_allowed"] is False
    assert latency_payload["evidence_level"] == "api_dry_run"
    assert round(latency_payload["latency_ms"], 3) == 10.0
    assert latency_payload["plan"]["result"]["plan"]["writes_allowed"] is False

    query_plan_payload = api.make_query_plan_payload({"text": "H2.Z64 2023년 8월 평균 전력 사용량"})
    assert query_plan_payload["route"] == "evidence_answer"
    assert query_plan_payload["table"] == CANONICAL_MEASUREMENT_15MIN
    assert query_plan_payload["params"]["meter_urns"] == ["H2.Z64"]
    assert query_plan_payload["params"]["measurement"] == "W"
    assert query_plan_payload["writes_allowed"] is False
    assert query_plan_payload["side_effects_executed"] is False
    assert query_plan_payload["sql"].startswith("SELECT ")
    assert "H2.Z64" not in query_plan_payload["sql"]

    email_payload = api.make_report_email_dry_run_payload(
        {"recipients": "ops@example.com", "subject": "CMS report", "body": "Dry-run only."}
    )
    assert email_payload["route"] == "/reports/email/dry-run"
    assert email_payload["status"] == "queued"
    assert email_payload["dry_run"] is True
    assert email_payload["side_effects_executed"] is False
    assert email_payload["send_attempted"] is False
    assert email_payload["writes_allowed"] is False
    assert email_payload["evidence_level"] == "api_dry_run"
    assert email_payload["recipients"] == ["ops@example.com"]

    replay_start = datetime(2023, 1, 1, tzinfo=UTC)
    replay_end = datetime(2023, 1, 1, 0, 30, tzinfo=UTC)
    replay_plan = LiveReplayPlan(request=build_request(start_at=replay_start, end_at=replay_end, limit=10))
    assert replay_plan.mongo_filter()["ts"] == {"$gte": replay_start, "$lt": replay_end}
    cache = InMemoryRecentCache(
        points=(
            LivePoint("meter:a", replay_start, 1.0, CANONICAL_MEASUREMENT_15MIN),
            LivePoint("meter:a", replay_end, 2.0, CANONICAL_MEASUREMENT_15MIN),
        )
    )
    replay_result = read_live_replay(build_request(start_at=replay_start, end_at=replay_end, limit=10), cache=cache)
    assert [point.ts for point in replay_result.points] == [replay_start]
    try:
        ReplayWindow(table=CANONICAL_MEASUREMENT_15MIN, start_at=replay_start, end_at=replay_start)
    except ValueError:
        pass
    else:
        raise AssertionError("zero-length replay windows must be rejected")

    dag = airflow_skeleton.describe_dag()
    assert dag.dag_id == airflow_skeleton.DAG_ID
    assert dag.enabled is False
    assert dag.schedule is None
    assert dag.writes_allowed is False
    assert dag.mart_generation_deferred is True
    assert airflow_skeleton.make_airflow_dag() == dag
    task_contracts = airflow_skeleton.task_contracts()
    assert set(task_contracts) == set(airflow_skeleton.TASK_IDS)
    assert all(task["writes"] == [] for task in task_contracts.values())
    assert task_contracts["describe_recent_cache_read"]["reads"] == ["mongo_recent_live_replay_cache"]

    graph = langgraph_skeleton.describe_graph()
    assert graph.routes == langgraph_skeleton.ROUTES
    assert set(graph.routes) == {"quick_answer", "evidence_answer", "needs_job", "approval_required", "report_shell"}
    assert graph.side_effects_executed is False
    assert graph.scope == "optional async evidence/report/job/approval review workflow only"
    assert langgraph_skeleton.make_langgraph() == graph

    quick_decision = langgraph_skeleton.route_request(AgentRequest(text="how does the cms work"))
    assert quick_decision.route == "quick_answer"
    assert quick_decision.needs_approval is False
    evidence_decision = langgraph_skeleton.route_request(AgentRequest(text="show latest power usage"))
    assert evidence_decision.route == "evidence_answer"
    job_decision = langgraph_skeleton.route_request(AgentRequest(text="monthly report summary"))
    assert job_decision.route == "needs_job"
    approval_decision = langgraph_skeleton.route_request(AgentRequest(text="delete cache and schedule deploy"))
    assert approval_decision.route == "approval_required"
    assert approval_decision.needs_approval is True
    blocked_decision = langgraph_skeleton.route_request(AgentRequest(text="any usage", context={"qa_blocked": True}))
    assert blocked_decision.route == "report_shell"
    explicit_decision = langgraph_skeleton.route_request(AgentRequest(text="status", route_hint="needs_job"))
    assert explicit_decision.route == "needs_job"

    store = review_jobs.ReviewJobStore(id_prefix="verify")
    submitted = store.submit(AgentRequest(text="monthly report summary"))
    assert submitted["mode"] == "job"
    assert submitted["status"] == "queued"
    processed = store.process(submitted["job_id"])
    assert processed["status"] == "succeeded"
    assert processed["response"]["job_ref"] == f"/ops/jobs/{submitted['job_id']}"
    approval_submitted = store.submit(AgentRequest(text="approve and write to canonical"))
    approval_processed = store.process(approval_submitted["job_id"])
    assert approval_processed["status"] == "running"
    assert approval_processed["awaiting_approval"] is True
    approved = store.approve(approval_submitted["job_id"], approved_by="viowlet")
    assert approved["status"] == "succeeded"
    assert approved["job"]["progress"]["execution"] == "deferred"

    approval_state = langgraph_skeleton.run_review(
        langgraph_skeleton.GraphState(request=AgentRequest(text="approve and write to canonical"))
    )
    assert approval_state.route == "approval_required"
    assert approval_state.needs_human is True
    assert approval_state.response is not None
    assert approval_state.response.needs_human is True
    assert approval_state.response.side_effects_executed is False

    evidence_state = langgraph_skeleton.run_review(
        langgraph_skeleton.GraphState(
            request=AgentRequest(text="show power usage", context={"request_id": "r-1", "coverage_ratio": 0.95})
        )
    )
    assert evidence_state.route == "evidence_answer"
    assert evidence_state.evidence_packet is not None
    assert evidence_state.evidence_packet.qa_summary.status == "pass"
    assert evidence_state.response.side_effects_executed is False

    blocked_state = langgraph_skeleton.run_review(
        langgraph_skeleton.GraphState(request=AgentRequest(text="show power usage", context={"qa_blocked": True}))
    )
    assert blocked_state.route == "report_shell"
    assert blocked_state.report_draft is not None

    for forbidden in ("langgraph", "langchain", "openai", "anthropic"):
        assert forbidden not in sys.modules, f"{forbidden} must not be imported by skeleton modules"

    runtime_names = [
        CANONICAL_MEASUREMENT_1MIN,
        CANONICAL_MEASUREMENT_15MIN,
        CANONICAL_MEASUREMENT_1H,
        *MONGO_COLLECTIONS,
        MeasurementLoadRun(run_id="r1", target_table=CANONICAL_MEASUREMENT_15MIN).table_name,
    ]
    assert all("measurement" in name for name in runtime_names)
    assert all("resampled" not in name for name in runtime_names)

    start = datetime(2023, 1, 1, tzinfo=UTC)
    end = datetime(2023, 1, 2, tzinfo=UTC)
    window = MeasurementWindow(table=CANONICAL_MEASUREMENT_15MIN, start_at=start, end_at=end)
    assert window.contains(start)
    assert not window.contains(end)

    event = MeasurementEvent(
        event_id="evt_1",
        source_kind="replay",
        source_ts=start,
        ingest_ts=start,
        meter_urn="meter:a",
        measurement="power",
        value=1.2,
        run_id="run_1",
    )
    assert event.mongo_collection == "measurement_raw"
    assert event.lineage_key == "run_1:evt_1"

    quarantine = MeasurementQuarantineEvent(
        quarantine_id="q1",
        source_run_id="run_1",
        source_kind="replay",
        reason_code="invalid_value",
        qa_stage="normalize",
    )
    assert quarantine.table_name == "qa.measurement_quarantine"
    assert quarantine.is_data_quality_issue

    budget = default_latency_budget()
    assert budget.chat_quick_p95_ms < budget.chat_evidence_p95_ms
    assert budget.report_pipeline_p95_ms > budget.dashboard_recent_p95_ms

    split = DataSplit.live_replay_default()
    assert split.name == "live_replay"
    assert split.purpose == "holdout_replay"

    job = ApiJob(job_id="job_1", job_type="qa_check", status="queued")
    assert job.status_url == "/ops/jobs/job_1"
    assert job.side_effects_executed is False

    result = MeasurementCheckResult(
        check_result_id="c1",
        check_run_id="r1",
        check_name="coverage",
        severity="fatal",
        status="failed",
    )
    assert result.blocks_promote

    print("cms skeleton contracts ok")


if __name__ == "__main__":
    main()
