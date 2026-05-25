#!/usr/bin/env python3
"""Verify label alignment and leakage guards for grid_peak_demand_15min."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs/modeling/grid_peak_demand_15min"
DEMAND_RATE_EUR_PER_KW = {
    2018: 87.38,
    2019: 100.01,
    2020: 109.77,
    2021: 111.62,
    2022: 16.31,
    2023: 17.53,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def future_max(series: pd.Series, steps: int) -> pd.Series:
    shifted = pd.concat([series.shift(-i) for i in range(1, steps + 1)], axis=1)
    out = shifted.max(axis=1, skipna=True)
    out[shifted.notna().sum(axis=1) < steps] = np.nan
    return out


def same_split_future_mask(split: pd.Series, steps: int) -> pd.Series:
    shifted = pd.concat([split.shift(-i) for i in range(1, steps + 1)], axis=1)
    same = shifted.eq(split, axis=0).all(axis=1)
    complete = shifted.notna().all(axis=1)
    return (same & complete).astype(bool)


def mismatch_summary(df: pd.DataFrame, label: str, expected: pd.Series, actual: pd.Series) -> dict[str, object]:
    mask = expected.notna() & actual.notna()
    diff = (actual[mask] - expected[mask]).abs()
    mismatch = diff > 1e-9
    missing_mismatch = expected.isna() != actual.isna()
    return {
        "check": label,
        "checked_rows": int(mask.sum()),
        "mismatch_rows": int(mismatch.sum() + missing_mismatch.sum()),
        "max_abs_error": float(diff.max()) if len(diff) else 0.0,
        "status": "pass" if int(mismatch.sum() + missing_mismatch.sum()) == 0 else "fail",
    }


def verify_label_alignment(df: pd.DataFrame, audit_dir: Path) -> pd.DataFrame:
    summaries = []
    t1_mask = df["target_grid_import_P_t_plus_1_valid"] if "target_grid_import_P_t_plus_1_valid" in df else same_split_future_mask(df["split"], 1)
    t4_mask = df["target_grid_import_P_t_plus_4_valid"] if "target_grid_import_P_t_plus_4_valid" in df else same_split_future_mask(df["split"], 4)
    n1_mask = df["target_next_1h_valid"] if "target_next_1h_valid" in df else same_split_future_mask(df["split"], 4)
    n4_mask = df["target_next_4h_valid"] if "target_next_4h_valid" in df else same_split_future_mask(df["split"], 16)
    expected_t1 = df["grid_import_P"].shift(-1).where(t1_mask)
    expected_t4 = df["grid_import_P"].shift(-4).where(t4_mask)
    expected_next_1h = future_max(df["grid_import_P"], 4).where(n1_mask)
    expected_next_4h = future_max(df["grid_import_P"], 16).where(n4_mask)
    summaries.append(mismatch_summary(df, "target_grid_import_P_t_plus_1", expected_t1, df["target_grid_import_P_t_plus_1"]))
    summaries.append(mismatch_summary(df, "target_grid_import_P_t_plus_4", expected_t4, df["target_grid_import_P_t_plus_4"]))
    summaries.append(mismatch_summary(df, "target_next_1h_max_grid_import_P", expected_next_1h, df["target_next_1h_max_grid_import_P"]))
    summaries.append(mismatch_summary(df, "target_next_4h_max_grid_import_P", expected_next_4h, df["target_next_4h_max_grid_import_P"]))

    for label, target_col in [("1h", "target_next_1h_max_grid_import_P"), ("4h", "target_next_4h_max_grid_import_P")]:
        inc = (df[target_col] - df["current_month_peak_kw"]).clip(lower=0)
        inc[df[target_col].isna()] = np.nan
        summaries.append(mismatch_summary(df, f"target_peak_increment_kw_next_{label}", inc, df[f"target_peak_increment_kw_next_{label}"]))
        rate = df["ts"].dt.year.map(DEMAND_RATE_EUR_PER_KW).astype("float64")
        summaries.append(mismatch_summary(df, f"target_month_peak_increment_cost_proxy_eur_next_{label}", inc * rate, df[f"target_month_peak_increment_cost_proxy_eur_next_{label}"]))

    out = pd.DataFrame(summaries)
    out.to_csv(audit_dir / "label_alignment_check.csv", index=False)
    return out


def verify_month_peak(df: pd.DataFrame, audit_dir: Path) -> pd.DataFrame:
    month_key = df["ts"].dt.strftime("%Y-%m")
    rows = []
    for month, sub in df.groupby(month_key, sort=True):
        expected = sub["grid_import_P"].cummax()
        diff = (sub["current_month_peak_kw"] - expected).abs()
        first = sub.iloc[0]
        rows.append(
            {
                "month": month,
                "rows": int(len(sub)),
                "first_ts": first["ts"],
                "first_grid_import_P": float(first["grid_import_P"]),
                "first_current_month_peak_kw": float(first["current_month_peak_kw"]),
                "first_row_reset_ok": bool(abs(float(first["current_month_peak_kw"] - first["grid_import_P"])) <= 1e-9),
                "cummax_mismatch_rows": int((diff > 1e-9).sum()),
                "max_abs_error": float(diff.max()) if len(diff) else 0.0,
                "non_decreasing_ok": bool(sub["current_month_peak_kw"].diff().fillna(0).ge(-1e-9).all()),
            }
        )
    out = pd.DataFrame(rows)
    out["status"] = np.where(
        out["first_row_reset_ok"] & (out["cummax_mismatch_rows"] == 0) & out["non_decreasing_ok"],
        "pass",
        "fail",
    )
    out.to_csv(audit_dir / "month_peak_leakage_check.csv", index=False)
    return out


def verify_time_grid(df: pd.DataFrame) -> dict[str, object]:
    delta = df["ts"].diff().dropna()
    return {
        "rows": int(len(df)),
        "min_ts": df["ts"].min().isoformat(),
        "max_ts": df["ts"].max().isoformat(),
        "duplicate_ts_rows": int(df["ts"].duplicated().sum()),
        "non_15min_step_rows": int((delta != pd.Timedelta(minutes=15)).sum()),
        "status": "pass" if int(df["ts"].duplicated().sum()) == 0 and int((delta != pd.Timedelta(minutes=15)).sum()) == 0 else "fail",
    }


def verify_split_boundary_targets(df: pd.DataFrame, audit_dir: Path) -> pd.DataFrame:
    checks = [
        ("target_grid_import_P_t_plus_1", "target_grid_import_P_t_plus_1_valid" if "target_grid_import_P_t_plus_1_valid" in df else "target_grid_import_P_t_plus_1_same_split"),
        ("target_grid_import_P_t_plus_4", "target_grid_import_P_t_plus_4_valid" if "target_grid_import_P_t_plus_4_valid" in df else "target_grid_import_P_t_plus_4_same_split"),
        ("target_next_1h_max_grid_import_P", "target_next_1h_valid" if "target_next_1h_valid" in df else "target_next_1h_same_split"),
        ("target_next_4h_max_grid_import_P", "target_next_4h_valid" if "target_next_4h_valid" in df else "target_next_4h_same_split"),
    ]
    rows = []
    for target_col, mask_col in checks:
        cross = ~df[mask_col].astype(bool)
        rows.append(
            {
                "target": target_col,
                "cross_split_or_incomplete_rows": int(cross.sum()),
                "non_null_cross_split_rows": int(df.loc[cross, target_col].notna().sum()),
                "status": "pass" if int(df.loc[cross, target_col].notna().sum()) == 0 else "fail",
            }
        )
    out = pd.DataFrame(rows)
    out.to_csv(audit_dir / "split_boundary_target_check.csv", index=False)
    return out


def write_event_examples(df: pd.DataFrame, out_dir: Path) -> None:
    p99 = float(df["grid_import_P"].quantile(0.99))
    examples = (
        df.loc[df["grid_import_P"] >= p99, ["ts", "split", "raw_grid_P", "grid_import_P", "current_month_peak_kw", "target_next_1h_max_grid_import_P", "target_exceed_month_peak_next_1h", "target_peak_increment_kw_next_1h"]]
        .sort_values("grid_import_P", ascending=False)
        .head(30)
    )
    examples.to_csv(out_dir / "grid_peak_event_examples.csv", index=False)


def main() -> None:
    args = parse_args()
    out_dir = args.out_dir
    audit_dir = out_dir / "audits"
    audit_dir.mkdir(parents=True, exist_ok=True)
    frame_path = out_dir / "grid_peak_frame_15min.parquet"
    df = pd.read_parquet(frame_path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    time_grid = verify_time_grid(df)
    label = verify_label_alignment(df, audit_dir)
    month = verify_month_peak(df, audit_dir)
    split_boundary = verify_split_boundary_targets(df, audit_dir)
    write_event_examples(df, out_dir)

    status = "pass" if time_grid["status"] == "pass" and (label["status"] == "pass").all() and (month["status"] == "pass").all() and (split_boundary["status"] == "pass").all() else "fail"
    report = {
        "status": status,
        "time_grid": time_grid,
        "label_checks": {"rows": int(len(label)), "failed": int((label["status"] != "pass").sum())},
        "month_peak_checks": {"months": int(len(month)), "failed": int((month["status"] != "pass").sum())},
        "split_boundary_target_checks": {"rows": int(len(split_boundary)), "failed": int((split_boundary["status"] != "pass").sum())},
    }
    (out_dir / "grid_peak_verification_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
