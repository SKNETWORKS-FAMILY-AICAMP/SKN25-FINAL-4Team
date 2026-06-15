from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLE_PLAN = ROOT / "scripts/database/migrations/gate28_model_serving_runtime_roles.sql"
VERIFY_SQL = ROOT / "scripts/database/verify/gate28_model_serving_runtime_privilege_check.sql"

RUNTIME_ROLE = "cms_model_serving_runtime"
REFERENCE_ROLE = "cms_model_serving_reference_read"

APPROVED_INPUTS = (
    "mart.peak_feature_15min",
    "mart.anomaly_feature_1h",
)
APPROVED_OUTPUTS = (
    "mart.pmax_forecast_15min",
    "mart.anomaly_warning_1h",
    "ops.pmax_forecast_inference_log",
    "ops.anomaly_warning_inference_log",
    "qa.pmax_forecast_evaluation",
    "qa.anomaly_warning_evaluation",
    "qa.model_serving_evidence_packet",
)
REFERENCE_INPUTS = (
    "reference.corrected_resampled_15min",
    "reference.corrected_resampled_1h",
)


def _sql_without_comments(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or stripped.startswith("\\"):
            continue
        kept.append(line.split("--", 1)[0])
    return "\n".join(kept)


def test_gate28_role_plan_is_gated_additive_and_secret_free() -> None:
    text = ROLE_PLAN.read_text()
    sql = _sql_without_comments(text)
    upper_sql = sql.upper()

    assert "cms.allow_gate28_model_serving_runtime_role_plan" in text
    assert "IS DISTINCT FROM '1'" in sql
    assert f"CREATE ROLE {RUNTIME_ROLE} NOLOGIN" in sql
    assert f"CREATE ROLE {REFERENCE_ROLE} NOLOGIN" in sql
    assert "PASSWORD" not in upper_sql
    assert re.search(r"CREATE\s+ROLE\s+\w+\s+LOGIN\b", sql, re.IGNORECASE) is None

    forbidden_patterns = (
        r"\bREVOKE\b",
        r"\bDROP\b",
        r"\bALTER\s+DEFAULT\s+PRIVILEGES\b",
        r"\bDELETE\s+FROM\b",
        r"\bTRUNCATE\b",
        rf"GRANT\s+[^;]*\bDELETE\b[^;]*\bTO\s+{RUNTIME_ROLE}\b",
        rf"GRANT\s+[^;]*\bDELETE\b[^;]*\bTO\s+{REFERENCE_ROLE}\b",
        rf"GRANT\s+[^;]*\bCREATE\b[^;]*\bTO\s+{RUNTIME_ROLE}\b",
        rf"GRANT\s+[^;]*\bCREATE\b[^;]*\bTO\s+{REFERENCE_ROLE}\b",
        r"GRANT\s+[^;]*\bON\s+ALL\s+TABLES\b",
        r"GRANT\s+[^;]*\bON\s+ALL\s+SEQUENCES\b",
        r"GRANT\s+[^;]*\bON\s+SCHEMA\s+canonical\b",
        r"GRANT\s+[^;]*\bON\s+TABLE\s+canonical\.",
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL) is None, pattern


def test_gate28_runtime_role_grants_only_approved_inputs_and_outputs() -> None:
    sql = _sql_without_comments(ROLE_PLAN.read_text())

    for table in APPROVED_INPUTS:
        assert f"GRANT SELECT ON TABLE {table} TO {RUNTIME_ROLE};" in sql
        assert re.search(rf"GRANT\s+[^;]*(INSERT|UPDATE|DELETE)[^;]*\bON\s+TABLE\s+{re.escape(table)}\b", sql, re.IGNORECASE) is None

    for table in APPROVED_OUTPUTS:
        assert f"GRANT SELECT, INSERT, UPDATE ON TABLE {table} TO {RUNTIME_ROLE};" in sql

    runtime_grant_lines = [line for line in sql.splitlines() if f"TO {RUNTIME_ROLE}" in line]
    assert all("reference." not in line for line in runtime_grant_lines)
    assert all("canonical." not in line for line in runtime_grant_lines)
    assert not any("live." in line for line in runtime_grant_lines)

    for table in REFERENCE_INPUTS:
        assert f"GRANT SELECT ON TABLE {table} TO {REFERENCE_ROLE};" in sql
    reference_grant_lines = [line for line in sql.splitlines() if f"TO {REFERENCE_ROLE}" in line]
    assert all("canonical." not in line for line in reference_grant_lines)
    assert all("INSERT" not in line and "UPDATE" not in line and "DELETE" not in line for line in reference_grant_lines)


def test_gate28_read_only_verification_gate_proves_boundaries_without_writes() -> None:
    text = VERIFY_SQL.read_text()
    sql = _sql_without_comments(text)
    statements = [statement.strip().upper() for statement in sql.split(";") if statement.strip()]

    assert statements
    assert all(statement.startswith("WITH") or statement.startswith("SELECT") for statement in statements)
    assert "forbidden_table_privileges:no_extra_no_canonical_no_delete" in text
    assert "forbidden_schema_privileges:no_broad_create_or_extra_usage" in text
    assert "schema_create_privileges:no_create_on_any_non_system_schema" in text
    assert "default_privileges:no_managed_schema_defaults_to_serving_roles_or_public" in text
    assert "gate28_model_serving_runtime_privilege_boundary" in text
    assert RUNTIME_ROLE in text
    assert REFERENCE_ROLE in text

    for verb in ("CREATE", "GRANT", "REVOKE", "ALTER", "DROP", "INSERT INTO", "UPDATE ", "DELETE FROM", "TRUNCATE"):
        assert not any(statement.startswith(verb) for statement in statements), verb

    for table in APPROVED_INPUTS + APPROVED_OUTPUTS + REFERENCE_INPUTS:
        schema, table_name = table.split(".", 1)
        assert f"'{schema}', '{table_name}'" in text
