"""Deep-dive equipment-system anomaly candidates after excluding metering-scaling cases.

Outputs:
- Ranked non-meter candidate events for Cooling/CHP.
- Lead-window precursor summaries.
- Cooling component summaries for selected high-value events.
- Timeline and deep-dive figures.

Interpretation boundary: equipment-system/operation review candidates, not confirmed faults.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "insights"))

from run_cooling_chp_anomaly_quantification_1h import (  # noqa: E402
    CHP_PUBLIC_POWER_TO_HEAT_RATIO,
    SEVERE_Z,
    fit_relation_model,
    positive_quantile,
)
from run_equipment_relation_strength_1h import connect, fetch_reduced_1h  # noqa: E402

OUT_TAB = ROOT / "outputs" / "tables" / "equipment_anomaly_validation"
OUT_FIG = ROOT / "outputs" / "figures" / "equipment_anomaly_validation"
TZ = "Europe/Berlin"
MAX_CLUSTER_GAP_HOURS = 2


def cluster_events(candidates: pd.DataFrame, module: str, candidate_type: str) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    frame = candidates.sort_values("ts").copy().reset_index(drop=True)
    cluster_ids: list[int] = []
    cid = 0
    prev_ts = None
    for ts in frame["ts"]:
        if prev_ts is None or (ts - prev_ts) > pd.Timedelta(hours=MAX_CLUSTER_GAP_HOURS):
            cid += 1
        cluster_ids.append(cid)
        prev_ts = ts
    frame["cluster_num"] = cluster_ids
    rows = []
    for cluster_num, group in frame.groupby("cluster_num"):
        rows.append(
            {
                "module": module,
                "candidate_type": candidate_type,
                "event_id": f"{module.lower().replace(' ', '_')}_{candidate_type}_{cluster_num:03d}",
                "start_ts": group["ts"].min(),
                "end_ts": group["ts"].max(),
                "start_local_ts": group["local_ts"].min(),
                "end_local_ts": group["local_ts"].max(),
                "duration_hours_observed": int(len(group)),
                "max_abs_robust_z": float(group["ranking_abs_z"].max()),
                "median_abs_robust_z": float(group["ranking_abs_z"].median()),
                "min_actual": float(group["actual"].min()),
                "max_actual": float(group["actual"].max()),
                "min_pred": float(group["pred"].min()) if "pred" in group else np.nan,
                "max_pred": float(group["pred"].max()) if "pred" in group else np.nan,
            }
        )
    return pd.DataFrame(rows)


def build_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    data = fetch_reduced_1h()
    cooling_model = fit_relation_model(
        data,
        "Cooling efficiency",
        "cooling_elec_P",
        ["cooling_thermal_P", "Ta", "hour_sin", "hour_cos", "month_sin", "month_cos"],
    )
    chp_model = fit_relation_model(
        data,
        "CHP operation",
        "chp_elec_P",
        ["chp_heat_P", "heating_total_P", "Ta", "hour_sin", "hour_cos", "month_sin", "month_cos"],
    )

    c = cooling_model.frame.copy()
    c["actual"] = c["cooling_elec_P"]
    c["ranking_abs_z"] = c["abs_robust_z"]
    c["local_date"] = c["local_ts"].dt.strftime("%Y-%m-%d")
    c_thermal_active = positive_quantile(c["cooling_thermal_P"], 0.50)
    c_candidates = c[
        (c["cooling_thermal_P"] >= c_thermal_active)
        & (c["robust_z"] >= SEVERE_Z)
        & (~c["local_date"].isin(["2023-09-19", "2023-09-20"]))
    ].copy()
    c_events = cluster_events(c_candidates, "Cooling efficiency", "severe_over_electricity_candidate")
    c_events["interpretation"] = "cooling electric over expected under active thermal context"
    c_events["data_quality_excluded_rule"] = "excluded known Sept-2023 thermal scaling window and residual direction is over-electricity"

    h = chp_model.frame.copy()
    h["actual"] = h["chp_elec_P"]
    h["abs_chp_elec_P"] = h["chp_elec_P"].abs()
    h["chp_el_heat_ratio_abs"] = np.where(h["chp_heat_P"] > 0, h["abs_chp_elec_P"] / h["chp_heat_P"], np.nan)
    heat_p50 = positive_quantile(h["chp_heat_P"], 0.50)
    heat_p75 = positive_quantile(h["chp_heat_P"], 0.75)
    heat_p10 = positive_quantile(h["chp_heat_P"], 0.10)
    elec_p50 = positive_quantile(h["abs_chp_elec_P"], 0.50)
    elec_p75 = positive_quantile(h["abs_chp_elec_P"], 0.75)
    elec_p10 = positive_quantile(h["abs_chp_elec_P"], 0.10)
    ratio_context = h[(h["chp_heat_P"] >= heat_p50) & (h["abs_chp_elec_P"] >= elec_p50)].copy()
    ratio_diff = ratio_context["chp_el_heat_ratio_abs"] - CHP_PUBLIC_POWER_TO_HEAT_RATIO
    ratio_median = float(ratio_diff.median())
    ratio_mad = float(np.median(np.abs(ratio_diff - ratio_median)))
    ratio_scale = 1.4826 * ratio_mad if ratio_mad > 0 else float(ratio_diff.std(ddof=0))
    h["ratio_robust_z"] = (h["chp_el_heat_ratio_abs"] - CHP_PUBLIC_POWER_TO_HEAT_RATIO - ratio_median) / ratio_scale
    h["abs_ratio_robust_z"] = h["ratio_robust_z"].abs()
    h["ranking_abs_z"] = h["abs_robust_z"]

    event_frames = []
    specs = [
        (
            h[(h["chp_heat_P"] >= heat_p75) & (h["abs_chp_elec_P"] <= elec_p10)].assign(ranking_abs_z=lambda x: x["abs_robust_z"]),
            "heat_without_electricity_candidate",
            "high CHP heat with very low electricity",
        ),
        (
            h[(h["abs_chp_elec_P"] >= elec_p75) & (h["chp_heat_P"] <= heat_p10)].assign(ranking_abs_z=lambda x: x["abs_robust_z"]),
            "electricity_without_heat_candidate",
            "high CHP electricity with very low heat",
        ),
        (
            h[(h["chp_heat_P"] >= heat_p50) & (h["abs_chp_elec_P"] >= elec_p50) & (h["abs_ratio_robust_z"] >= SEVERE_Z)].assign(
                ranking_abs_z=lambda x: x["abs_ratio_robust_z"]
            ),
            "power_to_heat_ratio_deviation_candidate",
            "public power-to-heat ratio deviation",
        ),
    ]
    for cand, ctype, interpretation in specs:
        ev = cluster_events(cand, "CHP operation", ctype)
        if len(ev):
            ev["interpretation"] = interpretation
            ev["data_quality_excluded_rule"] = "physical CHP heat/electric co-occurrence or ratio rule; source-lineage still requires raw check"
            event_frames.append(ev)
    h_events = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
    return c, h, pd.concat([c_events, h_events], ignore_index=True)


def select_events(events: pd.DataFrame) -> pd.DataFrame:
    cool = events[events["module"] == "Cooling efficiency"].copy()
    chp = events[events["module"] == "CHP operation"].copy()
    selected = []
    # Cooling: long sustained events plus high-z examples, de-duplicated.
    selected.append(cool.sort_values(["duration_hours_observed", "max_abs_robust_z"], ascending=[False, False]).head(4))
    selected.append(cool.sort_values(["max_abs_robust_z", "duration_hours_observed"], ascending=[False, False]).head(4))
    # CHP: strict physical events by severity and duration.
    selected.append(chp.sort_values(["max_abs_robust_z", "duration_hours_observed"], ascending=[False, False]).head(6))
    selected.append(chp.sort_values(["duration_hours_observed", "max_abs_robust_z"], ascending=[False, False]).head(6))
    out = pd.concat(selected, ignore_index=True).drop_duplicates("event_id")
    return out.sort_values(["module", "max_abs_robust_z"], ascending=[True, False]).reset_index(drop=True)


def precursor_summary(selected: pd.DataFrame, cooling: pd.DataFrame, chp: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in selected.itertuples(index=False):
        frame = cooling if row.module == "Cooling efficiency" else chp
        start = pd.Timestamp(row.start_ts)
        end = pd.Timestamp(row.end_ts)
        pre = frame[(frame["ts"] >= start - pd.Timedelta(hours=24)) & (frame["ts"] < start)].copy()
        event = frame[(frame["ts"] >= start) & (frame["ts"] <= end)].copy()
        last6 = pre.tail(6)
        if row.module == "Cooling efficiency":
            pre_signal = pre["robust_z"]
            event_signal = event["robust_z"]
            pre_moderate_hours = int((pre["robust_z"] >= 4).sum())
            feature_notes = (
                f"thermal_event_mean_kw={event['cooling_thermal_P'].mean()/1000:.2f}; "
                f"Ta_event_mean={event['Ta'].mean():.2f}; "
                f"actual_minus_pred_event_mean_kw={(event['actual']-event['pred']).mean()/1000:.2f}"
            )
        else:
            if row.candidate_type == "power_to_heat_ratio_deviation_candidate":
                pre_signal = pre["abs_ratio_robust_z"].dropna()
                event_signal = event["abs_ratio_robust_z"].dropna()
            else:
                pre_signal = pre["abs_robust_z"].dropna()
                event_signal = event["abs_robust_z"].dropna()
            pre_moderate_hours = int((pre_signal >= 4).sum())
            ratio_mean = event["chp_el_heat_ratio_abs"].replace([np.inf, -np.inf], np.nan).mean()
            feature_notes = (
                f"heat_event_mean_kw={event['chp_heat_P'].mean()/1000:.2f}; "
                f"abs_elec_event_mean_kw={event['abs_chp_elec_P'].mean()/1000:.2f}; "
                f"ratio_event_mean={ratio_mean:.3f}"
            )
        rows.append(
            {
                "event_id": row.event_id,
                "module": row.module,
                "candidate_type": row.candidate_type,
                "start_local_ts": row.start_local_ts,
                "end_local_ts": row.end_local_ts,
                "duration_hours_observed": row.duration_hours_observed,
                "event_max_abs_z": row.max_abs_robust_z,
                "pre24_max_signal_z": float(pre_signal.max()) if len(pre_signal) else np.nan,
                "pre24_moderate_hours": pre_moderate_hours,
                "pre6_mean_residual_kw": float(last6["residual"].mean() / 1000.0) if len(last6) else np.nan,
                "event_mean_residual_kw": float(event["residual"].mean() / 1000.0) if len(event) else np.nan,
                "precursor_read": "lead-up present" if pre_moderate_hours >= 3 else "abrupt or sparse lead-up",
                "feature_notes": feature_notes,
                "interpretation_boundary": "operation/equipment-system review candidate; BMS state, alarms, maintenance logs required for fault confirmation",
            }
        )
    return pd.DataFrame(rows)


def fetch_cooling_components_for_selected(selected: pd.DataFrame) -> pd.DataFrame:
    cool = selected[selected["module"] == "Cooling efficiency"].copy()
    if cool.empty:
        return pd.DataFrame()
    conn = connect()
    conn.execute("set default_transaction_read_only = on")
    rows = []
    sql = """
        select c.ts, md.equipment_group, c.meter_urn, md.equipment_name, c.value
        from ems.cr_measurement_1h c
        join ems.meter_definition md using (meter_urn)
        where c.measurement = 'P'
          and md.equipment_group in ('central_cooling','cooling_thermal','local_cooling','ventilation')
          and c.ts >= %(start_ts)s
          and c.ts <= %(end_ts)s
        order by c.ts, md.equipment_group, c.meter_urn
    """
    with conn.cursor() as cur:
        for ev in cool.itertuples(index=False):
            cur.execute(sql, {"start_ts": pd.Timestamp(ev.start_ts).to_pydatetime(), "end_ts": pd.Timestamp(ev.end_ts).to_pydatetime()})
            data = pd.DataFrame(cur.fetchall(), columns=[desc.name for desc in cur.description])
            if data.empty:
                continue
            for (group, meter, name), g in data.groupby(["equipment_group", "meter_urn", "equipment_name"]):
                values = pd.to_numeric(g["value"], errors="coerce")
                rows.append(
                    {
                        "event_id": ev.event_id,
                        "start_local_ts": ev.start_local_ts,
                        "end_local_ts": ev.end_local_ts,
                        "equipment_group": group,
                        "meter_urn": meter,
                        "equipment_name": name,
                        "mean_kw": float(values.mean() / 1000.0),
                        "max_kw": float(values.max() / 1000.0),
                        "hours": int(values.count()),
                    }
                )
            wide = data.pivot_table(index="ts", columns="meter_urn", values="value", aggfunc="mean")
            groups = {
                "central_cooling_sum": ["H1.Z11", "H1.Z12", "H1.Z16", "H1.Z24", "H1.Z25"],
                "ventilation_sum": ["H2.T.Z31", "H2.Z68", "H2.Z69", "H2.Z70", "H3.Z42"],
                "local_cooling_sum": ["H2.Z66", "H2.Z67", "H2.ZE66", "H2.ZE67", "H3.Z45"],
            }
            for name, cols in groups.items():
                have = [c for c in cols if c in wide]
                if not have:
                    continue
                s = wide[have].sum(axis=1)
                rows.append(
                    {
                        "event_id": ev.event_id,
                        "start_local_ts": ev.start_local_ts,
                        "end_local_ts": ev.end_local_ts,
                        "equipment_group": "aggregate",
                        "meter_urn": name,
                        "equipment_name": name,
                        "mean_kw": float(s.mean() / 1000.0),
                        "max_kw": float(s.max() / 1000.0),
                        "hours": int(s.count()),
                    }
                )
    conn.close()
    return pd.DataFrame(rows)


def plot_timeline(events: pd.DataFrame) -> Path:
    fig, ax = plt.subplots(figsize=(15, 6))
    plot_df = events.copy()
    y_map = {name: i for i, name in enumerate(sorted(plot_df["candidate_type"].unique()))}
    plot_df["y"] = plot_df["candidate_type"].map(y_map)
    x = pd.to_datetime(plot_df["start_ts"], utc=True).dt.tz_convert(TZ)
    sizes = np.clip(plot_df["duration_hours_observed"].astype(float) * 15, 35, 900)
    sc = ax.scatter(x, plot_df["y"], s=sizes, c=plot_df["max_abs_robust_z"], cmap="magma", alpha=0.72, edgecolor="black", linewidth=0.4)
    ax.set_yticks(list(y_map.values()), list(y_map.keys()))
    ax.set_title("Non-meter equipment-system candidate events: Cooling over-electricity and CHP strict physical/ratio cases")
    ax.set_xlabel("Local start time")
    ax.set_ylabel("Candidate type")
    ax.grid(True, alpha=0.25)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y", tz=plot_df["start_local_ts"].iloc[0].tzinfo if hasattr(plot_df["start_local_ts"].iloc[0], "tzinfo") else None))
    cb = fig.colorbar(sc, ax=ax)
    cb.set_label("max abs robust z")
    fig.tight_layout()
    path = OUT_FIG / "non_meter_equipment_candidate_timeline.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_deep_windows(selected: pd.DataFrame, cooling: pd.DataFrame, chp: pd.DataFrame) -> list[Path]:
    paths: list[Path] = []
    # Choose compact representative windows.
    cool_sel = selected[selected["module"] == "Cooling efficiency"].sort_values(["max_abs_robust_z", "duration_hours_observed"], ascending=[False, False]).head(4)
    chp_sel = selected[selected["module"] == "CHP operation"].sort_values(["max_abs_robust_z", "duration_hours_observed"], ascending=[False, False]).head(4)

    for module_name, sel, frame, fname in [
        ("Cooling over-electricity", cool_sel, cooling, "cooling_non_meter_deep_windows.png"),
        ("CHP strict physical/ratio", chp_sel, chp, "chp_non_meter_deep_windows.png"),
    ]:
        if sel.empty:
            continue
        fig, axes = plt.subplots(len(sel), 1, figsize=(15, 3.2 * len(sel)), sharex=False)
        if len(sel) == 1:
            axes = [axes]
        for ax, ev in zip(axes, sel.itertuples(index=False), strict=False):
            start = pd.Timestamp(ev.start_ts)
            end = pd.Timestamp(ev.end_ts)
            win = frame[(frame["ts"] >= start - pd.Timedelta(hours=24)) & (frame["ts"] <= end + pd.Timedelta(hours=24))].copy()
            x = win["local_ts"]
            if module_name.startswith("Cooling"):
                ax.plot(x, win["actual"] / 1000.0, label="actual cooling electric", color="#2ca02c", linewidth=1.8)
                ax.plot(x, win["pred"] / 1000.0, label="expected", color="#ff7f0e", linestyle="--", linewidth=1.5)
                ax.plot(x, win["cooling_thermal_P"] / 1000.0, label="thermal P", color="#1f77b4", alpha=0.75)
                ax2 = ax.twinx()
                ax2.bar(x, win["robust_z"], width=0.03, color="#9467bd", alpha=0.18, label="robust z")
                ax2.axhline(SEVERE_Z, color="#9467bd", linestyle=":", linewidth=1)
                ax2.set_ylabel("robust z")
            else:
                ax.plot(x, win["chp_heat_P"] / 1000.0, label="CHP heat P", color="#1f77b4", linewidth=1.7)
                ax.plot(x, win["abs_chp_elec_P"] / 1000.0, label="abs CHP electric P", color="#d62728", linewidth=1.7)
                ax.plot(x, win["pred"].abs() / 1000.0, label="expected abs/electric context", color="#ff7f0e", linestyle="--", alpha=0.8)
                ax2 = ax.twinx()
                ax2.bar(x, win["abs_ratio_robust_z"].fillna(0), width=0.03, color="#9467bd", alpha=0.18, label="ratio abs z")
                ax2.set_ylabel("ratio abs z")
            ax.axvspan(pd.Timestamp(ev.start_local_ts), pd.Timestamp(ev.end_local_ts), color="#d62728", alpha=0.12)
            ax.set_title(f"{ev.event_id} | {ev.start_local_ts} to {ev.end_local_ts} | hours={ev.duration_hours_observed} | max_z={ev.max_abs_robust_z:.1f}")
            ax.set_ylabel("kW")
            ax.grid(True, alpha=0.25)
            ax.legend(loc="upper left", ncol=3)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=x.dt.tz))
        fig.suptitle(f"{module_name}: selected non-meter candidate windows with ±24h context", fontsize=14, fontweight="bold")
        fig.tight_layout(rect=(0, 0, 1, 0.97))
        path = OUT_FIG / fname
        fig.savefig(path, dpi=180, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)
    return paths


def main() -> None:
    OUT_TAB.mkdir(parents=True, exist_ok=True)
    OUT_FIG.mkdir(parents=True, exist_ok=True)
    cooling, chp, events = build_frames()
    selected = select_events(events)
    precursor = precursor_summary(selected, cooling, chp)
    components = fetch_cooling_components_for_selected(selected)

    events.to_csv(OUT_TAB / "09_non_meter_equipment_candidate_events_1h.csv", index=False)
    selected.to_csv(OUT_TAB / "09_selected_deep_dive_events_1h.csv", index=False)
    precursor.to_csv(OUT_TAB / "09_precursor_summary_1h.csv", index=False)
    components.to_csv(OUT_TAB / "09_cooling_selected_component_summary_1h.csv", index=False)

    fig_timeline = plot_timeline(events)
    deep_paths = plot_deep_windows(selected, cooling, chp)

    brief = [
        "# Step 9 non-meter equipment-system deep dive",
        "",
        "- Exclusion rule: known Sept-2023 cooling thermal scaling window excluded; Cooling candidates use over-electricity direction only.",
        "- CHP candidates use strict heat/electric co-occurrence and power-to-heat ratio rules.",
        "- Boundary: BMS state, alarm, setpoint, maintenance log, and raw/source-lineage checks are required before fault confirmation.",
        "",
        "## Counts",
    ]
    counts = events.groupby(["module", "candidate_type"]).agg(events=("event_id", "count"), hours=("duration_hours_observed", "sum"), max_z=("max_abs_robust_z", "max")).reset_index()
    for r in counts.itertuples(index=False):
        brief.append(f"- {r.module} / {r.candidate_type}: events={r.events}, hours={r.hours}, max_z={r.max_z:.2f}")
    brief.extend(["", "## Selected events"])
    for r in selected.head(20).itertuples(index=False):
        brief.append(f"- {r.event_id}: {r.start_local_ts}~{r.end_local_ts}, hours={r.duration_hours_observed}, max_z={r.max_abs_robust_z:.2f}, type={r.candidate_type}")
    brief.extend(["", "## Figures", f"- {fig_timeline}"] + [f"- {p}" for p in deep_paths])
    (OUT_TAB / "STEP9_NON_METER_DEEP_DIVE_BRIEF.md").write_text("\n".join(brief) + "\n", encoding="utf-8")

    print(
        {
            "events": str(OUT_TAB / "09_non_meter_equipment_candidate_events_1h.csv"),
            "selected": str(OUT_TAB / "09_selected_deep_dive_events_1h.csv"),
            "precursor": str(OUT_TAB / "09_precursor_summary_1h.csv"),
            "cooling_components": str(OUT_TAB / "09_cooling_selected_component_summary_1h.csv"),
            "figures": [str(fig_timeline), *[str(p) for p in deep_paths]],
            "brief": str(OUT_TAB / "STEP9_NON_METER_DEEP_DIVE_BRIEF.md"),
        }
    )


if __name__ == "__main__":
    main()
