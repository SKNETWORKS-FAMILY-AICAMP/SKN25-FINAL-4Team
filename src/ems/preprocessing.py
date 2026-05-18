from __future__ import annotations

import pandas as pd

# EDA 확정 이상치 처리 기준

# 절댓값 처리: 변류기 방향 오류 (전 기간)
ABS_VALUE_METERS = {
    "H1.Z15": ["I1", "I2", "I3", "P"],
    "H1.Z28": ["I1", "I2", "I3", "P"],
}

# WQ 음수 고착 계량기 (전 기간 절댓값)
ABS_WQ_METERS = {
    "H1.Z10", "H2.T.Z30", "H2.Z64", "H2.Z65",
    "H3.Z40", "H3.Z41", "H4.Z51",
}

# 제거 대상 (전 기간)
DROP_METER_MEASUREMENTS = {
    "V.Z81": ["W_in"],
}

# NaN 처리: 특정 구간
NAN_INTERVALS = [
    {
        "meter_urn": "H2.T.Z34",
        "measurements": ["U1", "U2", "U3", "f"],
        "start": "2020-03-07",
        "end": "2020-03-09",
    },
    {
        "meter_urn": "H2.ZE66",
        "measurements": ["PF"],
        "start": "2022-03-01",
        "end": None,
    },
    {
        "meter_urn": "H2.ZE67",
        "measurements": ["PF"],
        "start": "2022-03-01",
        "end": None,
    },
]

# 플래그 대상: 단상 전압 이상
VOLTAGE_FLAG_INTERVALS = [
    {
        "meter_urn": "H2.ZE74",
        "measurement": "U2",
        "start": "2022-03-18",
        "end": "2022-04-09",
        "flag": "single_phase_voltage_anomaly",
    },
]

# 모델 제외 대상
MODEL_EXCLUDE = {
    "H1.K15": ["Tdiff"],
    "H1.K12": None,  # 2022년 이후 전체 제외
}

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

def apply_eda_corrections(df: pd.DataFrame) -> pd.DataFrame:
    """4단계 보완: EDA에서 확정된 이상치 처리."""
    df = df.copy()

    # 절댓값 처리: 변류기 방향 오류
    for meter_urn, measurements in ABS_VALUE_METERS.items():
        mask = (df["meter_urn"] == meter_urn) & (df["measurement"].isin(measurements))
        df.loc[mask, "value"] = df.loc[mask, "value"].abs()

    # WQ 음수 고착 절댓값 처리
    wq_mask = (
        df["meter_urn"].isin(ABS_WQ_METERS) &
        (df["measurement"] == "WQ")
    )
    df.loc[wq_mask, "value"] = df.loc[wq_mask, "value"].abs()

    # 제거 대상
    for meter_urn, measurements in DROP_METER_MEASUREMENTS.items():
        drop_mask = (
            (df["meter_urn"] == meter_urn) &
            (df["measurement"].isin(measurements))
        )
        df = df[~drop_mask]

    # NaN 처리
    for interval in NAN_INTERVALS:
        mask = (
            (df["meter_urn"] == interval["meter_urn"]) &
            (df["measurement"].isin(interval["measurements"])) &
            (df["ts"] >= pd.Timestamp(interval["start"], tz="UTC"))
        )
        if interval["end"]:
            mask &= df["ts"] < pd.Timestamp(interval["end"], tz="UTC")
        df.loc[mask, "value"] = float("nan")
        df.loc[mask, "quality_flag"] = "eda_nan"

    # 플래그 처리
    for flag_cfg in VOLTAGE_FLAG_INTERVALS:
        mask = (
            (df["meter_urn"] == flag_cfg["meter_urn"]) &
            (df["measurement"] == flag_cfg["measurement"]) &
            (df["ts"] >= pd.Timestamp(flag_cfg["start"], tz="UTC")) &
            (df["ts"] < pd.Timestamp(flag_cfg["end"], tz="UTC"))
        )
        df.loc[mask, "quality_flag"] = flag_cfg["flag"]

    return df



def run_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    df = validate_input(df)
    df = basic_clean(df)
    df = apply_sign_convention(df)
    df = apply_ct_corrections(df)
    df = flag_anomalies(df)
    df = apply_eda_corrections(df)  # 추가
    return df

