"""
preprocessing_v2.py
기존 preprocessing.py 대비 변경사항:
- 게이트웨이 장애 구간 플래그 추가 (is_gateway_failure)
- 해당 구간은 인공 보정 데이터(과거 복사)이므로 모델 학습에서 제외 권장
"""
from __future__ import annotations
import pandas as pd
from ems.preprocessing import (
    validate_input,
    basic_clean,
    apply_sign_convention,
    apply_ct_corrections,
    flag_anomalies,
    apply_eda_corrections,
    apply_physical_constraints,
)

# 게이트웨이 장애 구간 (인공 보정 데이터 구간)
GATEWAY_FAILURE_RANGES = [
    {"start": "2020-02-13", "end": "2020-03-06", "name": "Workshop Gateway Failure #1"},
    {"start": "2020-08-20", "end": "2020-09-17", "name": "Emission Lab Gateway Failure"},
    {"start": "2021-11-15", "end": "2021-12-10", "name": "Distribution Gateway Failure"},
    {"start": "2022-05-06", "end": "2022-07-14", "name": "Workshop Gateway Failure #2"},
]


def flag_gateway_failures(df: pd.DataFrame) -> pd.DataFrame:
    """게이트웨이 장애 구간 플래그 추가."""
    df = df.copy()
    df["is_gateway_failure"] = False
    df["gateway_failure_name"] = None

    for cfg in GATEWAY_FAILURE_RANGES:
        mask = (
            (df["ts"] >= pd.Timestamp(cfg["start"], tz="UTC")) &
            (df["ts"] < pd.Timestamp(cfg["end"], tz="UTC"))
        )
        df.loc[mask, "is_gateway_failure"] = True
        df.loc[mask, "gateway_failure_name"] = cfg["name"]

    n_flagged = df["is_gateway_failure"].sum()
    print(f"  게이트웨이 장애 구간 플래그: {n_flagged}행")
    return df


def run_pipeline_v2(df: pd.DataFrame, exclude_gateway: bool = True) -> pd.DataFrame:
    """
    v2 파이프라인.
    exclude_gateway=True이면 게이트웨이 장애 구간을 제외하고 반환.
    """
    df = validate_input(df)
    df = basic_clean(df)
    df = apply_sign_convention(df)
    df = apply_ct_corrections(df)
    df = flag_anomalies(df)
    df = apply_eda_corrections(df)
    df = apply_physical_constraints(df)
    df = flag_gateway_failures(df)

    if exclude_gateway:
        before = len(df)
        df = df[~df["is_gateway_failure"]].copy()
        print(f"  게이트웨이 장애 구간 제외: {before - len(df)}행 제거")

    return df