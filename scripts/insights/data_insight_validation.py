from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import pandas as pd


def select_peak_mask(df: pd.DataFrame, *, site_col: str, quantile: float) -> tuple[pd.Series, float]:
    if site_col not in df.columns:
        raise KeyError(f"missing site column: {site_col}")
    threshold = float(df[site_col].quantile(quantile))
    return df[site_col] >= threshold, threshold


def compute_peak_lift(df: pd.DataFrame, peak_mask: pd.Series, group_cols: Iterable[str]) -> pd.DataFrame:
    rows: list[dict[str, float | str | None]] = []
    nonpeak_mask = ~peak_mask
    for group in group_cols:
        peak_mean = df.loc[peak_mask, group].mean()
        nonpeak_mean = df.loc[nonpeak_mask, group].mean()
        lift_ratio = None
        if pd.notna(peak_mean) and pd.notna(nonpeak_mean) and nonpeak_mean != 0:
            lift_ratio = float(peak_mean / nonpeak_mean)
        rows.append(
            {
                "group": group,
                "peak_mean_p": float(peak_mean) if pd.notna(peak_mean) else None,
                "nonpeak_mean_p": float(nonpeak_mean) if pd.notna(nonpeak_mean) else None,
                "lift_ratio": lift_ratio,
                "peak_nonnull_pct": float(df.loc[peak_mask, group].notna().mean() * 100) if peak_mask.sum() else None,
            }
        )
    return pd.DataFrame(rows)


def compute_group_peak_overlap(df: pd.DataFrame, peak_mask: pd.Series, group_cols: Iterable[str], *, group_quantile: float) -> pd.DataFrame:
    site_peak_hours = int(peak_mask.sum())
    rows: list[dict[str, float | int | str | None]] = []
    for group in group_cols:
        group_threshold = df[group].quantile(group_quantile)
        group_high = df[group] >= group_threshold
        overlap_hours = int(group_high.loc[peak_mask].sum())
        rows.append(
            {
                "group": group,
                "group_p95": float(group_threshold) if pd.notna(group_threshold) else None,
                "site_peak_hours": site_peak_hours,
                "overlap_hours": overlap_hours,
                "overlap_pct_of_site_peak": float(overlap_hours / site_peak_hours * 100) if site_peak_hours else None,
                "baseline_high_pct": float(group_high.mean() * 100) if pd.notna(group_threshold) else None,
            }
        )
    return pd.DataFrame(rows)


def compute_night_baseload_proxy(df: pd.DataFrame, group_cols: Iterable[str], *, night_hours: set[int]) -> pd.DataFrame:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("df index must be a DatetimeIndex")
    night_mask = df.index.hour.isin(night_hours)
    rows: list[dict[str, float | str | None]] = []
    for group in group_cols:
        night_mean = df.loc[night_mask, group].mean()
        overall_mean = df[group].mean()
        ratio = None
        if pd.notna(night_mean) and pd.notna(overall_mean) and overall_mean != 0:
            ratio = float(night_mean / overall_mean)
        rows.append(
            {
                "group": group,
                "night_mean_p": float(night_mean) if pd.notna(night_mean) else None,
                "overall_mean_p": float(overall_mean) if pd.notna(overall_mean) else None,
                "night_to_overall_ratio": ratio,
            }
        )
    return pd.DataFrame(rows)


def validate_probe_summary(
    overlap: pd.DataFrame,
    month_dist: pd.DataFrame,
    hour_dist: pd.DataFrame,
    *,
    min_overlap_pct: float = 60.0,
    min_overlap_groups: int = 2,
    min_peak_month_count: int = 3,
    min_peak_hour_count: int = 4,
) -> dict[str, object]:
    overlap_groups = overlap[overlap["overlap_pct_of_site_peak"] >= min_overlap_pct]
    peak_months = month_dist[month_dist["peak_hours"] > 0]
    peak_hours = hour_dist[hour_dist["peak_hours"] > 0]
    checks = {
        "repeated_group_overlap": {
            "passed": bool(len(overlap_groups) >= min_overlap_groups),
            "observed_groups": int(len(overlap_groups)),
            "required_groups": min_overlap_groups,
            "threshold_pct": min_overlap_pct,
        },
        "seasonal_concentration": {
            "passed": bool(len(peak_months) >= min_peak_month_count),
            "observed_months": int(len(peak_months)),
            "required_months": min_peak_month_count,
        },
        "hourly_concentration": {
            "passed": bool(len(peak_hours) >= min_peak_hour_count),
            "observed_hours": int(len(peak_hours)),
            "required_hours": min_peak_hour_count,
        },
    }
    return {"passed": all(check["passed"] for check in checks.values()), "checks": checks}


def validate_existing_probe(input_dir: Path) -> dict[str, object]:
    overlap = pd.read_csv(input_dir / "site_peak_group_overlap.csv")
    month_dist = pd.read_csv(input_dir / "site_peak_month_distribution.csv")
    hour_dist = pd.read_csv(input_dir / "site_peak_hour_distribution.csv")
    return validate_probe_summary(overlap, month_dist, hour_dist)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate 1h EMS data-insight probe outputs.")
    parser.add_argument("--input-dir", type=Path, default=Path("outputs/tables/data_insight_probe_1h"))
    parser.add_argument("--out", type=Path, default=Path("outputs/tables/data_insight_probe_1h/validation_summary.json"))
    args = parser.parse_args()

    result = validate_existing_probe(args.input_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
