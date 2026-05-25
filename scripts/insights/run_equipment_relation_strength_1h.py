"""Measure first-pass 1h normal-relation strength for Cooling, CHP, and PV.

Read-only DB script. Outputs compact relation-strength and top-residual tables.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

ROOT = Path(__file__).resolve().parents[2]
EMS_ROOT = Path("/home/viowlet/Projects/EMS")
OUT = ROOT / "outputs" / "tables" / "equipment_anomaly_validation"
TZ = "Europe/Berlin"

SIGNALS = {
    "cooling_elec_P": ("cooling", "cool_elec", "P"),
    "cooling_thermal_P": ("cooling", "total", "P"),
    "chp_elec_P": ("electricity", "chp", "P"),
    "chp_heat_P": ("heating", "chp_heat", "P"),
    "heating_total_P": ("heating", "total", "P"),
    "pv_P": ("electricity", "pv", "P"),
    "Ta": ("weather", "weather", "Ta"),
    "Igm": ("weather", "weather", "Igm"),
}


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect() -> psycopg.Connection:
    load_env(EMS_ROOT / ".env")
    load_env(ROOT / ".env")
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def fetch_reduced_1h() -> pd.DataFrame:
    values = sorted(set(SIGNALS.values()))
    where_parts = []
    params = {}
    for i, (category, subcategory, measurement) in enumerate(values):
        where_parts.append(
            f"(category = %(cat{i})s and subcategory = %(sub{i})s and measurement = %(meas{i})s)"
        )
        params[f"cat{i}"] = category
        params[f"sub{i}"] = subcategory
        params[f"meas{i}"] = measurement
    sql = f"""
        select ts, category, subcategory, measurement, value
        from ems.reduced_measurement_1h
        where {' or '.join(where_parts)}
    """
    conn = connect()
    conn.execute("set default_transaction_read_only = on")
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [desc.name for desc in cur.description]
    conn.close()
    long = pd.DataFrame(rows, columns=cols)
    long["signal"] = long.apply(
        lambda row: next(
            name
            for name, triplet in SIGNALS.items()
            if triplet == (row["category"], row["subcategory"], row["measurement"])
        ),
        axis=1,
    )
    wide = long.pivot_table(index="ts", columns="signal", values="value", aggfunc="mean").reset_index()
    wide["ts"] = pd.to_datetime(wide["ts"], utc=True)
    local = wide["ts"].dt.tz_convert(TZ)
    wide["local_ts"] = local
    wide["hour"] = local.dt.hour
    wide["month"] = local.dt.month
    wide["weekday"] = local.dt.weekday
    wide["hour_sin"] = np.sin(2 * np.pi * wide["hour"] / 24.0)
    wide["hour_cos"] = np.cos(2 * np.pi * wide["hour"] / 24.0)
    wide["month_sin"] = np.sin(2 * np.pi * wide["month"] / 12.0)
    wide["month_cos"] = np.cos(2 * np.pi * wide["month"] / 12.0)
    return wide


def fit_ols(frame: pd.DataFrame, target: str, features: list[str], train_until: str = "2022-01-01") -> dict:
    cols = [target, *features]
    data = frame.dropna(subset=cols).copy()
    train = data[data["ts"] < pd.Timestamp(train_until, tz="UTC")]
    test = data[data["ts"] >= pd.Timestamp(train_until, tz="UTC")]
    if len(train) < 100 or len(test) < 100:
        train = data.iloc[: int(len(data) * 0.7)]
        test = data.iloc[int(len(data) * 0.7) :]
    x_train = train[features].to_numpy(dtype=float)
    x_test = test[features].to_numpy(dtype=float)
    y_train = train[target].to_numpy(dtype=float)
    y_test = test[target].to_numpy(dtype=float)
    x_train = np.column_stack([np.ones(len(x_train)), x_train])
    x_test = np.column_stack([np.ones(len(x_test)), x_test])
    coef = np.linalg.lstsq(x_train, y_train, rcond=None)[0]
    pred_train = x_train @ coef
    pred_test = x_test @ coef

    def metrics(y: np.ndarray, pred: np.ndarray) -> tuple[float, float, float]:
        residual = y - pred
        mae = float(np.mean(np.abs(residual)))
        rmse = float(np.sqrt(np.mean(residual**2)))
        denom = float(np.sum((y - np.mean(y)) ** 2))
        r2 = float(1 - np.sum(residual**2) / denom) if denom > 0 else float("nan")
        return r2, mae, rmse

    train_r2, train_mae, train_rmse = metrics(y_train, pred_train)
    test_r2, test_mae, test_rmse = metrics(y_test, pred_test)
    data["pred"] = np.column_stack([np.ones(len(data)), data[features].to_numpy(dtype=float)]) @ coef
    data["residual"] = data[target] - data["pred"]
    residual_std = float(data["residual"].std(ddof=0))
    data["abs_z"] = np.abs(data["residual"] / residual_std) if residual_std > 0 else np.nan
    return {
        "n_total": int(len(data)),
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "train_r2": train_r2,
        "test_r2": test_r2,
        "train_mae": train_mae,
        "test_mae": test_mae,
        "train_rmse": train_rmse,
        "test_rmse": test_rmse,
        "coef": coef,
        "data": data,
    }


def corr(frame: pd.DataFrame, a: str, b: str) -> float:
    data = frame[[a, b]].dropna()
    if len(data) < 2:
        return float("nan")
    return float(data[a].corr(data[b], method="pearson"))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    wide = fetch_reduced_1h()
    summary_rows = []
    for col in SIGNALS:
        if col in wide:
            valid = wide[col].notna()
            summary_rows.append(
                {
                    "signal": col,
                    "nonnull_rows": int(valid.sum()),
                    "min_ts": wide.loc[valid, "ts"].min() if valid.any() else pd.NaT,
                    "max_ts": wide.loc[valid, "ts"].max() if valid.any() else pd.NaT,
                    "mean": float(wide.loc[valid, col].mean()) if valid.any() else np.nan,
                    "p01": float(wide.loc[valid, col].quantile(0.01)) if valid.any() else np.nan,
                    "p50": float(wide.loc[valid, col].quantile(0.50)) if valid.any() else np.nan,
                    "p99": float(wide.loc[valid, col].quantile(0.99)) if valid.any() else np.nan,
                }
            )
    pd.DataFrame(summary_rows).to_csv(OUT / "02_relation_input_1h_summary.csv", index=False)

    module_specs = [
        {
            "module": "Cooling efficiency",
            "target": "cooling_elec_P",
            "features": ["cooling_thermal_P", "Ta", "hour_sin", "hour_cos", "month_sin", "month_cos"],
            "main_pair": ("cooling_elec_P", "cooling_thermal_P"),
            "filter": wide["cooling_elec_P"].notna(),
            "interpretation": "냉방 열량·외기온 대비 전력 사용 관계",
        },
        {
            "module": "CHP operation",
            "target": "chp_elec_P",
            "features": ["chp_heat_P", "heating_total_P", "Ta", "hour_sin", "hour_cos", "month_sin", "month_cos"],
            "main_pair": ("chp_elec_P", "chp_heat_P"),
            "filter": wide["chp_elec_P"].notna(),
            "interpretation": "CHP 전기·열 생산 관계와 열수요 proxy",
        },
        {
            "module": "PV performance",
            "target": "pv_P",
            "features": ["Igm", "Ta", "hour_sin", "hour_cos", "month_sin", "month_cos"],
            "main_pair": ("pv_P", "Igm"),
            "filter": (wide["pv_P"].notna()) & (wide["Igm"].fillna(0) > 10) & (wide["hour"].between(5, 21)),
            "interpretation": "일사량·시간대 대비 PV 발전 관계; 야간 제외",
        },
    ]

    strength_rows = []
    residual_rows = []
    for spec in module_specs:
        frame = wide.loc[spec["filter"]].copy()
        result = fit_ols(frame, spec["target"], spec["features"])
        pair_a, pair_b = spec["main_pair"]
        strength_rows.append(
            {
                "module": spec["module"],
                "target": spec["target"],
                "features": ", ".join(spec["features"]),
                "n_total": result["n_total"],
                "n_train": result["n_train"],
                "n_test": result["n_test"],
                "pair_corr_pearson": corr(frame, pair_a, pair_b),
                "train_r2": result["train_r2"],
                "test_r2": result["test_r2"],
                "train_mae": result["train_mae"],
                "test_mae": result["test_mae"],
                "train_rmse": result["train_rmse"],
                "test_rmse": result["test_rmse"],
                "interpretation": spec["interpretation"],
                "boundary": "1h reduced-view 1차 관계 검증; 원인 확정에는 raw/BMS/정비 이력 필요",
            }
        )
        top = result["data"].sort_values("abs_z", ascending=False).head(30).copy()
        for row in top.itertuples():
            residual_rows.append(
                {
                    "module": spec["module"],
                    "ts": row.ts,
                    "local_ts": row.local_ts,
                    "target": spec["target"],
                    "actual": getattr(row, spec["target"]),
                    "pred": row.pred,
                    "residual": row.residual,
                    "abs_z": row.abs_z,
                    "initial_label": "statistical_residual_candidate",
                    "requires_manual_review": True,
                }
            )

    strength = pd.DataFrame(strength_rows)
    strength.to_csv(OUT / "03_relation_strength_1h.csv", index=False)
    pd.DataFrame(residual_rows).to_csv(OUT / "03_top_residual_candidates_1h.csv", index=False)

    brief = [
        "# Step 2-3 Cooling/CHP/PV 1h 정상 관계 1차 검증\n",
        f"- 생성 시각(UTC): {datetime.now(timezone.utc).isoformat()}",
        "- 기준 relation: `ems.reduced_measurement_1h`",
        "- 해석 경계: residual 상위 후보는 통계적 후보이며, 설비 원인 확정에는 추가 evidence가 필요함",
        "\n## 관계 강도\n",
    ]
    for row in strength.itertuples():
        brief.append(
            f"- {row.module}: n={row.n_total}, pair_corr={row.pair_corr_pearson:.3f}, test_R2={row.test_r2:.3f}, test_MAE={row.test_mae:.3f}"
        )
    brief.append("\n## 생성 파일\n")
    for name in ["02_relation_input_1h_summary.csv", "03_relation_strength_1h.csv", "03_top_residual_candidates_1h.csv", "STEP2_STEP3_RELATION_BRIEF.md"]:
        brief.append(f"- `{OUT / name}`")
    (OUT / "STEP2_STEP3_RELATION_BRIEF.md").write_text("\n".join(brief) + "\n", encoding="utf-8")

    print(json.dumps({"strength": strength.to_dict(orient="records")}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
