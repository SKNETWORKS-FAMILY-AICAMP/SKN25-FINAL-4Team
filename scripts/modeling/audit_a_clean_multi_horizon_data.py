#!/usr/bin/env python3
"""Audit A-clean multi-horizon modeling data for split, alignment, and leakage risks."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MODEL_SCRIPT = ROOT / "scripts/modeling/train_a_clean_huang2022_multi_horizon.py"
DATASET_DIR = ROOT / "outputs/modeling/a_clean_targets_1h"
OUT_DIR = ROOT / "outputs/modeling/a_clean_huang2022_multi_horizon"
REPORT_DIR = ROOT / "reports/a_clean_huang2022_benchmark"
TABLE_DIR = REPORT_DIR / "tables"
AUDIT_DIR = OUT_DIR / "audit"

LABELS = {
    "T1_group__central_cooling__P": "중앙 냉방",
    "T1_group__local_cooling__P": "국소 냉방",
    "T1_group__server_power__P": "서버 전원",
    "T1_group__ventilation__P": "환기 계통",
}
HORIZONS = [1, 24, 168]
LOOKBACK = 24


def load_model_module() -> Any:
    spec = importlib.util.spec_from_file_location("multi_horizon_model", MODEL_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {MODEL_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def period_summary(series: pd.Series, max_periods: int = 20) -> list[dict[str, Any]]:
    if series.empty:
        return []
    s = series.sort_values().reset_index(drop=True)
    groups = s.diff().gt(pd.Timedelta(hours=1)).cumsum()
    out = []
    for _, g in s.groupby(groups):
        out.append({"start": str(g.min()), "end": str(g.max()), "rows": int(len(g))})
    return out[:max_periods]


def rmse(y: np.ndarray, yhat: np.ndarray) -> float:
    mask = np.isfinite(y) & np.isfinite(yhat)
    return float(np.sqrt(np.mean((y[mask] - yhat[mask]) ** 2)))


def audit() -> dict[str, Any]:
    mod = load_model_module()
    target = pd.read_parquet(DATASET_DIR / mod.TARGET_FILE)
    feature = pd.read_parquet(DATASET_DIR / mod.FEATURE_FILE)
    target["ts"] = pd.to_datetime(target["ts"])
    feature["ts"] = pd.to_datetime(feature["ts"])

    result: dict[str, Any] = {
        "paths": {
            "root": str(ROOT),
            "dataset_dir": str(DATASET_DIR),
            "target_file": str(DATASET_DIR / mod.TARGET_FILE),
            "feature_file": str(DATASET_DIR / mod.FEATURE_FILE),
            "model_script": str(MODEL_SCRIPT),
        },
        "source_frame": {},
        "horizon_frame_checks": [],
        "model_input_policy": {},
        "baseline_checks": [],
        "issues": [],
    }

    # Source frame audit
    result["source_frame"] = {
        "target_shape": [int(x) for x in target.shape],
        "feature_shape": [int(x) for x in feature.shape],
        "target_columns": list(target.columns),
        "feature_columns": list(feature.columns),
        "target_ids": sorted(target["target_id"].dropna().unique().tolist()),
        "target_duplicate_key_rows": int(target.duplicated(["target_id", "ts"]).sum()),
        "feature_duplicate_ts_rows": int(feature.duplicated(["ts"]).sum()),
    }
    target_time = []
    for tid, g in target.groupby("target_id"):
        g = g.sort_values("ts")
        diffs = g["ts"].diff().dropna()
        target_time.append({
            "target_id": tid,
            "label": LABELS.get(tid, tid),
            "rows": int(len(g)),
            "min_ts": str(g["ts"].min()),
            "max_ts": str(g["ts"].max()),
            "gap_gt_1h_count": int(diffs.gt(pd.Timedelta(hours=1)).sum()),
            "non_1h_step_count": int(diffs.ne(pd.Timedelta(hours=1)).sum()),
            "target_nan_rows": int(g["target_value"].isna().sum()),
            "negative_rows": int(g["target_value"].lt(0).sum()),
            "target_observed_false_rows": int((~g["target_observed"].eq(True)).sum()) if "target_observed" in g else None,
            "full_component_false_rows": int((~g["is_full_component_observed"].eq(True)).sum()) if "is_full_component_observed" in g else None,
        })
    result["source_frame"]["target_time_grid"] = target_time

    feature_sorted = feature.sort_values("ts")
    fdiff = feature_sorted["ts"].diff().dropna()
    result["source_frame"]["feature_time_grid"] = {
        "rows": int(len(feature_sorted)),
        "min_ts": str(feature_sorted["ts"].min()),
        "max_ts": str(feature_sorted["ts"].max()),
        "gap_gt_1h_count": int(fdiff.gt(pd.Timedelta(hours=1)).sum()),
        "non_1h_step_count": int(fdiff.ne(pd.Timedelta(hours=1)).sum()),
        "split_counts": {str(k): int(v) for k, v in feature_sorted["split"].value_counts(dropna=False).sort_index().items()},
        "split_ranges": {
            str(split): {"min": str(g["ts"].min()), "max": str(g["ts"].max()), "rows": int(len(g))}
            for split, g in feature_sorted.groupby("split", dropna=False)
        },
        "gateway_outage_rows": int(feature_sorted["is_gateway_outage"].eq(True).sum()),
        "gateway_outage_periods": period_summary(feature_sorted.loc[feature_sorted["is_gateway_outage"].eq(True), "ts"]),
    }

    feature_by_ts = feature.set_index("ts")
    target_lookup = {
        tid: g.set_index("ts")["target_value"].sort_index()
        for tid, g in target.groupby("target_id")
    }

    # Horizon frame audit, full-row checks where practical.
    for h in HORIZONS:
        for tid in LABELS:
            df = mod.make_frame(target, feature, tid, LOOKBACK, h)
            usable = mod.usable_mask(df)
            check: dict[str, Any] = {
                "horizon_hours": h,
                "target_id": tid,
                "label": LABELS[tid],
                "rows": int(len(df)),
                "usable_rows": int(usable.sum()),
            }
            check["target_ts_offset_failures"] = int((df["target_ts"] - df["origin_ts"]).ne(pd.Timedelta(hours=h)).sum())
            # Split/gateway must be assigned by target_ts.
            joined_split = df["target_ts"].map(feature_by_ts["split"])
            split_mismatch = df["split"].ne(joined_split)
            check["split_target_ts_mismatch_rows"] = int(split_mismatch.sum())
            check["split_target_ts_mismatch_usable_rows"] = int((split_mismatch & usable).sum())
            joined_gateway = df["target_ts"].map(feature_by_ts["is_gateway_outage"])
            gateway_mismatch = df["is_gateway_outage"].ne(joined_gateway)
            check["gateway_target_ts_mismatch_rows"] = int(gateway_mismatch.sum())
            check["gateway_target_ts_mismatch_usable_rows"] = int((gateway_mismatch & usable).sum())
            # Target value must be target lookup at target_ts.
            y = target_lookup[tid]
            joined_y = df["target_ts"].map(y)
            target_match = np.isclose(df["target_value"].to_numpy(float), joined_y.to_numpy(float), equal_nan=True)
            check["target_value_lookup_mismatch_rows"] = int((~target_match).sum())
            # Lag checks for all rows where corresponding timestamp exists.
            lag_checks = {}
            for lag in [1, 2, 24]:
                expected = (df["origin_ts"] - pd.Timedelta(hours=lag - 1)).map(y)
                observed = df[f"target_lag_{lag}"]
                exists = expected.notna()
                match = np.isclose(observed[exists].to_numpy(float), expected[exists].to_numpy(float), equal_nan=True)
                lag_checks[f"target_lag_{lag}"] = {
                    "checked_rows": int(exists.sum()),
                    "mismatch_rows": int((~match).sum()),
                }
            check["lag_checks"] = lag_checks
            # Split boundaries.
            for split in ["train", "validation", "test"]:
                s = df[usable & df["split"].eq(split)]
                check[f"{split}_target_min"] = str(s["target_ts"].min())
                check[f"{split}_target_max"] = str(s["target_ts"].max())
                check[f"{split}_origin_min"] = str(s["origin_ts"].min())
                check[f"{split}_origin_max"] = str(s["origin_ts"].max())
                check[f"{split}_usable_rows"] = int(len(s))
            check["label_minus_latest_input_hours_min"] = int(((df.loc[usable, "target_ts"] - df.loc[usable, "origin_ts"]).dt.total_seconds() / 3600).min())
            result["horizon_frame_checks"].append(check)

    # Model input policy audit.
    result["model_input_policy"] = {
        "tabular_features": [f"target_lag_{i}" for i in range(1, LOOKBACK + 1)] + ["hour_sin", "hour_cos"],
        "tabular_time_features_anchor": "target_ts hour_sin/hour_cos; calendar future is known but differs from LSTM input anchor.",
        "lstm_channels_from_manifest": ["origin_target_scaled", "origin_hour_sin", "origin_hour_cos"],
        "lstm_time_features_anchor": "origin_ts hour_sin/hour_cos",
        "input_policy_issue": "SVR/XGBoost use target-time hour features, while LSTM uses origin-time hour features. This is not target-value leakage, but it is an inconsistent model-family comparison for 24h/168h.",
        "lstm_training_log_available": False,
        "lstm_training_log_issue": "Current artifacts do not persist epoch loss, best epoch, or train RMSE, so LSTM underfit/overfit cannot be diagnosed from saved run alone.",
    }
    result["issues"].extend([
        {"severity": "medium", "item": "LSTM 시간 피처 anchor가 tabular 모델과 다름", "detail": result["model_input_policy"]["input_policy_issue"]},
        {"severity": "medium", "item": "LSTM 학습 진단 로그 부족", "detail": result["model_input_policy"]["lstm_training_log_issue"]},
        {"severity": "medium", "item": "baseline 부족", "detail": "장기 horizon 해석에는 origin persistence / seasonal persistence baseline을 후보군에 명시적으로 포함해야 함."},
    ])

    # Baseline check: operationally allowed origin persistence.
    for p in sorted(OUT_DIR.glob("h*/T1_group__*/best_predictions.parquet")):
        h = int(p.relative_to(OUT_DIR).parts[0][1:])
        tid = p.relative_to(OUT_DIR).parts[1]
        pred = pd.read_parquet(p)
        base_mask = pred["target_value"].notna() & (~pred["is_gateway_outage"].eq(True))
        for split in ["validation", "test"]:
            m = base_mask & pred["split"].eq(split)
            y = pred.loc[m, "target_value"].to_numpy(float)
            origin_pred = pred.loc[m, "origin_value"].to_numpy(float)
            model_pred = pred.loc[m, "prediction"].to_numpy(float)
            result["baseline_checks"].append({
                "horizon_hours": h,
                "target_id": tid,
                "label": LABELS.get(tid, tid),
                "split": split,
                "rows": int(m.sum()),
                "origin_persistence_rmse": rmse(y, origin_pred),
                "selected_model_rmse": rmse(y, model_pred),
                "selected_model_minus_origin_rmse": rmse(y, model_pred) - rmse(y, origin_pred),
            })

    return result


def markdown_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    rows = [[str(x) for x in row] for row in df.to_numpy()]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header, sep] + body)


def write_report(result: dict[str, Any]) -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    json_path = AUDIT_DIR / "multi_horizon_data_audit.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    frame_df = pd.DataFrame(result["horizon_frame_checks"])
    baseline_df = pd.DataFrame(result["baseline_checks"])
    frame_csv = TABLE_DIR / "multi_horizon_frame_audit.csv"
    baseline_csv = TABLE_DIR / "multi_horizon_origin_persistence_baseline.csv"
    frame_df.to_csv(frame_csv, index=False, encoding="utf-8-sig")
    baseline_df.to_csv(baseline_csv, index=False, encoding="utf-8-sig")

    # Compact markdown report.
    src = result["source_frame"]
    feature = src["feature_time_grid"]
    all_fail_cols = [
        "target_ts_offset_failures", "split_target_ts_mismatch_usable_rows", "gateway_target_ts_mismatch_usable_rows", "target_value_lookup_mismatch_rows",
    ]
    aggregate_failures = {col: int(frame_df[col].sum()) for col in all_fail_cols}
    lag_failures = {}
    for item in result["horizon_frame_checks"]:
        for lag, vals in item["lag_checks"].items():
            lag_failures[lag] = lag_failures.get(lag, 0) + int(vals["mismatch_rows"])

    baseline_view = baseline_df[baseline_df["split"].eq("test")].copy()
    baseline_view["selected_model_rmse"] = baseline_view["selected_model_rmse"].round(1)
    baseline_view["origin_persistence_rmse"] = baseline_view["origin_persistence_rmse"].round(1)
    baseline_view["selected_model_minus_origin_rmse"] = baseline_view["selected_model_minus_origin_rmse"].round(1)
    baseline_view = baseline_view[["horizon_hours", "label", "selected_model_rmse", "origin_persistence_rmse", "selected_model_minus_origin_rmse"]]

    lines = [
        "# Multi-horizon A-clean 데이터 감사",
        "",
        "## 감사 범위",
        f"- Target file: `{result['paths']['target_file']}`",
        f"- Feature file: `{result['paths']['feature_file']}`",
        f"- Model script: `{result['paths']['model_script']}`",
        "- Horizon: 1h, 24h, 168h",
        "- 대상: 중앙 냉방, 국소 냉방, 서버 전원, 환기 계통",
        "",
        "## 원천 frame 점검",
        f"- target shape: `{src['target_shape']}`",
        f"- feature shape: `{src['feature_shape']}`",
        f"- target duplicate key rows: `{src['target_duplicate_key_rows']}`",
        f"- feature duplicate ts rows: `{src['feature_duplicate_ts_rows']}`",
        f"- feature split counts: `{feature['split_counts']}`",
        f"- gateway outage rows: `{feature['gateway_outage_rows']}`",
        f"- gateway outage periods: `{feature['gateway_outage_periods']}`",
        "",
        "## 누수/정렬 검사 결과",
        f"- target_ts offset failures: `{aggregate_failures['target_ts_offset_failures']}`",
        f"- split target_ts mismatch usable rows: `{aggregate_failures['split_target_ts_mismatch_usable_rows']}`",
        f"- gateway target_ts mismatch usable rows: `{aggregate_failures['gateway_target_ts_mismatch_usable_rows']}`",
        f"- target value lookup mismatch rows: `{aggregate_failures['target_value_lookup_mismatch_rows']}`",
        f"- lag mismatch rows: `{lag_failures}`",
        "",
        "## 확인된 주의 사항",
    ]
    for issue in result["issues"]:
        lines.append(f"- `{issue['severity']}` {issue['item']}: {issue['detail']}")
    lines += [
        "",
        "## 2023년 보고 구간 origin persistence baseline 대조",
        markdown_table(baseline_view),
        "",
        "## 산출물",
        f"- JSON: `{json_path}`",
        f"- frame audit CSV: `{frame_csv}`",
        f"- baseline CSV: `{baseline_csv}`",
    ]
    report_path = REPORT_DIR / "multi_horizon_data_audit.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "json_path": str(json_path),
        "report_path": str(report_path),
        "frame_csv": str(frame_csv),
        "baseline_csv": str(baseline_csv),
        "aggregate_failures": aggregate_failures,
        "lag_failures": lag_failures,
        "issues": result["issues"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    write_report(audit())
