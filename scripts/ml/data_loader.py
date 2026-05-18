"""DB에서 모델 학습용 데이터를 로드하고 피처를 생성하는 공통 모듈.

- 타겟: electricity/total/P (Grid 전기 소비량, W 단위)
- 학습 구간: Regime 5 (2020-09-15 ~ 2023-06-01) — 안정적 운영 구간
- 피처: 달력 + 기상(기온/일사량) + Lag + Rolling
"""

from __future__ import annotations

import os
import numpy as np
import pandas as pd
import psycopg
from dotenv import load_dotenv

load_dotenv()

CONNECT_KWARGS = {
    "host": os.environ["DB_HOST"],
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ["DB_NAME"],
    "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}

# Regime 5: 계량기 교체 완료 ~ 난방 현대화 이전
TRAIN_START = "2020-09-15"
TRAIN_END   = "2022-12-31"  # 학습
VAL_START   = "2023-01-01"  # 검증
VAL_END     = "2023-05-31"  # 검증 끝 (Regime 5 종료 직전)


def _query(sql: str) -> pd.DataFrame:
    with psycopg.connect(**CONNECT_KWARGS) as conn:
        return pd.read_sql(sql, conn)


def load_raw() -> pd.DataFrame:
    """electricity total + 기상 데이터를 시간별로 피벗하여 반환."""
    sql = """
        SELECT
            ts,
            category,
            subcategory,
            measurement,
            value
        FROM ems.reduced_measurement_1h
        WHERE
            (category = 'electricity' AND subcategory = 'total'  AND measurement = 'P')
            OR (category = 'weather'     AND subcategory = 'weather' AND measurement = 'Ta')
            OR (category = 'weather'     AND subcategory = 'weather' AND measurement = 'Igm')
        ORDER BY ts
    """
    df_long = _query(sql)
    df_long["col"] = df_long["subcategory"] + "_" + df_long["measurement"]
    df = df_long.pivot_table(index="ts", columns="col", values="value", aggfunc="first")
    df.index = pd.to_datetime(df.index, utc=True)
    df.sort_index(inplace=True)

    # 컬럼 이름 단순화
    df = df.rename(columns={
        "total_P": "grid_kw",
        "weather_Ta":   "temp_c",
        "weather_Igm": "solar_wm2",
    })
    # W → kW 변환
    df["grid_kw"] = df["grid_kw"] / 1000.0
    return df


def add_features(df: pd.DataFrame, lags: list[int] | None = None) -> pd.DataFrame:
    """달력 피처 + Lag + Rolling 피처 추가."""
    if lags is None:
        lags = [1, 2, 3, 24, 48, 168]  # 1h~3h, 1day, 2day, 1week

    ts = df.index
    df["hour"]       = ts.hour
    df["dayofweek"]  = ts.dayofweek        # 0=월, 6=일
    df["month"]      = ts.month
    df["is_weekend"] = (ts.dayofweek >= 5).astype(int)
    df["hour_sin"]   = np.sin(2 * np.pi * ts.hour / 24)
    df["hour_cos"]   = np.cos(2 * np.pi * ts.hour / 24)
    df["month_sin"]  = np.sin(2 * np.pi * ts.month / 12)
    df["month_cos"]  = np.cos(2 * np.pi * ts.month / 12)

    for lag in lags:
        df[f"lag_{lag}h"] = df["grid_kw"].shift(lag)

    df["roll_mean_24h"] = df["grid_kw"].shift(1).rolling(24).mean()
    df["roll_std_24h"]  = df["grid_kw"].shift(1).rolling(24).std()
    df["roll_mean_168h"] = df["grid_kw"].shift(1).rolling(168).mean()

    # 기상 결측 선형 보간 (단기 결측만)
    df["temp_c"]    = df["temp_c"].interpolate(method="linear", limit=6)
    df["solar_wm2"] = df["solar_wm2"].interpolate(method="linear", limit=6)

    return df


def get_train_val(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """학습/검증 구간 분리. 결측 제거."""
    df = df.dropna()
    train = df[(df.index >= TRAIN_START) & (df.index <= TRAIN_END)]
    val   = df[(df.index >= VAL_START)   & (df.index <= VAL_END)]
    return train, val


FEATURE_COLS = [
    "hour", "dayofweek", "month", "is_weekend",
    "hour_sin", "hour_cos", "month_sin", "month_cos",
    "temp_c", "solar_wm2",
    "lag_1h", "lag_2h", "lag_3h", "lag_24h", "lag_48h", "lag_168h",
    "roll_mean_24h", "roll_std_24h", "roll_mean_168h",
]
TARGET_COL = "grid_kw"
