"""Break down the 2023-08-21 cooling over-electricity candidate by meter.

Outputs component tables and figures for central cooling meters, cooling thermal
meter, local cooling, and ventilation context.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psycopg

ROOT = Path(__file__).resolve().parents[2]
EMS_ROOT = Path("/home/viowlet/Projects/EMS")
sys.path.insert(0, str(ROOT / "scripts" / "insights"))

from run_cooling_chp_anomaly_quantification_1h import fit_relation_model  # noqa: E402
from run_equipment_relation_strength_1h import fetch_reduced_1h  # noqa: E402

OUT_FIG = ROOT / "outputs" / "figures" / "equipment_anomaly_validation"
OUT_TAB = ROOT / "outputs" / "tables" / "equipment_anomaly_validation"
TZ = "Europe/Berlin"

START_UTC = "2023-08-20 00:00:00+00"
END_UTC = "2023-08-23 12:00:00+00"
CAND_START_LOCAL = pd.Timestamp("2023-08-21 08:00:00", tz=TZ)
CAND_END_LOCAL = pd.Timestamp("2023-08-21 23:00:00", tz=TZ)


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


def fetch_component_data() -> pd.DataFrame:
    conn = connect()
    conn.execute("set default_transaction_read_only = on")
    sql = """
    select c.ts, md.equipment_group, c.meter_urn, md.equipment_name, c.value
    from ems.cr_measurement_1h c
    join ems.meter_definition md using (meter_urn)
    where c.measurement = 'P'
      and md.equipment_group in ('central_cooling','cooling_thermal','local_cooling','ventilation')
      and c.ts >= %(start_ts)s
      and c.ts < %(end_ts)s
    order by c.ts, md.equipment_group, c.meter_urn
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"start_ts": START_UTC, "end_ts": END_UTC})
        rows = cur.fetchall()
        cols = [desc.name for desc in cur.description]
    conn.close()
    frame = pd.DataFrame(rows, columns=cols)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame["local_ts"] = frame["ts"].dt.tz_convert(TZ)
    return frame


def prepare_model_context() -> pd.DataFrame:
    data = fetch_reduced_1h()
    model = fit_relation_model(
        data,
        "Cooling efficiency",
        "cooling_elec_P",
        ["cooling_thermal_P", "Ta", "hour_sin", "hour_cos", "month_sin", "month_cos"],
    )
    ctx = model.frame.copy()
    ctx["ts"] = pd.to_datetime(ctx["ts"], utc=True)
    mask = (ctx["ts"] >= pd.Timestamp(START_UTC)) & (ctx["ts"] < pd.Timestamp(END_UTC))
    return ctx.loc[mask, ["ts", "local_ts", "cooling_elec_P", "cooling_thermal_P", "Ta", "pred", "residual", "abs_robust_z"]].copy()


def make_wide(frame: pd.DataFrame) -> pd.DataFrame:
    pivot = frame.pivot_table(index=["ts", "local_ts"], columns="meter_urn", values="value", aggfunc="mean").reset_index()
    for col in pivot.columns:
        if col not in {"ts", "local_ts"}:
            pivot[col] = pd.to_numeric(pivot[col], errors="coerce")
    return pivot


def summarize(frame: pd.DataFrame, ctx: pd.DataFrame) -> pd.DataFrame:
    cand_mask = (frame["local_ts"] >= CAND_START_LOCAL) & (frame["local_ts"] <= CAND_END_LOCAL)
    rows = []
    for (group, meter, name), g in frame.groupby(["equipment_group", "meter_urn", "equipment_name"]):
        all_v = g["value"].astype(float)
        cand_v = g.loc[cand_mask.reindex(g.index, fill_value=False), "value"].astype(float)
        rows.append(
            {
                "equipment_group": group,
                "meter_urn": meter,
                "equipment_name": name,
                "candidate_hours": int(cand_v.count()),
                "candidate_mean": float(cand_v.mean()) if cand_v.count() else np.nan,
                "candidate_max": float(cand_v.max()) if cand_v.count() else np.nan,
                "window_mean": float(all_v.mean()),
                "window_max": float(all_v.max()),
            }
        )
    out = pd.DataFrame(rows).sort_values(["equipment_group", "candidate_max"], ascending=[True, False])
    # Add aggregate rows.
    wide = make_wide(frame)
    central_cols = [c for c in ["H1.Z11", "H1.Z12", "H1.Z16", "H1.Z24", "H1.Z25"] if c in wide]
    local_cols = [c for c in ["H2.Z66", "H2.Z67", "H2.ZE66", "H2.ZE67", "H3.Z45"] if c in wide]
    vent_cols = [c for c in ["H2.T.Z31", "H2.Z68", "H2.Z69", "H2.Z70", "H3.Z42"] if c in wide]
    wide["central_cooling_sum"] = wide[central_cols].sum(axis=1)
    wide["local_cooling_sum"] = wide[local_cols].sum(axis=1)
    wide["ventilation_sum"] = wide[vent_cols].sum(axis=1)
    cand = wide[(wide["local_ts"] >= CAND_START_LOCAL) & (wide["local_ts"] <= CAND_END_LOCAL)]
    agg_rows = []
    for name, col in [("central_cooling_sum", "central_cooling_sum"), ("local_cooling_sum", "local_cooling_sum"), ("ventilation_sum", "ventilation_sum")]:
        agg_rows.append(
            {
                "equipment_group": "aggregate",
                "meter_urn": name,
                "equipment_name": name,
                "candidate_hours": int(cand[col].count()),
                "candidate_mean": float(cand[col].mean()),
                "candidate_max": float(cand[col].max()),
                "window_mean": float(wide[col].mean()),
                "window_max": float(wide[col].max()),
            }
        )
    return pd.concat([out, pd.DataFrame(agg_rows)], ignore_index=True)


def plot_breakdown(frame: pd.DataFrame, ctx: pd.DataFrame) -> Path:
    wide = make_wide(frame)
    merged = wide.merge(ctx, on=["ts", "local_ts"], how="left")
    central_cols = [c for c in ["H1.Z11", "H1.Z12", "H1.Z16", "H1.Z24", "H1.Z25"] if c in merged]
    vent_cols = [c for c in ["H2.T.Z31", "H2.Z68", "H2.Z69", "H2.Z70", "H3.Z42"] if c in merged]
    local_cols = [c for c in ["H2.Z66", "H2.Z67", "H2.ZE66", "H2.ZE67", "H3.Z45"] if c in merged]

    x = merged["local_ts"]
    colors = ["#4c78a8", "#72b7b2", "#f58518", "#e45756", "#54a24b"]
    fig, axes = plt.subplots(5, 1, figsize=(16, 13), sharex=True)
    fig.suptitle("Cooling over-electricity candidate breakdown around 2023-08-21", fontsize=15, fontweight="bold")

    axes[0].stackplot(x, *[(merged[c] / 1000.0).fillna(0) for c in central_cols], labels=central_cols, colors=colors[: len(central_cols)], alpha=0.85)
    axes[0].plot(x, merged["cooling_elec_P"] / 1000.0, color="black", linewidth=1.5, label="reduced cooling_elec_P")
    axes[0].set_ylabel("Central cooling electric (kW)")
    axes[0].legend(loc="upper left", ncol=3)

    axes[1].plot(x, merged["cooling_thermal_P"] / 1000.0, color="#0066cc", linewidth=2, label="reduced cooling thermal P")
    if "V.K21" in merged:
        axes[1].plot(x, merged["V.K21"] / 1000.0, color="#ff7f0e", linestyle="--", linewidth=1.5, label="V.K21 thermal P")
    axes[1].set_ylabel("Thermal P (kW)")
    axes[1].legend(loc="upper left")

    axes[2].plot(x, merged["cooling_elec_P"] / 1000.0, color="#2ca02c", linewidth=2, label="Actual electric P")
    axes[2].plot(x, merged["pred"] / 1000.0, color="#ff7f0e", linestyle="--", linewidth=2, label="Expected electric P")
    axes[2].bar(x, merged["residual"] / 1000.0, width=0.035, color="#9467bd", alpha=0.28, label="Residual")
    axes[2].axhline(0, color="black", linewidth=0.8)
    axes[2].set_ylabel("Electric / residual (kW)")
    axes[2].legend(loc="upper left")

    axes[3].stackplot(x, *[(merged[c] / 1000.0).fillna(0) for c in vent_cols], labels=vent_cols, alpha=0.8)
    axes[3].set_ylabel("Ventilation P (kW)")
    axes[3].legend(loc="upper left", ncol=3)

    axes[4].stackplot(x, *[(merged[c] / 1000.0).fillna(0) for c in local_cols], labels=local_cols, alpha=0.8)
    axes[4].plot(x, merged["Ta"], color="black", linewidth=1.2, label="Ta (°C)")
    axes[4].set_ylabel("Local cooling P / Ta")
    axes[4].legend(loc="upper left", ncol=3)

    for ax in axes:
        ax.axvspan(CAND_START_LOCAL, CAND_END_LOCAL, color="#d62728", alpha=0.12)
        ax.grid(True, alpha=0.3)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=x.dt.tz))
    axes[-1].set_xlabel("Local time (Europe/Berlin)")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    OUT_FIG.mkdir(parents=True, exist_ok=True)
    path = OUT_FIG / "cooling_component_breakdown_2023_08_21.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_cm_share(frame: pd.DataFrame) -> Path:
    wide = make_wide(frame)
    cand = wide[(wide["local_ts"] >= CAND_START_LOCAL) & (wide["local_ts"] <= CAND_END_LOCAL)].copy()
    cols = [c for c in ["H1.Z11", "H1.Z12", "H1.Z16", "H1.Z24", "H1.Z25"] if c in cand]
    means = (cand[cols].mean() / 1000.0).sort_values(ascending=False)
    fig, ax = plt.subplots(figsize=(9, 5))
    means.plot(kind="bar", ax=ax, color=["#4c78a8", "#72b7b2", "#f58518", "#e45756", "#54a24b"][: len(means)])
    ax.set_title("Mean central cooling electric P during 2023-08-21 candidate window")
    ax.set_ylabel("Mean P (kW)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = OUT_FIG / "cooling_cm_meter_mean_2023_08_21.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> None:
    frame = fetch_component_data()
    ctx = prepare_model_context()
    OUT_TAB.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT_TAB / "08_cooling_component_timeseries_2023_08_21.csv", index=False)
    summary = summarize(frame, ctx)
    summary.to_csv(OUT_TAB / "08_cooling_component_summary_2023_08_21.csv", index=False)
    fig1 = plot_breakdown(frame, ctx)
    fig2 = plot_cm_share(frame)
    print({"figures": [str(fig1), str(fig2)], "summary": str(OUT_TAB / "08_cooling_component_summary_2023_08_21.csv")})


if __name__ == "__main__":
    main()
