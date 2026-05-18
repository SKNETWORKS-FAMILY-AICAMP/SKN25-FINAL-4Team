from __future__ import annotations

import pandas as pd

# EDA 확정 이상치 처리 기준

# 음수 누적값 절댓값 처리 (WQ 외 항목)
ABS_ENERGY_METERS = {
    "H1.Z12": ["WQ_out"],
    "H1.Z25": ["WQ_out"],
    "H2.Z65": ["W_out"],
    "H2.Z67": ["W_out"],
    "H2.ZE65": ["W3"],
    "H2.ZE74": ["W2"],
}

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

# 물리적 이상 기준 상수 정의

# 냉방 계량기 (Tdiff 양수 불가)
COOLING_METERS = {
    "H1.K11", "H1.K12", "H1.K14", "H1.K15", "H1.K16",
    "H2.K21", "V.K21",
}

# 난방 계량기 (Tdiff 음수 불가)
HEATING_METERS = {
    "H1.W11", "H1.W12",
}

# 전압 정상 범위 (EN 50160 230V±10%)
VOLTAGE_MIN = 207.0
VOLTAGE_MAX = 253.0

# 주파수 정상 범위 (EN 50160 50Hz±1%)
FREQ_MIN = 49.5
FREQ_MAX = 50.5

# 온도 정상 범위
TEMP_MIN = -20.0
TEMP_MAX = 120.0

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

    # 음수 누적값 절댓값 처리 (계량기별 개별 항목)
    for meter_urn, measurements in ABS_ENERGY_METERS.items():
        mask = (
            (df["meter_urn"] == meter_urn) &
            (df["measurement"].isin(measurements))
        )
        df.loc[mask, "value"] = df.loc[mask, "value"].abs()

    return df


def apply_physical_constraints(df: pd.DataFrame) -> pd.DataFrame:
    """4단계 보완: 물리적 기준 기반 이상치 처리."""
    df = df.copy()

    # 음수 무효전력(Q) 플래그
    q_mask = (df["measurement"] == "Q") & (df["value"] < 0)
    df.loc[q_mask, "quality_flag"] = "negative_reactive_power"

    # 냉각 계량기 Tdiff 양수 플래그
    cooling_tdiff_mask = (
        df["meter_urn"].isin(COOLING_METERS) &
        (df["measurement"] == "Tdiff") &
        (df["value"] > 0)
    )
    df.loc[cooling_tdiff_mask, "quality_flag"] = "cooling_tdiff_positive"

    # 난방 계량기 Tdiff 음수 플래그
    heating_tdiff_mask = (
        df["meter_urn"].isin(HEATING_METERS) &
        (df["measurement"] == "Tdiff") &
        (df["value"] < 0)
    )
    df.loc[heating_tdiff_mask, "quality_flag"] = "heating_tdiff_negative"

    # 온도 범위 초과 NaN 처리 (Trl, Tvl)
    temp_mask = (
        df["measurement"].isin(["Trl", "Tvl"]) &
        ((df["value"] < TEMP_MIN) | (df["value"] > TEMP_MAX))
    )
    df.loc[temp_mask, "value"] = float("nan")
    df.loc[temp_mask, "quality_flag"] = "temp_out_of_range"

    # 음수 유량(qv) 플래그
    qv_mask = (df["measurement"] == "qv") & (df["value"] < 0)
    df.loc[qv_mask, "quality_flag"] = "negative_flow"

    # 주파수 범위 초과 플래그
    freq_mask = (
        (df["measurement"] == "f") &
        ((df["value"] < FREQ_MIN) | (df["value"] > FREQ_MAX))
    )
    df.loc[freq_mask, "quality_flag"] = "freq_out_of_range"

    # 전압 범위 초과 플래그
    voltage_mask = (
        df["measurement"].isin(["U1", "U2", "U3"]) &
        ((df["value"] < VOLTAGE_MIN) | (df["value"] > VOLTAGE_MAX))
    )
    df.loc[voltage_mask, "quality_flag"] = "voltage_out_of_range"

    # PF 범위 초과 NaN 처리
    pf_mask = (
        df["measurement"].isin(["PF", "PF1", "PF2", "PF3"]) &
        ((df["value"] < -1) | (df["value"] > 1))
    )
    df.loc[pf_mask, "value"] = float("nan")
    df.loc[pf_mask, "quality_flag"] = "pf_out_of_range"

    return df

# -----------------------------------------------------------------------
# 5단계: 파생변수 생성
# -----------------------------------------------------------------------

# [자의적] lag ticks 기준 (팀 논의 필요)
LAG_TICKS = [1, 4, 96, 672]

# [자의적] rolling window 기준 (팀 논의 필요)
ROLLING_WINDOWS = {
    "1h": 4,
    "6h": 24,
    "24h": 96,
    "7d": 672,
}


def build_imbalance_features(df: pd.DataFrame) -> pd.DataFrame:
    """5단계: 3상 불균형 파생변수 생성.
    
    확정: I_imbalance, PF_imbalance, P_I_ratio
    """
    df = df.copy()
    result_frames = []

    for meter_urn, group in df.groupby("meter_urn"):
        # wide format으로 전환 (ts 기준)
        pivot = group.pivot_table(
            index="ts", columns="measurement", values="value", aggfunc="first"
        )

        # I_imbalance = std(I1, I2, I3) [확정]
        i_cols = [c for c in ["I1", "I2", "I3"] if c in pivot.columns]
        if len(i_cols) == 3:
            pivot["I_imbalance"] = pivot[i_cols].std(axis=1)

        # PF_imbalance = std(PF1, PF2, PF3) [확정]
        pf_cols = [c for c in ["PF1", "PF2", "PF3"] if c in pivot.columns]
        if len(pf_cols) == 3:
            pivot["PF_imbalance"] = pivot[pf_cols].std(axis=1)

        # P_I_ratio = P / mean(I1, I2, I3) [확정]
        if "P" in pivot.columns and len(i_cols) == 3:
            i_mean = pivot[i_cols].mean(axis=1)
            pivot["P_I_ratio"] = pivot["P"] / i_mean.replace(0, float("nan"))

        # delta_W = W.diff() [확정]
        if "W" in pivot.columns:
            pivot["delta_W"] = pivot["W"].diff()

        # Tdiff 재계산 = Tvl - Trl [자의적: 팀 논의 필요]
        if "Tvl" in pivot.columns and "Trl" in pivot.columns:
            pivot["Tdiff_calc"] = pivot["Tvl"] - pivot["Trl"]

        # long format으로 복원
        pivot = pivot.reset_index()
        pivot["meter_urn"] = meter_urn
        melted = pivot.melt(
            id_vars=["ts", "meter_urn"],
            var_name="measurement",
            value_name="value"
        )
        result_frames.append(melted)

    if not result_frames:
        return df

    return pd.concat(result_frames, ignore_index=True)


def build_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """5단계: 시간 피처 생성. [자의적: 팀 논의 필요]
    
    UTC 기준으로 생성. 로컬 타임 변환은 별도 확정 후 추가.
    """
    import numpy as np

    df = df.copy()
    ts = df["ts"]

    df["hour"] = ts.dt.hour
    df["day_of_week"] = ts.dt.dayofweek  # 0=월요일
    df["month"] = ts.dt.month
    df["is_weekend"] = ts.dt.dayofweek >= 5

    # 계절
    df["season"] = ts.dt.month.map(lambda m: (
        "winter" if m in (12, 1, 2) else
        "spring" if m in (3, 4, 5) else
        "summer" if m in (6, 7, 8) else
        "autumn"
    ))

    # 일주기 sin/cos encoding
    seconds_in_day = 24 * 3600
    day_seconds = ts.dt.hour * 3600 + ts.dt.minute * 60 + ts.dt.second
    df["sin_hour"] = np.sin(2 * np.pi * day_seconds / seconds_in_day)
    df["cos_hour"] = np.cos(2 * np.pi * day_seconds / seconds_in_day)

    # 주기 sin/cos encoding
    df["sin_dow"] = np.sin(2 * np.pi * ts.dt.dayofweek / 7)
    df["cos_dow"] = np.cos(2 * np.pi * ts.dt.dayofweek / 7)

    return df


def build_rolling_lag_features(
    series: pd.Series,
    feature_name: str,
) -> pd.DataFrame:
    """5단계: rolling 및 lag 피처 생성. [자의적: 팀 논의 필요]
    
    과거 방향으로만 계산. shift 사용 금지.
    fit/transform 분리는 호출 측에서 관리.
    """
    frames = {}

    # rolling
    for label, ticks in ROLLING_WINDOWS.items():
        rolling = series.rolling(window=ticks, min_periods=1)
        frames[f"{feature_name}__rolling_mean__{label}"] = rolling.mean()
        frames[f"{feature_name}__rolling_std__{label}"] = rolling.std()

    # lag
    for tick in LAG_TICKS:
        frames[f"{feature_name}__lag_{tick}tick"] = series.shift(tick)

    # 1tick difference
    frames[f"{feature_name}__diff_1tick"] = series.diff(1)

    return pd.DataFrame(frames, index=series.index)


def run_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    df = validate_input(df)
    df = basic_clean(df)
    df = apply_sign_convention(df)
    df = apply_ct_corrections(df)
    df = flag_anomalies(df)
    df = apply_eda_corrections(df)
    df = apply_physical_constraints(df)
    df = build_imbalance_features(df)  # 5단계 추가
    df = build_time_features(df)       # 5단계 추가 [자의적]
    return df

