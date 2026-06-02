"""Run the FastAPI query-plan mock evaluation set and write report artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient

from cms.service import api
from scripts.verify.query_plan_eval_support import QUERY_EVAL_CASES, render_markdown_report, run_eval_case, summarize_records


OUT_DIR = Path("reports/query_plan_eval_20260601")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = TestClient(api.create_app())
    records = [run_eval_case(client, case) for case in QUERY_EVAL_CASES]
    summary = summarize_records(records)

    json_path = OUT_DIR / "query_plan_eval_results.json"
    md_path = OUT_DIR / "query_plan_eval_report.md"
    csv_path = OUT_DIR / "query_plan_eval_cases.csv"

    json_path.write_text(json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown_report(records), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["case_id", "status", "prompt", "actual_status", "table", "aggregation", "failures"],
        )
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "case_id": record["case_id"],
                    "status": record["status"],
                    "prompt": record.get("prompt", ""),
                    "actual_status": record.get("actual_status", ""),
                    "table": record.get("table", ""),
                    "aggregation": record.get("aggregation", ""),
                    "failures": "; ".join(record.get("failures", [])),
                }
            )

    print(f"QUERY_PLAN_EVAL_STATUS={summary['status']}")
    print(f"QUERY_PLAN_EVAL_TOTAL={summary['total']}")
    print(f"QUERY_PLAN_EVAL_PASSED={summary['passed']}")
    print(f"QUERY_PLAN_EVAL_FAILED={summary['failed']}")
    print(f"QUERY_PLAN_EVAL_REPORT={md_path.as_posix()}")
    print(f"QUERY_PLAN_EVAL_JSON={json_path.as_posix()}")
    print(f"QUERY_PLAN_EVAL_CSV={csv_path.as_posix()}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
