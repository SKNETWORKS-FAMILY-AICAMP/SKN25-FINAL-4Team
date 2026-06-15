#!/usr/bin/env python3
"""Gate-controlled model-serving DDL apply helper.

Default mode prints the migration plan only. Actual execution requires:
  1. --execute
  2. ALLOW_MODEL_SERVING_DDL=1
  3. explicit POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER env

The script does not read .env files and does not apply canonical schema changes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from importlib import import_module
from pathlib import Path
from typing import Any

DDL_PATH = Path("scripts/database/migrations/model_serving_tables.sql")
DDL_GATE_ENV = "ALLOW_MODEL_SERVING_DDL"
_REQUIRED_EXEC_ENV = ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="apply DDL in one transaction after explicit gate checks")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    sql = DDL_PATH.read_text(encoding="utf-8")
    validation_errors = validate_ddl(sql)
    plan = {
        "ddl_path": DDL_PATH.as_posix(),
        "execute_requested": args.execute,
        "gate_env": DDL_GATE_ENV,
        "gate_enabled": os.environ.get(DDL_GATE_ENV) == "1",
        "validation_errors": validation_errors,
    }
    if not args.execute:
        plan["ok"] = not validation_errors
        print(json.dumps(plan, ensure_ascii=False, indent=2) if args.json else _format_plan(plan))
        return 0 if not validation_errors else 1

    errors = list(validation_errors)
    if os.environ.get(DDL_GATE_ENV) != "1":
        errors.append(f"{DDL_GATE_ENV} must be 1")
    errors.extend(f"missing env:{key}" for key in _REQUIRED_EXEC_ENV if not os.environ.get(key))
    if errors:
        plan["ok"] = False
        plan["errors"] = errors
        print(json.dumps(plan, ensure_ascii=False, indent=2) if args.json else _format_plan(plan))
        return 2

    apply_result = apply_ddl(sql)
    plan.update(apply_result)
    print(json.dumps(plan, ensure_ascii=False, default=str, indent=2) if args.json else _format_plan(plan))
    return 0 if apply_result.get("ok") else 1


def validate_ddl(sql: str) -> list[str]:
    lower = sql.lower()
    errors: list[str] = []
    if "do not execute" not in lower:
        errors.append("review-only warning is missing")
    if "canonical." in lower:
        errors.append("canonical schema reference is forbidden")
    if "drop table" in lower or "drop schema" in lower:
        errors.append("destructive DDL is forbidden")
    if "mart.anomaly_input_1h" in lower:
        errors.append("legacy anomaly input table name is forbidden; use mart.anomaly_feature_1h")
    if re.search(r"create\s+table\s+(?:if\s+not\s+exists\s+)?[a-z_]+\.[a-z0-9_]*_input_[a-z0-9_]*", lower):
        errors.append("new *_input_* tables are forbidden; use *_feature_* or *_training_frame_* names")
    required = (
        "mart.pmax_forecast_15min",
        "mart.peak_training_frame_15min",
        "mart.anomaly_feature_1h",
        "mart.anomaly_warning_1h",
        "ops.pmax_forecast_inference_log",
        "ops.anomaly_warning_inference_log",
        "qa.model_serving_evidence_packet",
    )
    for table in required:
        if table not in lower:
            errors.append(f"required table missing:{table}")
    return errors


def apply_ddl(sql: str) -> dict[str, Any]:
    psycopg = import_module("psycopg")
    kwargs = {
        "host": os.environ["POSTGRES_HOST"],
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "sslmode": os.environ.get("POSTGRES_SSLMODE", "disable"),
        "connect_timeout": int(os.environ.get("POSTGRES_CONNECT_TIMEOUT", "5")),
    }
    password = os.environ.get("POSTGRES_PASSWORD")
    if password:
        kwargs["password"] = password
    try:
        with psycopg.connect(**kwargs) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - CLI reports DB failure without exposing password.
        message = str(exc)
        if password:
            message = message.replace(password, "[REDACTED]")
        return {"ok": False, "error": message}
    return {"ok": True, "applied": True}


def _format_plan(plan: dict[str, Any]) -> str:
    lines = [f"ddl_path={plan['ddl_path']}", f"execute_requested={plan['execute_requested']}", f"gate_enabled={plan['gate_enabled']}"]
    for error in plan.get("validation_errors", ()):
        lines.append(f"validation_error={error}")
    for error in plan.get("errors", ()):
        lines.append(f"error={error}")
    if "applied" in plan:
        lines.append(f"applied={plan['applied']}")
    if "ok" in plan:
        lines.append(f"ok={plan['ok']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
