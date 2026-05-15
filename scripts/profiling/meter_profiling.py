"""전체 81개 계량기 프로파일링 스크립트.

DB에서 모든 계량기의 데이터 존재 기간, 결측률, measurement 커버리지,
게이트웨이 장애 영향, 설비 변경(Regime) 영향을 일괄 산출한다.

Usage:
    python scripts/profiling/meter_profiling.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg

from dotenv import load_dotenv

load_dotenv()

# ── DB 연결 ──────────────────────────────────────────────
CONNECT_KWARGS = {
    "host": os.environ["DB_HOST"],
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ["DB_NAME"],
    "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}


def query_df(sql: str, params=None) -> pd.DataFrame:
    """SQL 조회 → DataFrame 반환."""
    with psycopg.connect(**CONNECT_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [desc.name for desc in cur.description]
            rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


# ── 논문 기반 상수 ─────────────────────────────────────────

# 게이트웨이 장애 기간 (Usage Notes)
GATEWAY_FAILURES = [
    {
        "name": "Workshop gateway failure #1",
        "start": "2020-02-13",
        "end": "2020-03-06",
        "affected_group": "workshop",
    },
    {
        "name": "Emission lab gateway failure",
        "start": "2020-08-20",
        "end": "2020-09-17",
        "affected_group": "emission_lab",
    },
    {
        "name": "Distribution gateway failure",
        "start": "2021-11-15",
        "end": "2021-12-10",
        "affected_group": "distribution",
        "affected_meters": ["H2.T.Z30", "H2.T.Z31", "H2.T.Z32", "H2.K21"],
    },
    {
        "name": "Workshop gateway failure #2",
        "start": "2022-05-06",
        "end": "2022-07-14",
        "affected_group": "workshop",
    },
]

# 설비 변경 이벤트 (Regime 경계)
REGIME_EVENTS = [
    {"date": "2019-02-13", "event": "CHP 제어 로직 변경 (On/Off → 50-100% 모듈레이션)", "affected": "CHP"},
    {"date": "2019-06-01", "event": "PV 시스템 1차 설치 (소규모)", "affected": "PV"},
    {"date": "2020-03-01", "event": "COVID-19 시작 (사무실 인원 감축)", "affected": "전체 소비"},
    {"date": "2020-06-01", "event": "PV 시스템 2차 증설 (풀용량 749kWp)", "affected": "PV"},
    {"date": "2020-09-09", "event": "사무실 변압기 계량기 교체 (H2.Z35/36 → H2.Z351/361)", "affected": "Office"},
    {"date": "2023-06-01", "event": "난방 시스템 현대화 (CHP 가동률 향상)", "affected": "CHP, Heating"},
]

# 계량기 그룹 매핑 (계량기_메타데이터.md 기준)
METER_GROUPS = {
    "grid_transformer": ["V.Z82", "V.Z81", "H2.Z35", "H2.Z351", "H2.Z36", "H2.Z361"],
    "chp": ["H1.Z20", "H1.ZE20"],
    "pv": ["V.Z84", "V.ZE84", "H1.Z310", "H2.Z311", "H3.Z312"],
    "emission_lab": [
        "H1.Z15", "H1.Z28", "H1.Z17", "H1.Z29", "H1.Z10", "H1.Z13",
        "H1.Z14", "H1.Z16", "H1.Z19", "H1.Z23", "H1.Z18", "H1.Z21",
        "H1.Z22", "H1.Z26", "H1.Z27",
    ],
    "central_cooling": ["H1.Z16", "H1.Z11", "H1.Z12", "H1.Z24", "H1.Z25"],
    "server_power": [
        "H3.Z43", "H3.ZE43", "H3.Z44", "H3.ZE44", "H3.Z46",
        "H2.Z61", "H2.Z62", "H2.Z63", "H2.Z64", "H2.ZE64",
        "H2.Z65", "H2.ZE65", "H3.Z71",
    ],
    "local_cooling": ["H3.Z45", "H2.Z66", "H2.ZE66", "H2.Z67", "H2.ZE67"],
    "ventilation": ["H2.T.Z31", "H3.Z42", "H2.Z68", "H2.Z69", "H2.Z70"],
    "workshop_test": ["H2.T.Z34", "H2.ZE74"],
    "office_distribution": ["H2.T.Z30", "H2.T.Z32", "H4.Z50", "H4.ZE50", "H4.Z51", "H4.ZE51"],
    "design_studio": [
        "H2.T.Z33", "H3.Z40", "H3.ZE40", "H3.Z41", "H3.ZE41",
        "H3.Z47", "H3.Z48", "H3.Z49",
    ],
    "cooling_thermal": ["V.K21"],
    "hvac_thermal": ["H1.K11", "H1.K12", "H1.K14", "H1.K15", "H2.K21"],
    "server_thermal": ["H1.K16"],
    "heat_generation": ["H1.W11"],
    "chp_heat_generation": ["H1.W12"],
    "weather_station": ["WeatherStation.Weather"],
}

# 역매핑: meter_urn → equipment_group
METER_TO_GROUP = {}
for group, meters in METER_GROUPS.items():
    for m in meters:
        # 하나의 미터가 여러 그룹에 속할 수 있음 (예: H1.Z16 → central_cooling, emission_lab)
        if m not in METER_TO_GROUP:
            METER_TO_GROUP[m] = group


def get_output_dir() -> Path:
    """산출물 디렉터리 반환."""
    out = Path("outputs/profiling")
    out.mkdir(parents=True, exist_ok=True)
    return out


# ── 1. 미터 레지스트리 ──────────────────────────────────────

def profile_meter_registry() -> pd.DataFrame:
    """full_meter 레지스트리 조회 + 그룹 매핑."""
    df = query_df("SELECT meter_urn, meter_group FROM ems.full_meter ORDER BY meter_urn")
    df["equipment_group"] = df["meter_urn"].map(METER_TO_GROUP).fillna("unknown")
    print(f"[Registry] 전체 미터 수: {len(df)}")
    return df


# ── 2. CR Mart 기준 미터별 프로파일 ───────────────────────────

def profile_cr_coverage() -> pd.DataFrame:
    """cr_measurement_1h 기준: 미터별 시간 범위, row 수, measurement 수, 결측률."""
    sql = """
    SELECT
        meter_urn,
        '1h'::text AS resolution_code,
        count(*) AS total_rows,
        count(DISTINCT measurement) AS measurement_count,
        min(ts) AS min_ts,
        max(ts) AS max_ts,
        count(*) FILTER (WHERE value IS NULL) AS null_rows,
        round(
            100.0 * count(*) FILTER (WHERE value IS NULL) / NULLIF(count(*), 0),
            2
        ) AS null_pct
    FROM ems.cr_measurement_1h
    GROUP BY meter_urn
    ORDER BY meter_urn
    """
    df = query_df(sql)
    if "min_ts" in df.columns:
        df["min_ts"] = pd.to_datetime(df["min_ts"], utc=True)
        df["max_ts"] = pd.to_datetime(df["max_ts"], utc=True)
        df["span_days"] = (df["max_ts"] - df["min_ts"]).dt.total_seconds() / 86400
    print(f"[CR Coverage] 미터-해상도 조합 수: {len(df)}")
    return df


# ── 3. 미터별 measurement 상세 ──────────────────────────────

def profile_measurement_detail() -> pd.DataFrame:
    """미터별 보유 measurement 목록과 row 수 (1h 기준)."""
    sql = """
    SELECT
        meter_urn,
        measurement,
        '1h'::text AS resolution_code,
        count(*) AS rows,
        count(*) FILTER (WHERE value IS NULL) AS null_rows,
        round(100.0 * count(*) FILTER (WHERE value IS NULL) / NULLIF(count(*), 0), 2) AS null_pct,
        min(ts) AS min_ts,
        max(ts) AS max_ts
    FROM ems.cr_measurement_1h
    GROUP BY meter_urn, measurement
    ORDER BY meter_urn, measurement
    """
    return query_df(sql)


# ── 4. Source file 적재 상태 ─────────────────────────────────

def profile_source_files() -> pd.DataFrame:
    """full_source_file 기준 미터별 적재 상태 요약."""
    sql = """
    SELECT
        meter_urn,
        processing_level,
        resolution_code,
        count(*) AS source_files,
        count(DISTINCT measurement) AS measurements,
        sum(csv_rows) AS csv_rows,
        sum(inserted_rows) AS inserted_rows,
        sum(conflict_rows) AS conflict_rows,
        sum(null_value_rows) AS null_value_rows,
        sum(non_finite_rows) AS non_finite_rows,
        sum(invalid_value_rows) AS invalid_value_rows,
        sum(invalid_ts_rows) AS invalid_ts_rows,
        count(*) FILTER (WHERE status != 'loaded') AS non_loaded_files
    FROM ems.full_source_file
    GROUP BY meter_urn, processing_level, resolution_code
    ORDER BY meter_urn, processing_level, resolution_code
    """
    return query_df(sql)


# ── 5. 연도별 결측률 (1h 기준) ──────────────────────────────

def profile_yearly_null_rate() -> pd.DataFrame:
    """연도별 미터별 결측률 (1h 기준)."""
    sql = """
    SELECT
        meter_urn,
        measurement,
        extract(year FROM ts) AS year,
        count(*) AS total_rows,
        count(*) FILTER (WHERE value IS NULL) AS null_rows,
        round(100.0 * count(*) FILTER (WHERE value IS NULL) / NULLIF(count(*), 0), 2) AS null_pct
    FROM ems.cr_measurement_1h
    GROUP BY meter_urn, measurement, extract(year FROM ts)
    ORDER BY meter_urn, measurement, year
    """
    return query_df(sql)


# ── 6. 게이트웨이 장애 구간 결측 영향 ──────────────────────────

def profile_gateway_failure_impact() -> pd.DataFrame:
    """게이트웨이 장애 기간 동안 각 미터의 결측 비율."""
    results = []
    for failure in GATEWAY_FAILURES:
        sql = """
        SELECT
            meter_urn,
            count(*) AS total_rows,
            count(*) FILTER (WHERE value IS NULL) AS null_rows,
            round(100.0 * count(*) FILTER (WHERE value IS NULL) / NULLIF(count(*), 0), 2) AS null_pct
        FROM ems.cr_measurement_1h
        WHERE ts >= %s::timestamptz AND ts < %s::timestamptz
        GROUP BY meter_urn
        ORDER BY null_pct DESC
        """
        df = query_df(sql, (failure["start"], failure["end"]))
        df["failure_name"] = failure["name"]
        df["failure_start"] = failure["start"]
        df["failure_end"] = failure["end"]
        results.append(df)
    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


# ── 메인 ───────────────────────────────────────────────────

def main():
    out = get_output_dir()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    print("=" * 60)
    print("EMS 전체 미터 프로파일링 시작")
    print("=" * 60)

    # 1. 미터 레지스트리
    print("\n[1/6] 미터 레지스트리 조회...")
    registry = profile_meter_registry()
    registry.to_csv(out / "01_meter_registry.csv", index=False)

    # 2. CR Mart 기준 커버리지
    print("[2/6] CR Mart 커버리지 산출...")
    cr_coverage = profile_cr_coverage()
    cr_coverage.to_csv(out / "02_cr_coverage.csv", index=False)

    # 3. 미터별 measurement 상세
    print("[3/6] 미터별 measurement 상세 산출...")
    meas_detail = profile_measurement_detail()
    meas_detail.to_csv(out / "03_measurement_detail.csv", index=False)

    # 4. Source file 적재 상태
    print("[4/6] Source file 적재 상태 산출...")
    source_files = profile_source_files()
    source_files.to_csv(out / "04_source_files.csv", index=False)

    # 5. 연도별 결측률
    print("[5/6] 연도별 결측률 산출...")
    yearly_null = profile_yearly_null_rate()
    yearly_null.to_csv(out / "05_yearly_null_rate.csv", index=False)

    # 6. 게이트웨이 장애 영향
    print("[6/6] 게이트웨이 장애 영향 산출...")
    gw_impact = profile_gateway_failure_impact()
    gw_impact.to_csv(out / "06_gateway_failure_impact.csv", index=False)

    # ── 요약 출력 ───────────────────────────────────────────
    print("\n" + "=" * 60)
    print("프로파일링 완료 요약")
    print("=" * 60)

    print(f"\n총 미터 수: {len(registry)}")

    if not cr_coverage.empty:
        # 미터별 집계 (resolution별 분리 전)
        meter_summary = cr_coverage.groupby("meter_urn").agg(
            total_rows=("total_rows", "sum"),
            null_rows=("null_rows", "sum"),
            min_ts=("min_ts", "min"),
            max_ts=("max_ts", "max"),
        ).reset_index()
        meter_summary["null_pct"] = (100.0 * meter_summary["null_rows"] / meter_summary["total_rows"]).round(2)

        print(f"\nCR Mart 데이터 보유 미터 수: {len(meter_summary)}")
        print(f"전체 기간: {meter_summary['min_ts'].min()} ~ {meter_summary['max_ts'].max()}")
        print(f"전체 평균 결측률: {meter_summary['null_pct'].mean():.2f}%")

        # 결측률 상위 10
        top_null = meter_summary.nlargest(10, "null_pct")[["meter_urn", "total_rows", "null_rows", "null_pct"]]
        print("\n결측률 상위 10 미터:")
        print(top_null.to_string(index=False))

    if not gw_impact.empty:
        # 게이트웨이 장애 시 100% 결측인 미터
        fully_missing = gw_impact[gw_impact["null_pct"] == 100]
        print(f"\n게이트웨이 장애 기간 중 100% 결측 (미터-장애 조합): {len(fully_missing)}건")

    # Regime 이벤트 출력
    print("\n설비 변경 이벤트 (Regime 경계):")
    for evt in REGIME_EVENTS:
        print(f"  {evt['date']} | {evt['event']}")

    print(f"\n산출물 저장 위치: {out.resolve()}")
    print("완료!")


if __name__ == "__main__":
    main()
