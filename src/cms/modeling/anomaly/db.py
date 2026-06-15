"""DB 연결 및 데이터 조회. test2 의존 없음."""
from __future__ import annotations

import os
import re
import time

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from cms.contracts.anomaly_detection_1h import ANOMALY_DETECTION_FEATURE_TABLE
from cms.modeling.anomaly.config import MeterSpec, TARGET_COLUMN

TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_SOURCE_TABLE = ANOMALY_DETECTION_FEATURE_TABLE
FEATURE_COLUMN_MAP = {
    "P": "p_value",
    "U1": "u1_value",
    "PF": "pf_value",
    "qv": "qv_value",
    "Tdiff": "tdiff_value",
}


def _source_table() -> str:
    table = os.getenv("CMS_ANOMALY_SOURCE_TABLE", DEFAULT_SOURCE_TABLE)
    if not TABLE_NAME_PATTERN.fullmatch(table):
        raise ValueError(f"Invalid schema-qualified CMS_ANOMALY_SOURCE_TABLE: {table!r}")
    if table != DEFAULT_SOURCE_TABLE:
        raise ValueError(f"Anomaly serving source table must be {DEFAULT_SOURCE_TABLE}, got {table!r}")
    return table


def _feature_select_expr(feature: str) -> str:
    if feature in FEATURE_COLUMN_MAP:
        column = FEATURE_COLUMN_MAP[feature]
        return f'{column} AS "{feature}"'
    escaped = feature.replace("'", "''")
    return f'(derived_features ->> \'{escaped}\')::double precision AS "{feature}"'


def build_engine():
    load_dotenv()
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "cms")
    db_user = os.getenv("DB_USER", "postgres")
    db_pass = os.getenv("DB_PASS", "")
    url = f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    return create_engine(url, pool_pre_ping=True)


def fetch_meter_frame(
    engine,
    spec: MeterSpec,
    start_ts: str = "2018-01-01 00:00:00",
    end_ts: str = "2024-01-01 00:00:00",
) -> pd.DataFrame:
    """계량기 원본 데이터 조회 (1시간 집계 테이블)."""
    measurements = tuple(dict.fromkeys((TARGET_COLUMN, *spec.features)))
    source_table = _source_table()
    select_parts = [
        "bucket_ts AS ts", "meter_urn",
        *[_feature_select_expr(measurement) for measurement in measurements],
    ]
    sql = f"""
SELECT {", ".join(select_parts)}
FROM {source_table}
WHERE meter_urn = :meter_urn
  AND bucket_ts >= :start_ts
  AND bucket_ts < :end_ts
ORDER BY bucket_ts
"""
    last_err = None
    for attempt in range(1, 4):
        try:
            frame = pd.read_sql(
                text(sql), con=engine,
                params={"meter_urn": spec.meter_urn, "start_ts": start_ts, "end_ts": end_ts},
                parse_dates=["ts"],
            )
            if frame.empty:
                raise ValueError(f"No rows for {spec.meter_urn}")
            return frame
        except Exception as exc:
            last_err = exc
            if attempt < 3:
                time.sleep(5)
    raise last_err


def fetch_meter_window(
    engine,
    spec: MeterSpec,
    end_ts: pd.Timestamp,
    window_hours: int,
) -> pd.DataFrame:
    """inference용: 특정 시각 기준 최근 window_hours 데이터 조회."""
    start_ts = end_ts - pd.Timedelta(hours=window_hours)
    return fetch_meter_frame(
        engine, spec,
        start_ts=start_ts.strftime("%Y-%m-%d %H:%M:%S"),
        end_ts=end_ts.strftime("%Y-%m-%d %H:%M:%S"),
    )
