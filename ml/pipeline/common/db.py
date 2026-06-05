"""DB 연결 및 데이터 조회. test2 의존 없음."""
from __future__ import annotations

import os
import time

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from ml.pipeline.common.config import MeterSpec, TARGET_COLUMN


def build_engine():
    load_dotenv()
    from sqlalchemy.engine import URL
    url = URL.create(
        drivername="postgresql+psycopg2",
        username=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", os.getenv("DB_PASS", "")),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "ems"),
    )
    return create_engine(url, pool_pre_ping=True)


def fetch_meter_frame(
    engine,
    spec: MeterSpec,
    start_ts: str = "2018-01-01 00:00:00",
    end_ts: str = "2024-01-01 00:00:00",
) -> pd.DataFrame:
    """계량기 원본 데이터 조회 (1시간 집계 테이블)."""
    measurements = tuple(dict.fromkeys((TARGET_COLUMN, *spec.features)))
    select_parts = [
        "ts", "meter_urn",
        *[f"MAX(CASE WHEN measurement = '{m}' THEN value END) AS \"{m}\""
          for m in measurements],
    ]
    sql = f"""
SELECT {", ".join(select_parts)}
FROM reference.corrected_resampled_1h
WHERE meter_urn = :meter_urn
  AND ts >= :start_ts
  AND ts < :end_ts
GROUP BY ts, meter_urn
ORDER BY ts
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
