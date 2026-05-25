"""Preprocessing pipeline for transformer group meters.

Rule-based anomaly handling based on physical_anomaly_report_260521:
  Ch 1 — Physical Violation  : set to NaN (remove)
  Ch 2 — CT Reversal         : apply abs() transform
  Ch 3 — Measurement Limit   : add flag column, keep value

Transformer meters in DB:
  V.Z81, V.Z82                         (Parking lot transformers)
  H2.Z35 / H2.Z351  (redundant pair)
  H2.Z36 / H2.Z361  (redundant pair)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config.meter_metadata import get_metadata, get_meters_by_group
from fetch_h1z16_with_weather import (
    build_engine,
    fetch_meter_data,
    fetch_weather_data,
    get_measurement_columns,
)

# ── 리포트 Ch 1 임계값 ──────────────────────────────────────────────
PF_PHYSICAL_LIMIT = 1.5         # abs(PF) > 1.5 → 물리 법칙 위반
U_MAX_V = 1000.0                # 저압 배전망 상한 (V)
F_MIN_HZ = 47.0                 # 계통 주파수 하한 (Hz)
F_MAX_HZ = 53.0                 # 계통 주파수 상한 (Hz)
W_IN_MIN_KWH = -1_000_000.0    # 소비 계량기 누적 음수 한계 (kWh)

# ── 리포트 Ch 2 임계값 ──────────────────────────────────────────────
CT_REVERSAL_RATIO = 0.99        # 음수 비율 >= 99% → CT 역접속 판정

# ── 리포트 Ch 3 임계값 ──────────────────────────────────────────────
PF_MEASUREMENT_LIMIT = 1.0      # abs(PF) > 1.0 ~ <= 1.5 → 측정 한계
F_DEVIATION_LIMIT_HZ = 0.5     # abs(f - 50) > 0.5Hz → 측정 한계


# ── Ch 1: 물리 법칙 위반 → NaN ──────────────────────────────────────

def apply_physical_violation_rules(
    df: pd.DataFrame,
    metadata: dict[str, Any],
    meter_urn: str,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Ch 1 — 물리적으로 불가능한 값을 NaN으로 교체."""
    out = df.copy()
    log: list[dict[str, Any]] = []

    def _nan_mask(col: str, mask: pd.Series, detail: str) -> None:
        if col not in out.columns:
            return
        n = int(mask.sum())
        if n == 0:
            return
        out.loc[mask, col] = pd.NA
        log.append({
            "chapter": 1,
            "type": "PHYSICAL_VIOLATION",
            "meter_urn": meter_urn,
            "column": col,
            "n_affected": n,
            "action": "nan",
            "detail": detail,
        })

    # PF > |1.5|
    for col in ["PF", "PF1", "PF2", "PF3"]:
        _nan_mask(col, out[col].abs() > PF_PHYSICAL_LIMIT if col in out.columns else pd.Series(dtype=bool),
                  f"abs({col}) > {PF_PHYSICAL_LIMIT}")

    # U < 0V 또는 U > 1000V
    for col in ["U1", "U2", "U3"]:
        if col in out.columns:
            _nan_mask(col, out[col] < 0, f"{col} < 0V")
            _nan_mask(col, out[col] > U_MAX_V, f"{col} > {U_MAX_V}V")

    # f < 47Hz 또는 f > 53Hz
    if "f" in out.columns:
        _nan_mask("f", out["f"] < F_MIN_HZ, f"f < {F_MIN_HZ}Hz")
        _nan_mask("f", out["f"] > F_MAX_HZ, f"f > {F_MAX_HZ}Hz")

    # W_in < -1,000,000 kWh (소비 계량기 오버플로우)
    if metadata.get("energy_type") == "consumption" and "W_in" in out.columns:
        _nan_mask("W_in", out["W_in"] < W_IN_MIN_KWH,
                  f"W_in < {W_IN_MIN_KWH:,} kWh (소비 계량기 오버플로우)")

    return out, log


# ── Ch 2: CT 역접속 → abs() 변환 ────────────────────────────────────

def detect_and_fix_ct_reversal(
    df: pd.DataFrame,
    metadata: dict[str, Any],
    meter_urn: str,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Ch 2 — 소비 계량기 전류가 연중 99% 이상 음수이면 abs() 변환."""
    if metadata.get("energy_type") != "consumption":
        return df, []

    out = df.copy()
    log: list[dict[str, Any]] = []

    for col in ["I1", "I2", "I3"]:
        if col not in out.columns:
            continue
        series = out[col].dropna()
        if series.empty:
            continue
        neg_ratio = float((series < 0).sum()) / len(series)
        if neg_ratio >= CT_REVERSAL_RATIO:
            out[col] = out[col].abs()
            log.append({
                "chapter": 2,
                "type": "CT_REVERSAL",
                "meter_urn": meter_urn,
                "column": col,
                "n_affected": int((series < 0).sum()),
                "neg_ratio": round(neg_ratio, 4),
                "action": "abs",
                "detail": f"{col} 음수 비율 {neg_ratio:.1%} >= {CT_REVERSAL_RATIO:.0%} → abs() 변환",
            })

    return out, log


# ── Ch 3: 측정 한계 초과 → flag 컬럼 추가 ───────────────────────────

def apply_measurement_limit_flags(
    df: pd.DataFrame,
    meter_urn: str,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Ch 3 — 측정 한계 초과 행에 flag 컬럼 추가. 값은 보존."""
    out = df.copy()
    log: list[dict[str, Any]] = []

    # PF: 1.0 < abs(PF) <= 1.5
    pf_cols = [c for c in ["PF", "PF1", "PF2", "PF3"] if c in out.columns]
    if pf_cols:
        combined_mask = pd.Series(False, index=out.index)
        for col in pf_cols:
            mask = (out[col].abs() > PF_MEASUREMENT_LIMIT) & (out[col].abs() <= PF_PHYSICAL_LIMIT)
            combined_mask |= mask.fillna(False)
        out["flag_pf_measurement_limit"] = combined_mask
        n = int(combined_mask.sum())
        if n > 0:
            log.append({
                "chapter": 3,
                "type": "MEASUREMENT_LIMIT",
                "meter_urn": meter_urn,
                "column": "PF/PF1/PF2/PF3",
                "n_affected": n,
                "action": "flag",
                "detail": f"1.0 < abs(PF) <= 1.5 → flag_pf_measurement_limit",
            })
    else:
        out["flag_pf_measurement_limit"] = False

    # f: abs(f - 50) > 0.5Hz
    if "f" in out.columns:
        f_mask = (out["f"] - 50.0).abs() > F_DEVIATION_LIMIT_HZ
        out["flag_f_measurement_limit"] = f_mask.fillna(False)
        n = int(f_mask.fillna(False).sum())
        if n > 0:
            log.append({
                "chapter": 3,
                "type": "MEASUREMENT_LIMIT",
                "meter_urn": meter_urn,
                "column": "f",
                "n_affected": n,
                "action": "flag",
                "detail": f"abs(f - 50) > {F_DEVIATION_LIMIT_HZ}Hz → flag_f_measurement_limit",
            })
    else:
        out["flag_f_measurement_limit"] = False

    return out, log


# ── 데이터 로드 ──────────────────────────────────────────────────────

def fetch_transformer_data(engine, meter_urn: str) -> pd.DataFrame:
    df_meter = fetch_meter_data(engine, meter_urn).copy()
    df_weather = fetch_weather_data(engine).copy()

    df = df_meter.merge(df_weather, on="ts", how="left").sort_values("ts").reset_index(drop=True)

    numeric_cols = [*get_measurement_columns(meter_urn), "Ta", "Igm"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ── 단일 계량기 전처리 ───────────────────────────────────────────────

def preprocess_transformer_meter(
    meter_urn: str,
    print_progress: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    """transformer 계량기 1기에 대해 Ch1~Ch3 룰을 순서대로 적용.

    Returns
    -------
    df_clean       : 전처리 완료 DataFrame
    df_before      : 원본 DataFrame
    anomaly_log    : Ch1~Ch3 탐지 및 처리 이력
    ct_reversal    : CT 역접속이 확인된 컬럼 목록 (Ch2 항목만)
    """
    metadata = get_metadata(meter_urn)
    if metadata is None:
        raise ValueError(f"Metadata not found for {meter_urn}")
    if metadata.get("group_name") != "transformer":
        raise ValueError(f"{meter_urn} is not a transformer meter (group_name={metadata.get('group_name')})")

    if print_progress:
        print(f"[{meter_urn}] 데이터 로드 중...")
    engine = build_engine()
    df = fetch_transformer_data(engine, meter_urn)
    df_before = df.copy()

    if print_progress:
        print(f"[{meter_urn}] Ch1 — 물리 법칙 위반 NaN 처리...")
    df, log_ch1 = apply_physical_violation_rules(df, metadata, meter_urn)

    if print_progress:
        print(f"[{meter_urn}] Ch2 — CT 역접속 탐지 및 abs() 변환...")
    df, log_ch2 = detect_and_fix_ct_reversal(df, metadata, meter_urn)

    if print_progress:
        print(f"[{meter_urn}] Ch3 — 측정 한계 초과 flag 부여...")
    df, log_ch3 = apply_measurement_limit_flags(df, meter_urn)

    anomaly_log = log_ch1 + log_ch2 + log_ch3
    ct_reversal = [e for e in log_ch2 if e["type"] == "CT_REVERSAL"]

    if print_progress:
        _print_summary(meter_urn, df_before, df, anomaly_log)

    return df, df_before, anomaly_log, ct_reversal


# ── 전체 transformer 계량기 일괄 전처리 ─────────────────────────────

def preprocess_all_transformers(
    print_progress: bool = True,
) -> dict[str, tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]]:
    """transformer group 계량기 전체를 일괄 전처리.

    Returns
    -------
    dict[meter_urn] → (df_clean, df_before, anomaly_log, ct_reversal)
    """
    transformer_urns = get_meters_by_group("transformer")
    if not transformer_urns:
        raise RuntimeError("transformer group에 해당하는 계량기가 없습니다.")

    results: dict[str, tuple] = {}
    for urn in transformer_urns:
        if print_progress:
            print(f"\n{'='*50}")
            print(f"처리 중: {urn}")
            print(f"{'='*50}")
        results[urn] = preprocess_transformer_meter(urn, print_progress=print_progress)

    return results


# ── 출력 헬퍼 ────────────────────────────────────────────────────────

def _print_summary(
    meter_urn: str,
    df_before: pd.DataFrame,
    df_after: pd.DataFrame,
    anomaly_log: list[dict[str, Any]],
) -> None:
    print(f"\n[{meter_urn}] 전처리 결과 요약")

    ch_counts = {1: 0, 2: 0, 3: 0}
    for entry in anomaly_log:
        ch_counts[entry["chapter"]] += entry["n_affected"]

    print(f"  Ch1 물리 법칙 위반  → NaN 처리  : {ch_counts[1]:,}건")
    print(f"  Ch2 CT 역접속       → abs 변환   : {ch_counts[2]:,}건")
    print(f"  Ch3 측정 한계 초과  → flag 부여  : {ch_counts[3]:,}건")

    # NaN 증감 요약
    numeric_cols = [c for c in df_before.columns if pd.api.types.is_numeric_dtype(df_before[c])]
    changed = []
    for col in numeric_cols:
        before = int(df_before[col].isna().sum())
        after = int(df_after[col].isna().sum())
        if after != before:
            changed.append(f"    {col}: {before} → {after} (+{after - before})")
    if changed:
        print("  NaN 증감 컬럼:")
        print("\n".join(changed))

    # CT 역접속 탐지 결과
    ct_entries = [e for e in anomaly_log if e["type"] == "CT_REVERSAL"]
    if ct_entries:
        print("  CT 역접속 탐지:")
        for e in ct_entries:
            print(f"    {e['column']}: 음수 비율 {e['neg_ratio']:.1%} → abs() 적용")


# ── CLI 진입점 ───────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Transformer 계량기 전처리 (Ch1~Ch3 룰 적용)")
    parser.add_argument(
        "--meter-urn",
        default=None,
        help="특정 계량기 URN (생략 시 transformer group 전체 처리)",
    )
    args = parser.parse_args()

    if args.meter_urn:
        df, df_before, anomaly_log, ct_reversal = preprocess_transformer_meter(
            args.meter_urn, print_progress=True
        )
        print("\ndf.shape:", df.shape)
        print(df.head())
    else:
        results = preprocess_all_transformers(print_progress=True)
        print(f"\n총 {len(results)}기 transformer 계량기 전처리 완료.")
        for urn, (df, _, log, _) in results.items():
            total = sum(e["n_affected"] for e in log)
            print(f"  {urn}: shape={df.shape}, 총 탐지={total:,}건")


if __name__ == "__main__":
    main()
