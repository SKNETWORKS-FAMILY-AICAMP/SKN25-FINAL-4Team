"""
Fetch H1.Z16 hourly measurements from TimescaleDB and join weather data.

.env example:
    DB_HOST=localhost
    DB_PORT=5432
    DB_NAME=ems
    DB_USER=postgres
    DB_PASS=your_password
"""

from __future__ import annotations

import os
import time

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from config.meter_metadata import get_metadata


START_TS = "2018-01-01 00:00:00"
END_TS = "2024-12-31 23:59:59"
TARGET_METER_URN = "H1.Z16"
WEATHER_METER_URN = "WeatherStation.Weather"

ELECTRIC_MEASUREMENTS = (
    "P",
    "W",
    "PF",
    "PF1",
    "PF2",
    "PF3",
    "P1",
    "P2",
    "P3",
    "I1",
    "I2",
    "I3",
    "U1",
    "U2",
    "U3",
    "Q",
    "f",
    "W_in",
    "W_out",
)

THERMAL_MEASUREMENTS = (
    "P",
    "W",
    "Tvl",
    "Trl",
    "Tdiff",
    "qv",
    "V",
)

PIVOT_SQL = """
SELECT
    ts,
    meter_urn,
    MAX(CASE WHEN measurement = 'P'  THEN value END) AS "P",
    MAX(CASE WHEN measurement = 'W'  THEN value END) AS "W",
    MAX(CASE WHEN measurement = 'PF' THEN value END) AS "PF",
    MAX(CASE WHEN measurement = 'PF1' THEN value END) AS "PF1",
    MAX(CASE WHEN measurement = 'PF2' THEN value END) AS "PF2",
    MAX(CASE WHEN measurement = 'PF3' THEN value END) AS "PF3",
    MAX(CASE WHEN measurement = 'P1' THEN value END) AS "P1",
    MAX(CASE WHEN measurement = 'P2' THEN value END) AS "P2",
    MAX(CASE WHEN measurement = 'P3' THEN value END) AS "P3",
    MAX(CASE WHEN measurement = 'I1' THEN value END) AS "I1",
    MAX(CASE WHEN measurement = 'I2' THEN value END) AS "I2",
    MAX(CASE WHEN measurement = 'I3' THEN value END) AS "I3",
    MAX(CASE WHEN measurement = 'U1' THEN value END) AS "U1",
    MAX(CASE WHEN measurement = 'U2' THEN value END) AS "U2",
    MAX(CASE WHEN measurement = 'U3' THEN value END) AS "U3",
    MAX(CASE WHEN measurement = 'Q'  THEN value END) AS "Q",
    MAX(CASE WHEN measurement = 'f'  THEN value END) AS "f",
    MAX(CASE WHEN measurement = 'W_in' THEN value END) AS "W_in",
    MAX(CASE WHEN measurement = 'W_out' THEN value END) AS "W_out"
FROM ems.cr_measurement_1h
WHERE meter_urn = :meter_urn
  AND ts BETWEEN :start_ts AND :end_ts
GROUP BY ts, meter_urn
ORDER BY ts
"""

WEATHER_SQL = """
SELECT
    ts,
    MAX(CASE WHEN measurement = 'Ta'  THEN value END) AS "Ta",
    MAX(CASE WHEN measurement = 'Igm' THEN value END) AS "Igm"
FROM ems.cr_measurement_1h
WHERE meter_urn = :weather_meter_urn
  AND ts BETWEEN :start_ts AND :end_ts
GROUP BY ts
ORDER BY ts
"""

THERMAL_SQL = """
SELECT
    ts,
    meter_urn,
    MAX(CASE WHEN measurement = 'P'     THEN value END) AS "P",
    MAX(CASE WHEN measurement = 'W'     THEN value END) AS "W",
    MAX(CASE WHEN measurement = 'Tvl'   THEN value END) AS "Tvl",
    MAX(CASE WHEN measurement = 'Trl'   THEN value END) AS "Trl",
    MAX(CASE WHEN measurement = 'Tdiff' THEN value END) AS "Tdiff",
    MAX(CASE WHEN measurement = 'qv'    THEN value END) AS "qv",
    MAX(CASE WHEN measurement = 'V'     THEN value END) AS "V"
FROM ems.cr_measurement_1h
WHERE meter_urn = :meter_urn
  AND ts BETWEEN :start_ts AND :end_ts
GROUP BY ts, meter_urn
ORDER BY ts
"""


def build_engine():
    load_dotenv()

    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "ems")
    db_user = os.getenv("DB_USER", "postgres")
    db_pass = os.getenv("DB_PASS", "")

    database_url = (
        f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    )
    return create_engine(database_url, pool_pre_ping=True)


def get_measurement_columns(meter_urn: str) -> tuple[str, ...]:
    metadata = get_metadata(meter_urn)
    if metadata is None:
        raise ValueError(f"Metadata not found for {meter_urn}")
    if metadata.get("meter_type") == "thermal":
        return THERMAL_MEASUREMENTS
    return ELECTRIC_MEASUREMENTS


def fetch_main_data(engine) -> pd.DataFrame:
    return pd.read_sql(
        text(PIVOT_SQL),
        con=engine,
        params={
            "meter_urn": TARGET_METER_URN,
            "start_ts": START_TS,
            "end_ts": END_TS,
        },
        parse_dates=["ts"],
    )


def fetch_meter_data(engine, meter_urn: str) -> pd.DataFrame:
    metadata = get_metadata(meter_urn)
    if metadata is None:
        raise ValueError(f"Metadata not found for {meter_urn}")

    sql = THERMAL_SQL if metadata.get("meter_type") == "thermal" else PIVOT_SQL
    params = {
        "meter_urn": meter_urn,
        "start_ts": START_TS,
        "end_ts": END_TS,
    }

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return pd.read_sql(
                text(sql),
                con=engine,
                params=params,
                parse_dates=["ts"],
            )
        except Exception as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(5)

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to fetch data for {meter_urn}")


def fetch_weather_data(engine) -> pd.DataFrame:
    return pd.read_sql(
        text(WEATHER_SQL),
        con=engine,
        params={
            "weather_meter_urn": WEATHER_METER_URN,
            "start_ts": START_TS,
            "end_ts": END_TS,
        },
        parse_dates=["ts"],
    )


def validate_columns(df: pd.DataFrame, meter_urn: str = TARGET_METER_URN) -> None:
    expected_columns = {"ts", "meter_urn", *get_measurement_columns(meter_urn)}
    missing = expected_columns.difference(df.columns)
    if missing:
        raise ValueError(f"Pivot result is missing expected columns: {sorted(missing)}")


def main() -> None:
    engine = build_engine()

    df_main = fetch_main_data(engine)
    validate_columns(df_main)

    df_weather = fetch_weather_data(engine)

    df = df_main.merge(df_weather, on="ts", how="left")

    print("df.shape")
    print(df.shape)
    print()

    print("df.dtypes")
    print(df.dtypes)
    print()

    print("df.isnull().sum()")
    print(df.isnull().sum())
    print()

    print("df.head()")
    print(df.head())


if __name__ == "__main__":
    main()
