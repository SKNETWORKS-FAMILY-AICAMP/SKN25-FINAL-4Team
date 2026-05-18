"""
ems 스키마 읽기 전용 데이터 로더.
SELECT만 사용 — ems 스키마 데이터 절대 수정 없음.

미터 매핑 (논문 Table 2·3·Fig.1 기반):
  그리드    : V.Z81 + V.Z82 + H2.Z351 + H2.Z361  (논문 Table 8 — 부지 전체 4개 변압기)
              V.Z81/Z82: 주차장 변압기 (H1·H2·H3 담당)
              H2.Z351/Z361: 오피스 변압기 교체 버전 (2020-09-15~)
              P 컬럼만 사용. V.Z81 W_in은 오버플로우(-55억 kWh)이므로 사용 금지.
  PV        : H1.Z310 + H2.Z311 + H3.Z312 + V.Z84  (4개 PV 그룹)
  CHP 전기  : H1.ZE20 (2023~ 교정 의무 설치) / H1.Z20 (~2022 원본, 동일 선로)
              타임스탬프마다 ZE20 우선, 없으면 Z20 사용 (coalesce). 절대 합산 금지.
  CHP 열    : H1.W12
  총 열     : H1.W11
  냉방 출력 : V.K21  (3대 냉각기 합산 주 미터, 고장 기간 서브미터 재구성값 포함)
  냉방 전기 : H1.Z11 + H1.Z12 + H1.Z16 + H1.Z24 + H1.Z25  (CM1~3 전기)
  일사량/기온: WeatherStation.Weather  (Igm, Ta)

부호 규칙: 양수 = 소비(inflow), 음수 = 생산(outflow)
  → PV·CHP 미터는 음수이므로 abs() 적용 후 '생산량'으로 표현
"""

import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
TZ_LOCAL = "Europe/Berlin"

# ── 미터 그룹 정의 ────────────────────────────────────────────────

# 부지 전체 그리드 (논문 Table 8 기준 — 4개 변압기 전체)
# V.Z81/Z82: 주차장 변압기 (H1·H2·H3 담당)
# H2.Z35/Z36: 오피스 변압기 구형 (2020-09-15 이전), H2.Z351/Z361: 교체 버전 (이후)
# 동일 ts에 구형+신형이 함께 존재하지 않으므로 합산해도 이중 계산 없음
_GRID_METERS        = ["V.Z81", "V.Z82", "H2.Z35", "H2.Z36", "H2.Z351", "H2.Z361"]
_PV_METERS          = ["H1.Z310", "H2.Z311", "H3.Z312", "V.Z84"]
# CHP: H1.ZE20(2023~)과 H1.Z20(~2022)은 동일 선로 이중계측 → coalesce, 합산 금지
_CHP_ELEC_PRIMARY   = ["H1.ZE20"]   # 2023년 교정 의무 설치
_CHP_ELEC_FALLBACK  = ["H1.Z20"]    # 원본 미터 (~2022 유효)
_CHP_HEAT           = ["H1.W12"]
_HEAT_TOTAL         = ["H1.W11"]
_COOL_OUTPUT        = ["V.K21"]
_COOL_ELEC          = ["H1.Z11", "H1.Z12", "H1.Z16", "H1.Z24", "H1.Z25"]
_WEATHER            = ["WeatherStation.Weather"]

_ALL_METERS = (
    _GRID_METERS + _PV_METERS +
    _CHP_ELEC_PRIMARY + _CHP_ELEC_FALLBACK +
    _CHP_HEAT + _HEAT_TOTAL + _COOL_OUTPUT + _COOL_ELEC + _WEATHER
)


def _get_conn():
    return psycopg2.connect(DB_URL)


def _query(sql: str, params: tuple) -> pd.DataFrame:
    """ems 스키마 SELECT 전용 쿼리 실행."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [desc[0] for desc in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()
    return df


def get_data_range() -> tuple[datetime, datetime]:
    """ems 데이터의 실제 시작·종료 시각 반환."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT MIN(ts), MAX(ts) FROM ems.cr_measurement_1h;")
    row = cur.fetchone()
    conn.close()
    return row[0], row[1]


# ── 핵심 로더 ─────────────────────────────────────────────────────

def load_reduced(
    start: datetime,
    end: datetime,
    freq: str = "1h",
) -> pd.DataFrame:
    """
    reduced_data와 동일한 컬럼 구조로 반환.
    start/end는 timezone-aware datetime (UTC 또는 Berlin).

    반환 컬럼:
      ts (Europe/Berlin), grid_P, pv_P, chp_P,
      heat_total_P, chp_heat_P, cool_output_P, cool_elec_P,
      Igm, Ta, cop, self_sufficiency
    """
    table = "ems.cr_measurement_1h" if freq == "1h" else "ems.cr_measurement_15min"

    meters_sql = ",".join(f"'{m}'" for m in _ALL_METERS)
    sql = f"""
        SELECT ts, meter_urn, measurement, value
        FROM {table}
        WHERE ts >= %s AND ts < %s
          AND meter_urn IN ({meters_sql})
          AND measurement IN ('P', 'Igm', 'Ta')
        ORDER BY ts;
    """
    raw = _query(sql, (start, end))
    if raw.empty:
        return pd.DataFrame()

    raw["ts"] = pd.to_datetime(raw["ts"], utc=True).dt.tz_convert(TZ_LOCAL)

    # 미터·measurement 조합별 pivot
    raw["col"] = raw["meter_urn"] + "__" + raw["measurement"]
    pivot = raw.pivot_table(index="ts", columns="col", values="value", aggfunc="mean")

    def _sum_abs(cols):
        existing = [c for c in cols if c in pivot.columns]
        if not existing:
            return pd.Series(float("nan"), index=pivot.index)
        return pivot[existing].abs().sum(axis=1, min_count=1)

    def _sum(cols):
        existing = [c for c in cols if c in pivot.columns]
        if not existing:
            return pd.Series(float("nan"), index=pivot.index)
        return pivot[existing].sum(axis=1, min_count=1)

    df = pd.DataFrame(index=pivot.index)
    df.index.name = "ts"

    # 에너지 컬럼 (W 단위)
    df["grid_P"]       = _sum([f"{m}__P" for m in _GRID_METERS])
    df["pv_P"]         = _sum_abs([f"{m}__P" for m in _PV_METERS])      # 생산 → abs

    # CHP: ZE20(2023~) 우선, 없는 타임스탬프는 Z20(~2022) fallback — 합산 금지
    _ze20 = "H1.ZE20__P"
    _z20  = "H1.Z20__P"
    if _ze20 in pivot.columns and _z20 in pivot.columns:
        df["chp_P"] = pivot[_ze20].fillna(pivot[_z20]).abs()
    elif _ze20 in pivot.columns:
        df["chp_P"] = pivot[_ze20].abs()
    elif _z20 in pivot.columns:
        df["chp_P"] = pivot[_z20].abs()
    else:
        df["chp_P"] = float("nan")

    df["heat_total_P"] = _sum([f"{m}__P" for m in _HEAT_TOTAL])
    df["chp_heat_P"]   = _sum([f"{m}__P" for m in _CHP_HEAT])
    df["cool_output_P"]= _sum([f"{m}__P" for m in _COOL_OUTPUT])
    df["cool_elec_P"]  = _sum([f"{m}__P" for m in _COOL_ELEC])

    # 날씨
    df["Igm"] = pivot.get("WeatherStation.Weather__Igm", float("nan"))
    df["Ta"]  = pivot.get("WeatherStation.Weather__Ta",  float("nan"))

    # 파생 KPI
    total = df["grid_P"] + df["pv_P"] + df["chp_P"]
    df["self_sufficiency"] = (df["pv_P"] + df["chp_P"]) / total.replace(0, float("nan"))

    # COP: cool_elec == 0 방어
    df["cop"] = df["cool_output_P"] / df["cool_elec_P"].replace(0, float("nan"))

    return df.reset_index()


# ── 편의 함수 ─────────────────────────────────────────────────────

def load_recent(days: int = 7, freq: str = "1h") -> pd.DataFrame:
    """최근 N일 데이터 로드."""
    end   = datetime.now(tz=timezone.utc)
    start = end - pd.Timedelta(days=days)
    return load_reduced(start, end, freq=freq)


def load_range(start_str: str, end_str: str, freq: str = "1h") -> pd.DataFrame:
    """문자열 날짜로 로드. 예: '2022-01-01', '2022-02-01'"""
    tz = timezone.utc
    start = datetime.fromisoformat(start_str).replace(tzinfo=tz)
    end   = datetime.fromisoformat(end_str).replace(tzinfo=tz)
    return load_reduced(start, end, freq=freq)


def get_meter_list() -> pd.DataFrame:
    """ems.full_meter 조회 (읽기 전용)."""
    return _query("SELECT meter_urn, meter_group FROM ems.full_meter ORDER BY meter_urn;", ())


def get_anomaly_window(
    meter_urn: str,
    start: datetime,
    end: datetime,
    measurement: str = "P",
) -> pd.DataFrame:
    """특정 미터의 시계열 조회 (이상탐지 모델 입력용)."""
    sql = """
        SELECT ts, value
        FROM ems.cr_measurement_1h
        WHERE meter_urn = %s
          AND measurement = %s
          AND ts >= %s AND ts < %s
        ORDER BY ts;
    """
    df = _query(sql, (meter_urn, measurement, start, end))
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ_LOCAL)
    return df


# ── 테스트 ────────────────────────────────────────────────────────

if __name__ == "__main__":
    start_dt, end_dt = get_data_range()
    print(f"데이터 범위: {start_dt} ~ {end_dt}")

    # 데이터 마지막 7일 로드
    test_end   = end_dt
    test_start = end_dt - pd.Timedelta(days=7)
    print(f"\n테스트 구간: {test_start} ~ {test_end}")

    df = load_reduced(test_start, test_end)
    print(f"행 수: {len(df)}")
    if not df.empty:
        print(df[["ts", "grid_P", "pv_P", "chp_P", "cop", "self_sufficiency"]].tail(10).to_string(index=False))
        print(f"\n평균 자급률: {df['self_sufficiency'].mean():.1%}")
        print(f"COP 중앙값: {df['cop'].median():.2f}")
