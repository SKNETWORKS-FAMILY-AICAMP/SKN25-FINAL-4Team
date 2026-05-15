"""EMS feature engineering module.
 
feature_contract.md 기준으로 측정 row에서 모델 입력 피처를 생성한다.
Meter set 결정은 EMSOntology helper를 통해 수행하며 하드코딩 금지.
Wide format 전환은 모델 입력 직전에만 수행한다.
"""
 
from __future__ import annotations
 
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
 
import numpy as np
import pandas as pd
 
from ems.db import fetch_measurements, load_env
from ems.ontology import EMSOntology
 
logger = logging.getLogger(__name__)
 
ResolutionCode = Literal["15min", "1h", "1min"]
 
# feature_contract.md 5.3 우선 feature group
FEATURE_GROUPS = [
    "central_cooling",
    "server_power",
    "emission_lab",
    "pv",
    "chp",
    "local_cooling",
    "weather_station",
]
 
# feature_contract.md 9.2 rolling window 후보
ROLLING_WINDOWS_15MIN = {
    "1h": 4,
    "6h": 24,
    "24h": 96,
    "7d": 672,
}
 
LAG_TICKS = [1, 4, 96]  # 15min 기준: 15분, 1시간, 24시간
 
 
# ---------------------------------------------------------------------------
# Feature metadata
# ---------------------------------------------------------------------------
 
@dataclass
class FeatureMetadata:
    """feature_contract.md 15절 필수 항목."""
 
    feature_name: str
    feature_family: str
    resolution_code: ResolutionCode
    source_relation: str
    meter_set_rule: str
    meter_urns: list[str]
    measurement: str
    unit: str
    aggregation: str
    window: str
    redundancy_policy: str
    fit_period: str
    transform_period: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    code_version: str = ""
 
 
# ---------------------------------------------------------------------------
# DB 조회 헬퍼
# ---------------------------------------------------------------------------
 
def _fetch_group(
    meter_urns: list[str],
    measurement: str,
    start_ts: str,
    end_ts: str,
    resolution: ResolutionCode,
) -> pd.DataFrame:
    """여러 meter의 long format row를 DB에서 읽어 반환한다."""
    load_env()
    frames: list[pd.DataFrame] = []
    for urn in meter_urns:
        try:
            df = fetch_measurements(urn, measurement, start_ts, end_ts, resolution)
            frames.append(df)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DB 조회 실패: meter=%s measurement=%s error=%s", urn, measurement, exc)
    if not frames:
        return pd.DataFrame(columns=["ts", "meter_urn", "measurement", "value"])
    result = pd.concat(frames, ignore_index=True)
    result["ts"] = pd.to_datetime(result["ts"], utc=True)
    return result.sort_values("ts").reset_index(drop=True)
 
 
# ---------------------------------------------------------------------------
# 5. Group aggregate feature
# ---------------------------------------------------------------------------
 
def build_group_aggregate(
    kg: EMSOntology,
    group: str,
    measurement: str,
    start_ts: str,
    end_ts: str,
    resolution: ResolutionCode = "15min",
    exclude_redundant: bool = True,
    domain: str | None = None,
    role: str | None = None,
) -> tuple[pd.DataFrame, FeatureMetadata]:
    """feature_contract.md 16.1 group aggregate baseline.
 
    Parameters
    ----------
    kg:
        EMSOntology helper 인스턴스.
    group:
        equipment group code (예: ``central_cooling``).
    measurement:
        측정 항목 코드 (예: ``P``).
    start_ts, end_ts:
        조회 구간. 반개구간 ``[start_ts, end_ts)``.
    resolution:
        해상도. 기본값 ``15min``.
    exclude_redundant:
        True이면 redundant endpoint를 aggregate에서 제외한다.
    domain, role:
        ontology helper 필터. None이면 group 전체를 사용한다.
 
    Returns
    -------
    (DataFrame, FeatureMetadata)
        DataFrame columns: ``ts``, ``<feature_name>``.
    """
    meter_urns = kg.get_feature_meter_set(
        group=group,
        domain=domain,
        role=role,
        exclude_redundant=exclude_redundant,
    )
    if not meter_urns:
        raise ValueError(f"ontology에서 meter를 찾을 수 없습니다: group={group} domain={domain} role={role}")
 
    logger.info(
        "group aggregate: group=%s measurement=%s meters=%d exclude_redundant=%s",
        group,
        measurement,
        len(meter_urns),
        exclude_redundant,
    )
 
    raw = _fetch_group(meter_urns, measurement, start_ts, end_ts, resolution)
    if raw.empty:
        logger.warning("DB 조회 결과 없음: group=%s measurement=%s", group, measurement)
 
    # feature_contract.md 11절 naming 규칙
    redundancy_suffix = "sum_primary" if exclude_redundant else "sum"
    feature_name = f"group__{group}__{measurement}__{'sum_primary' if exclude_redundant else 'sum'}__{resolution}"
 
    agg = (
        raw.groupby("ts")["value"]
        .sum()
        .rename(feature_name)
        .reset_index()
    )
 
    redundancy_policy = "exclude_redundant" if exclude_redundant else "include_all"
    meter_set_rule = (
        f"kg.get_feature_meter_set(group='{group}', domain={domain!r}, "
        f"role={role!r}, exclude_redundant={exclude_redundant})"
    )
    source_relation = f"ems.cr_measurement_{resolution}"
 
    meta = FeatureMetadata(
        feature_name=feature_name,
        feature_family="aggregate",
        resolution_code=resolution,
        source_relation=source_relation,
        meter_set_rule=meter_set_rule,
        meter_urns=meter_urns,
        measurement=measurement,
        unit="",  # measurement definition에서 별도 확인
        aggregation="sum",
        window=resolution,
        redundancy_policy=redundancy_policy,
        fit_period="",
        transform_period=f"[{start_ts}, {end_ts})",
    )
 
    return agg, meta
 
 
# ---------------------------------------------------------------------------
# 6. Redundancy comparison feature
# ---------------------------------------------------------------------------
 
def build_redundancy_diff(
    kg: EMSOntology,
    group: str,
    measurement: str,
    start_ts: str,
    end_ts: str,
    resolution: ResolutionCode = "15min",
) -> tuple[pd.DataFrame, list[FeatureMetadata]]:
    """feature_contract.md 16.3 redundancy comparison baseline.
 
    Returns
    -------
    (DataFrame, list[FeatureMetadata])
        DataFrame columns: ``ts``, pair diff feature columns.
    """
    pairs = kg.get_redundancy_pairs(group=group)
    if not pairs:
        raise ValueError(f"redundancy pair 없음: group={group}")
 
    all_urns = list({urn for pair in pairs for urn in (pair["primary_meter"], pair["redundant_meter"])})
    raw = _fetch_group(all_urns, measurement, start_ts, end_ts, resolution)
 
    # pivot: columns = meter_urn
    pivot = raw.pivot_table(index="ts", columns="meter_urn", values="value", aggfunc="first")
 
    frames: list[pd.DataFrame] = [pivot.reset_index()[["ts"]]]
    metas: list[FeatureMetadata] = []
 
    source_relation = f"ems.cr_measurement_{resolution}"
 
    for pair in pairs:
        primary = pair["primary_meter"]
        redundant = pair["redundant_meter"]
 
        # feature_contract.md 11절: . → _ 치환
        p_code = primary.replace(".", "_")
        r_code = redundant.replace(".", "_")
        feature_name = f"pair__{p_code}_{r_code}__{measurement}__abs_diff__{resolution}"
 
        if primary not in pivot.columns or redundant not in pivot.columns:
            logger.warning("redundancy pair 데이터 없음: %s / %s", primary, redundant)
            continue
 
        abs_diff = (pivot[primary] - pivot[redundant]).abs().rename(feature_name)
        frames.append(abs_diff.reset_index(drop=True))
 
        meta = FeatureMetadata(
            feature_name=feature_name,
            feature_family="pair_comparison",
            resolution_code=resolution,
            source_relation=source_relation,
            meter_set_rule=f"kg.get_redundancy_pairs(group='{group}')",
            meter_urns=[primary, redundant],
            measurement=measurement,
            unit="",
            aggregation="abs_diff",
            window=resolution,
            redundancy_policy="pair_comparison",
            fit_period="",
            transform_period=f"[{start_ts}, {end_ts})",
        )
        metas.append(meta)
 
    result = pd.concat(frames, axis=1)
    return result, metas
 
 
# ---------------------------------------------------------------------------
# 7. Weather external feature
# ---------------------------------------------------------------------------
 
def build_weather_features(
    kg: EMSOntology,
    measurements: list[str],
    start_ts: str,
    end_ts: str,
    resolution: ResolutionCode = "15min",
    rolling_window_ticks: int | None = None,
) -> tuple[pd.DataFrame, list[FeatureMetadata]]:
    """feature_contract.md 16.2 weather external baseline.
 
    Parameters
    ----------
    measurements:
        기상 측정 항목 목록 (예: ``["Ta", "Igm", "Ah"]``).
    rolling_window_ticks:
        None이면 원 feature만 반환한다. 정수이면 rolling mean feature를 추가한다.
    """
    meter_urns = kg.get_feature_meter_set(
        group="weather_station",
        domain="weather",
        role="weather",
        exclude_redundant=False,
    )
    if not meter_urns:
        raise ValueError("ontology에서 weather_station meter를 찾을 수 없습니다.")
 
    source_relation = f"ems.cr_measurement_{resolution}"
    frames: list[pd.DataFrame] = []
    metas: list[FeatureMetadata] = []
 
    for meas in measurements:
        raw = _fetch_group(meter_urns, meas, start_ts, end_ts, resolution)
        if raw.empty:
            logger.warning("기상 데이터 없음: measurement=%s", meas)
            continue
 
        agg = raw.groupby("ts")["value"].mean()
 
        # 원 feature
        feature_name = f"weather__station__{meas}__mean__{resolution}"
        frames.append(agg.rename(feature_name))
        metas.append(FeatureMetadata(
            feature_name=feature_name,
            feature_family="weather_external",
            resolution_code=resolution,
            source_relation=source_relation,
            meter_set_rule="kg.get_feature_meter_set(group='weather_station', domain='weather', role='weather')",
            meter_urns=meter_urns,
            measurement=meas,
            unit="",
            aggregation="mean",
            window=resolution,
            redundancy_policy="include_all",
            fit_period="",
            transform_period=f"[{start_ts}, {end_ts})",
        ))
 
        # rolling mean feature
        if rolling_window_ticks is not None:
            window_label = f"{rolling_window_ticks}tick"
            rolling_name = f"weather__station__{meas}__rolling_mean__{window_label}"
            rolling = agg.rolling(window=rolling_window_ticks, min_periods=1).mean().rename(rolling_name)
            frames.append(rolling)
            metas.append(FeatureMetadata(
                feature_name=rolling_name,
                feature_family="weather_external",
                resolution_code=resolution,
                source_relation=source_relation,
                meter_set_rule="kg.get_feature_meter_set(group='weather_station', domain='weather', role='weather')",
                meter_urns=meter_urns,
                measurement=meas,
                unit="",
                aggregation="rolling_mean",
                window=window_label,
                redundancy_policy="include_all",
                fit_period="",
                transform_period=f"[{start_ts}, {end_ts})",
            ))
 
    if not frames:
        return pd.DataFrame(), metas
 
    result = pd.concat(frames, axis=1).reset_index()
    result.rename(columns={"ts": "ts"}, inplace=True)
    return result, metas
 
 
# ---------------------------------------------------------------------------
# 8. Calendar feature
# ---------------------------------------------------------------------------
 
def build_calendar_features(ts_index: pd.DatetimeIndex) -> pd.DataFrame:
    """feature_contract.md 9.1 calendar feature.
 
    DST 검증 전에는 UTC 기준으로 생성한다.
    Local time feature는 별도 변환 기준 확정 후 추가한다.
    """
    df = pd.DataFrame({"ts": ts_index})
    df["hour"] = ts_index.hour
    df["day_of_week"] = ts_index.dayofweek  # 0=월요일
    df["month"] = ts_index.month
    df["is_weekend"] = ts_index.dayofweek >= 5
    df["season"] = ts_index.month.map(_month_to_season)
 
    # 일주기 sin/cos encoding
    seconds_in_day = 24 * 3600
    day_seconds = ts_index.hour * 3600 + ts_index.minute * 60 + ts_index.second
    df["sin_hour"] = np.sin(2 * np.pi * day_seconds / seconds_in_day)
    df["cos_hour"] = np.cos(2 * np.pi * day_seconds / seconds_in_day)
 
    # 주기 sin/cos encoding
    df["sin_dow"] = np.sin(2 * np.pi * ts_index.dayofweek / 7)
    df["cos_dow"] = np.cos(2 * np.pi * ts_index.dayofweek / 7)
 
    return df
 
 
def _month_to_season(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"
 
 
# ---------------------------------------------------------------------------
# 9. Rolling feature
# ---------------------------------------------------------------------------
 
def build_rolling_features(
    series: pd.Series,
    feature_name_base: str,
    resolution: ResolutionCode = "15min",
    windows: dict[str, int] | None = None,
    lag_ticks: list[int] | None = None,
) -> pd.DataFrame:
    """feature_contract.md 9.2 rolling feature.
 
    과거 방향으로만 계산한다 (shift 사용 금지).
    Live replay 구간에서는 학습 구간 fit 값을 transform에만 사용해야 한다.
    이 함수는 값 계산만 수행하며 fit/transform 분리는 호출 측에서 관리한다.
    """
    _windows = windows if windows is not None else ROLLING_WINDOWS_15MIN
    _lags = lag_ticks if lag_ticks is not None else LAG_TICKS
 
    frames: dict[str, pd.Series] = {}
 
    for label, ticks in _windows.items():
        rolling = series.rolling(window=ticks, min_periods=1)
        frames[f"{feature_name_base}__rolling_mean__{label}"] = rolling.mean()
        frames[f"{feature_name_base}__rolling_std__{label}"] = rolling.std()
 
    for tick in _lags:
        frames[f"{feature_name_base}__lag_{tick}tick"] = series.shift(tick)
 
    # 1tick difference
    frames[f"{feature_name_base}__diff_1tick"] = series.diff(1)
 
    return pd.DataFrame(frames, index=series.index)
 
 
# ---------------------------------------------------------------------------
# 10. Wide format 전환
# ---------------------------------------------------------------------------
 
def to_wide(
    frames: list[pd.DataFrame],
    ts_col: str = "ts",
) -> pd.DataFrame:
    """feature_contract.md 12절 wide format 전환.
 
    모델 입력 직전에 호출한다.
    모든 frame은 ts 기준으로 merge한다.
    """
    if not frames:
        raise ValueError("frames가 비어 있습니다.")
 
    result = frames[0]
    if ts_col not in result.columns:
        result = result.reset_index()
 
    for frame in frames[1:]:
        if ts_col not in frame.columns:
            frame = frame.reset_index()
        result = result.merge(frame, on=ts_col, how="outer")
 
    result = result.sort_values(ts_col).reset_index(drop=True)
    return result
 
 
# ---------------------------------------------------------------------------
# 11. Feature metadata 저장
# ---------------------------------------------------------------------------
 
def save_feature_metadata(
    metas: list[FeatureMetadata],
    output_dir: Path,
    filename: str = "feature_metadata.csv",
) -> Path:
    """feature_contract.md 14절 산출물 저장."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [vars(m) for m in metas]
    # meter_urns list → 문자열
    for row in rows:
        row["meter_urns"] = "|".join(row["meter_urns"])
    df = pd.DataFrame(rows)
    path = output_dir / filename
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("feature metadata 저장: %s (%d rows)", path, len(df))
    return path
 