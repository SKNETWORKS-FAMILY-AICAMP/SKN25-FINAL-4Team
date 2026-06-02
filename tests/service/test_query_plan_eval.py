from __future__ import annotations

import importlib.util

import pytest

from scripts.verify.query_plan_eval_support import QUERY_EVAL_CASES, run_eval_case, summarize_records


def test_fastapi_query_plan_evaluation_set_matches_mock_rows() -> None:
    if importlib.util.find_spec("fastapi") is None:
        pytest.skip("fastapi is required for FastAPI query-plan evaluation")
    if importlib.util.find_spec("httpx") is None:
        pytest.skip("httpx is required for FastAPI TestClient")

    from fastapi.testclient import TestClient

    from cms.service import api

    client = TestClient(api.create_app())
    records = [run_eval_case(client, case) for case in QUERY_EVAL_CASES]
    summary = summarize_records(records)

    assert summary == {"total": 7, "passed": 7, "failed": 0, "status": "pass"}, [
        {"case_id": record["case_id"], "failures": record["failures"]} for record in records if record["status"] != "pass"
    ]
