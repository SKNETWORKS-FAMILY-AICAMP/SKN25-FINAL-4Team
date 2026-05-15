from __future__ import annotations

import pandas as pd


# 발전 계량기 7개
PRODUCTION_METERS = {
    "H1.Z20", "H1.ZE20",
    "V.Z84", "V.ZE84",
    "H1.Z310", "H2.Z311", "H3.Z312",
}

# 변류기 오류 보정 기준
CT_CORRECTIONS = {
    "H2.Z311": {
        "factor": 0.8,
        "start": "2020-06-25",
        "end": "2021-03-01",
    },
    "H4.ZE50": {
        "factor": 0.75,
        "start": "2022-03-22",
        "end": None,
    },
    "H4.ZE51": {
        "factor": 0.75,
        "start": "2022-03-22",
        "end": None,
    },
}

# H1.Z19 0값 처리 시작 시각
H1Z19_ZERO_START = "2022-03-01 08:15:00+00:00"

# COVID lockdown 구간
COVID_START = "2020-03-16"
COVID_END = "2021-01-17"


def validate_input(df: pd.DataFrame) -> pd.DataFrame:
    """1단계: 기본 입력 검증. registry 미등록 제외는 DB 조회 시 처리."""
    required_cols = {"ts", "meter_urn", "measurement", "value"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")
    return df


def basic_clean(df: pd.DataFrame) -> pd.DataFrame:
    """2단계: 기본 정제."""
    # timestamp UTC 정렬
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values("ts").reset_index(drop=True)

    # 중복 제거
    df = df.drop_duplicates(subset=["ts", "meter_urn", "measurement"])

    # NULL 제거
    df = df.dropna(subset=["value"])

    return df


def apply_sign_convention(df: pd.DataFrame) -> pd.DataFrame:
    """2단계: 부호 규약 적용. 소비 계량기 음수값 품질 플래그."""
    df = df.copy()
    is_production = df["meter_urn"].isin(PRODUCTION_METERS)
    is_consumption = ~is_production & (df["meter_urn"] != "WeatherStation.Weather")

    df["quality_flag"] = None
    df.loc[is_consumption & (df["value"] < 0), "quality_flag"] = "negative_consumption"

    return df


def apply_ct_corrections(df: pd.DataFrame) -> pd.DataFrame:
    """3단계: 변류기 오류 구간 보정."""
    df = df.copy()

    for meter_urn, cfg in CT_CORRECTIONS.items():
        mask = df["meter_urn"] == meter_urn
        mask &= df["ts"] >= pd.Timestamp(cfg["start"], tz="UTC")
        if cfg["end"]:
            mask &= df["ts"] < pd.Timestamp(cfg["end"], tz="UTC")
        df.loc[mask, "value"] *= cfg["factor"]

    # H1.Z19 0값 처리
    mask_z19 = (
        (df["meter_urn"] == "H1.Z19") &
        (df["ts"] >= pd.Timestamp(H1Z19_ZERO_START))
    )
    df.loc[mask_z19, "value"] = 0.0
    df.loc[mask_z19, "quality_flag"] = "ct_zero"

    return df


def flag_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """4단계: 이상 구간 플래그."""
    df = df.copy()
    df["is_building_event"] = False
    df["is_outage"] = False
    df["is_iqr_outlier"] = False

    # COVID lockdown 플래그
    covid_mask = (
        (df["ts"] >= pd.Timestamp(COVID_START, tz="UTC")) &
        (df["ts"] <= pd.Timestamp(COVID_END, tz="UTC"))
    )
    df.loc[covid_mask, "is_building_event"] = True

    # 0값 플래그
    df.loc[df["value"] == 0, "is_outage"] = True

    # IQR 이상치 플래그 (measurement별)
    for (meter, meas), group in df.groupby(["meter_urn", "measurement"]):
        q1 = group["value"].quantile(0.25)
        q3 = group["value"].quantile(0.75)
        iqr = q3 - q1
        upper = q3 + 1.5 * iqr
        lower = q1 - 1.5 * iqr
        outlier_idx = group[(group["value"] > upper) | (group["value"] < lower)].index
        df.loc[outlier_idx, "is_iqr_outlier"] = True

    return df


def run_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """전처리 파이프라인 전체 실행."""
    df = validate_input(df)
    df = basic_clean(df)
    df = apply_sign_convention(df)
    df = apply_ct_corrections(df)
    df = flag_anomalies(df)
    return df