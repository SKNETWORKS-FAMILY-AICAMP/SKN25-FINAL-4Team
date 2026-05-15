from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    import psycopg
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        dbname=os.environ["DB_NAME"],
    )


def fetch_measurements(
    meter_urn: str,
    measurement: str,
    start_ts: str,
    end_ts: str,
    resolution: str = "1h",
) -> pd.DataFrame:
    table = f"ems.cr_measurement_{resolution}"
    sql = f"""
        SELECT ts, meter_urn, measurement, value
        FROM {table}
        WHERE meter_urn = %s
          AND measurement = %s
          AND ts >= %s
          AND ts < %s
        ORDER BY ts
    """
    load_env()
    with connect() as conn:
        return pd.read_sql(sql, conn, params=(meter_urn, measurement, start_ts, end_ts))