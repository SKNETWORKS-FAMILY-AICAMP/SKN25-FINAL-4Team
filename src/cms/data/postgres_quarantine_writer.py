"""SQL-build-only quarantine writer contract for ``qa.bad_row``.

This module deliberately does not import a PostgreSQL client and never opens a
connection.  It only builds a parameterized INSERT command and sanitizes payload
fragments for local dry-run evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

QA_BAD_ROW_TABLE = "qa.bad_row"
QA_BAD_ROW_STAGE_VALUE_QUALITY = "value_quality"
QA_BAD_ROW_REVIEW_STATUS_PENDING = "pending"

_BAD_ROW_INSERT_COLUMNS = (
    "reason_code",
    "raw_ts",
    "raw_value",
    "source_file",
    "source_row_no",
    "source_ref",
    "lineage_ref",
    "meter_urn",
    "measurement",
    "qa_stage",
    "review_status",
    "raw_payload",
)

_SECRET_KEY_PARTS = (
    "secret",
    "password",
    "passwd",
    "token",
    "credential",
    "authorization",
    "connection_string",
    "dsn",
)
_MAX_RAW_VALUE_CHARS = 128
_MAX_RAW_PAYLOAD_CHARS = 2048


@dataclass(frozen=True)
class PostgresQuarantineCommand:
    """Parameterized SQL command for a dry-run ``qa.bad_row`` insert."""

    target_table: str
    sql: str
    params: dict[str, object]
    dry_run: bool = True


def _truncate_text(value: object, *, max_chars: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def _is_secret_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): "[REDACTED]" if _is_secret_key(key) else _redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def sanitize_raw_value(value: object) -> str | None:
    """Return a short, non-secret representation of a raw measurement value."""

    return _truncate_text(_redact(value), max_chars=_MAX_RAW_VALUE_CHARS)


def sanitize_raw_payload(payload: object) -> str | None:
    """Return a redacted/truncated JSON representation for dry-run evidence."""

    if payload is None:
        return None
    redacted = _redact(payload)
    try:
        text = json.dumps(redacted, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        text = str(redacted)
    return _truncate_text(text, max_chars=_MAX_RAW_PAYLOAD_CHARS)


def _require_qa_bad_row(payload: Mapping[str, object]) -> None:
    target = payload.get("target_table")
    if target != QA_BAD_ROW_TABLE:
        raise ValueError(f"target_table must be {QA_BAD_ROW_TABLE}; quarantine writes must not target live/canonical tables")


def make_bad_row_insert_command(payload: Mapping[str, object]) -> PostgresQuarantineCommand:
    """Build a parameterized dry-run INSERT for the ``qa.bad_row`` extension.

    The returned command is suitable for tests and reviewed SQL evidence only.
    It intentionally performs no network or database I/O.
    """

    _require_qa_bad_row(payload)
    columns = ", ".join(_BAD_ROW_INSERT_COLUMNS)
    values = ", ".join(f"%({column})s" for column in _BAD_ROW_INSERT_COLUMNS)
    sql = f"INSERT INTO {QA_BAD_ROW_TABLE} ({columns}) VALUES ({values})"
    params = {column: payload.get(column) for column in _BAD_ROW_INSERT_COLUMNS}
    params["raw_value"] = sanitize_raw_value(params.get("raw_value"))
    params["raw_payload"] = sanitize_raw_payload(params.get("raw_payload"))
    if not params.get("qa_stage"):
        params["qa_stage"] = QA_BAD_ROW_STAGE_VALUE_QUALITY
    if not params.get("review_status"):
        params["review_status"] = QA_BAD_ROW_REVIEW_STATUS_PENDING
    return PostgresQuarantineCommand(target_table=QA_BAD_ROW_TABLE, sql=sql, params=params, dry_run=True)


def build_bad_row_insert_command(payload: Mapping[str, object]) -> PostgresQuarantineCommand:
    """Compatibility alias for callers that use build_* naming."""

    return make_bad_row_insert_command(payload)


make_quarantine_insert_command = make_bad_row_insert_command


__all__ = [
    "PostgresQuarantineCommand",
    "QA_BAD_ROW_REVIEW_STATUS_PENDING",
    "QA_BAD_ROW_STAGE_VALUE_QUALITY",
    "QA_BAD_ROW_TABLE",
    "build_bad_row_insert_command",
    "make_bad_row_insert_command",
    "make_quarantine_insert_command",
    "sanitize_raw_payload",
    "sanitize_raw_value",
]
