"""Build a polished HTML report for EMS equipment-system anomaly evidence.

The report uses existing read-only analysis outputs and regenerates cleaner figures.
It does not expose DB credentials. Interpretation boundary: review candidates, not
confirmed equipment-fault labels.
"""

from __future__ import annotations

import base64
import html
import re
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
    fit_relation_model,
)
from run_equipment_relation_strength_1h import connect, fetch_reduced_1h  # noqa: E402

OUT_TAB = ROOT / "outputs" / "tables" / "equipment_anomaly_validation"
OUT_FIG = ROOT / "outputs" / "figures" / "equipment_anomaly_validation"
REPORT_DIR = ROOT / "reports" / "equipment_anomaly_validation"
REPORT_PATH = REPORT_DIR / "equipment_anomaly_deep_dive.html"
TZ = "Europe/Berlin"

# Event IDs selected as representative evidence after excluding the Sept-2023 cooling scaling window.
COOLING_EVENT_IDS = [
    "cooling_efficiency_severe_over_electricity_candidate_144",
    "cooling_efficiency_severe_over_electricity_candidate_067",
    "cooling_efficiency_severe_over_electricity_candidate_071",
    "cooling_efficiency_severe_over_electricity_candidate_081",
]
CHP_EVENT_IDS = [
    "chp_operation_electricity_without_heat_candidate_004",
    "chp_operation_heat_without_electricity_candidate_001",
    "chp_operation_electricity_without_heat_candidate_014",
    "chp_operation_power_to_heat_ratio_deviation_candidate_021",
]


def set_style() -> None:
    try:
        import koreanize_matplotlib  # noqa: F401
    except Exception:
        pass
    plt.rcParams.update(
        {
            "figure.facecolor": "#fbfaf7",
            "axes.facecolor": "#fffdf8",
            "axes.edgecolor": "#d8d1c4",
            "axes.labelcolor": "#2d2a26",
            "axes.titlecolor": "#171512",
            "xtick.color": "#3c3832",
            "ytick.color": "#3c3832",
            "grid.color": "#e8e1d6",
            "grid.linewidth": 0.8,
            "font.size": 10,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "legend.frameon": True,
            "legend.framealpha": 0.92,
            "legend.facecolor": "#fffdf8",
            "legend.edgecolor": "#e5ddcf",
        }
    )


def read_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    events = pd.read_csv(OUT_TAB / "09_non_meter_equipment_candidate_events_1h.csv")
    selected = pd.read_csv(OUT_TAB / "09_selected_deep_dive_events_1h.csv")
    precursor = pd.read_csv(OUT_TAB / "09_precursor_summary_1h.csv")
    components = pd.read_csv(OUT_TAB / "09_cooling_selected_component_summary_1h.csv")
    for frame in [events, selected, precursor, components]:
        for col in ["start_ts", "end_ts", "start_local_ts", "end_local_ts"]:
            if col in frame.columns:
                frame[col] = pd.to_datetime(frame[col], errors="coerce", utc=True)
    return events, selected, precursor, components


def build_model_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
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

    cooling = cooling_model.frame.copy()
    cooling["actual"] = cooling["cooling_elec_P"]
    cooling["ts"] = pd.to_datetime(cooling["ts"], utc=True)
    cooling["local_ts"] = pd.to_datetime(cooling["local_ts"], utc=True).dt.tz_convert(TZ)

    chp = chp_model.frame.copy()
    chp["actual"] = chp["chp_elec_P"]
    chp["abs_chp_elec_P"] = chp["chp_elec_P"].abs()
    chp["chp_el_heat_ratio_abs"] = np.where(chp["chp_heat_P"] > 0, chp["abs_chp_elec_P"] / chp["chp_heat_P"], np.nan)
    chp["ts"] = pd.to_datetime(chp["ts"], utc=True)
    chp["local_ts"] = pd.to_datetime(chp["local_ts"], utc=True).dt.tz_convert(TZ)
    return cooling, chp


def fetch_component_timeseries(start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
    conn = connect()
    conn.execute("set default_transaction_read_only = on")
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
        cur.execute(sql, {"start_ts": start_ts.to_pydatetime(), "end_ts": end_ts.to_pydatetime()})
        rows = cur.fetchall()
        cols = [desc.name for desc in cur.description]
    conn.close()
    frame = pd.DataFrame(rows, columns=cols)
    if frame.empty:
        return frame
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame["local_ts"] = frame["ts"].dt.tz_convert(TZ)
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    return frame


def image_data_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def local_label(ts: pd.Timestamp | str) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert(TZ).strftime("%Y-%m-%d %H:%M")


def parse_feature_notes(series: pd.Series) -> pd.DataFrame:
    rows = []
    for text in series.fillna(""):
        row: dict[str, float] = {}
        for key in ["heat_event_mean_kw", "abs_elec_event_mean_kw", "ratio_event_mean", "thermal_event_mean_kw", "Ta_event_mean", "actual_minus_pred_event_mean_kw"]:
            match = re.search(rf"{key}=([^;]+)", text)
            if match:
                try:
                    row[key] = float(match.group(1))
                except ValueError:
                    row[key] = np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def fig_candidate_overview(events: pd.DataFrame) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(15.5, 6.2), gridspec_kw={"width_ratios": [0.95, 1.55]})
    counts = (
        events.groupby(["module", "candidate_type"])
        .agg(events=("event_id", "count"), hours=("duration_hours_observed", "sum"), max_z=("max_abs_robust_z", "max"))
        .reset_index()
    )
    counts["label"] = counts["module"].str.replace(" operation", "", regex=False).str.replace(" efficiency", "", regex=False) + "\n" + counts[
        "candidate_type"
    ].str.replace("_candidate", "", regex=False).str.replace("_", " ")
    colors = ["#3b6f84" if "Cooling" in m else "#9b5a3c" for m in counts["module"]]
    axes[0].barh(counts["label"], counts["hours"], color=colors, alpha=0.9)
    axes[0].set_title("후보 시간 수: 계측 scaling 제외 후")
    axes[0].set_xlabel("observed candidate hours")
    axes[0].grid(axis="x", alpha=0.5)
    for i, row in counts.reset_index(drop=True).iterrows():
        axes[0].text(row["hours"] + max(counts["hours"]) * 0.015, i, f"{int(row['hours'])}h / {int(row['events'])} events", va="center", fontsize=9)

    plot_df = events.copy()
    plot_df["start_local"] = pd.to_datetime(plot_df["start_ts"], utc=True).dt.tz_convert(TZ)
    y_order = [
        "severe_over_electricity_candidate",
        "electricity_without_heat_candidate",
        "heat_without_electricity_candidate",
        "power_to_heat_ratio_deviation_candidate",
    ]
    y_map = {name: i for i, name in enumerate(y_order)}
    plot_df = plot_df[plot_df["candidate_type"].isin(y_map)]
    plot_df["y"] = plot_df["candidate_type"].map(y_map)
    size = np.clip(plot_df["duration_hours_observed"].astype(float) * 20, 35, 1150)
    palette = plot_df["module"].map({"Cooling efficiency": "#2e6f95", "CHP operation": "#b46645"}).fillna("#777")
    axes[1].scatter(plot_df["start_local"], plot_df["y"], s=size, c=palette, alpha=0.68, edgecolors="#1b1b1b", linewidths=0.35)
    axes[1].set_yticks(list(y_map.values()), [x.replace("_candidate", "").replace("_", " ") for x in y_order])
    axes[1].set_title("반복 분포: 긴 이벤트는 큰 원으로 표시")
    axes[1].xaxis.set_major_locator(mdates.YearLocator())
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y", tz=plot_df["start_local"].dt.tz))
    axes[1].grid(True, alpha=0.35)
    axes[1].set_xlabel("local start year")
    axes[1].set_ylim(-0.7, len(y_order) - 0.3)
    fig.suptitle("Equipment-system review candidates after excluding metering-scaling cases", fontsize=16, fontweight="bold", y=1.02)
    fig.tight_layout()
    path = OUT_FIG / "html_polished_candidate_overview.png"
    fig.savefig(path, dpi=190, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_cooling_component_summary(components: pd.DataFrame) -> Path:
    rows = []
    for event_id, group in components.groupby("event_id"):
        if event_id not in COOLING_EVENT_IDS:
            continue
        ag = group[group["equipment_group"] == "aggregate"].set_index("meter_urn")
        meters = group[group["equipment_group"] == "central_cooling"].set_index("meter_urn")
        central = float(ag.loc["central_cooling_sum", "mean_kw"]) if "central_cooling_sum" in ag.index else np.nan
        cm2 = float(meters.loc[[m for m in ["H1.Z11", "H1.Z12"] if m in meters.index], "mean_kw"].sum())
        cm1 = float(meters.loc["H1.Z16", "mean_kw"]) if "H1.Z16" in meters.index else 0.0
        cm3 = float(meters.loc[[m for m in ["H1.Z24", "H1.Z25"] if m in meters.index], "mean_kw"].sum())
        rows.append(
            {
                "event_id": event_id,
                "label": event_id.rsplit("_", 1)[-1],
                "start": local_label(group["start_local_ts"].iloc[0]),
                "central": central,
                "cm2": cm2,
                "cm1": cm1,
                "cm3": cm3,
                "vent": float(ag.loc["ventilation_sum", "mean_kw"]) if "ventilation_sum" in ag.index else np.nan,
                "local": float(ag.loc["local_cooling_sum", "mean_kw"]) if "local_cooling_sum" in ag.index else np.nan,
                "thermal": float(group[group["equipment_group"] == "cooling_thermal"]["mean_kw"].mean()),
            }
        )
    df = pd.DataFrame(rows).sort_values("start")
    labels = [f"{r.start[:10]}\n{r.label}" for r in df.itertuples(index=False)]
    x = np.arange(len(df))
    width = 0.18
    fig, axes = plt.subplots(2, 1, figsize=(14, 8.8), gridspec_kw={"height_ratios": [1.2, 0.8]})
    bottom = np.zeros(len(df))
    for col, color, label in [("cm2", "#2e6f95", "CM2: H1.Z11+H1.Z12"), ("cm1", "#c69c6d", "CM1"), ("cm3", "#8a8f98", "CM3")]:
        axes[0].bar(x, df[col], bottom=bottom, color=color, width=0.62, label=label)
        bottom += df[col].to_numpy()
    axes[0].plot(x, df["thermal"], color="#4b3f72", marker="o", linewidth=2.3, label="Cooling thermal P")
    axes[0].set_title("Cooling 후보별 central cooling 전력 구성")
    axes[0].set_ylabel("mean kW")
    axes[0].set_xticks(x, labels)
    axes[0].legend(ncol=4, loc="upper left")
    axes[0].grid(axis="y", alpha=0.4)

    axes[1].bar(x - width, df["central"], width, color="#2e6f95", label="central cooling")
    axes[1].bar(x, df["vent"], width, color="#d28b45", label="ventilation context")
    axes[1].bar(x + width, df["local"], width, color="#7a9e7e", label="local cooling context")
    share = (df["cm2"] / df["central"] * 100).replace([np.inf, -np.inf], np.nan)
    for i, pct in enumerate(share):
        axes[1].text(i - width, df["central"].iloc[i] + 2.5, f"CM2 {pct:.0f}%", ha="center", fontsize=9, color="#1d4e65")
    axes[1].set_title("CM2 집중도와 보조 계통 동시 부하")
    axes[1].set_ylabel("mean kW")
    axes[1].set_xticks(x, labels)
    axes[1].legend(ncol=3, loc="upper left")
    axes[1].grid(axis="y", alpha=0.4)
    fig.tight_layout()
    path = OUT_FIG / "html_polished_cooling_component_summary.png"
    fig.savefig(path, dpi=190, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_cooling_representative_window(cooling: pd.DataFrame) -> Path:
    start = pd.Timestamp("2023-08-20 00:00:00", tz="UTC")
    end = pd.Timestamp("2023-08-23 12:00:00", tz="UTC")
    comp = fetch_component_timeseries(start, end)
    wide = comp.pivot_table(index=["ts", "local_ts"], columns="meter_urn", values="value", aggfunc="mean").reset_index()
    for col in wide.columns:
        if col not in {"ts", "local_ts"}:
            wide[col] = pd.to_numeric(wide[col], errors="coerce") / 1000.0
    ctx = cooling[(cooling["ts"] >= start) & (cooling["ts"] <= end)].copy()
    ctx["actual_kw"] = ctx["actual"] / 1000.0
    ctx["pred_kw"] = ctx["pred"] / 1000.0
    ctx["thermal_kw"] = ctx["cooling_thermal_P"] / 1000.0
    ctx["residual_kw"] = ctx["residual"] / 1000.0
    merged = ctx.merge(wide, on=["ts", "local_ts"], how="left")
    merged["cm2_kw"] = merged[[c for c in ["H1.Z11", "H1.Z12"] if c in merged]].sum(axis=1)
    merged["cm1_cm3_kw"] = merged[[c for c in ["H1.Z16", "H1.Z24", "H1.Z25"] if c in merged]].sum(axis=1)
    merged["vent_kw"] = merged[[c for c in ["H2.T.Z31", "H2.Z68", "H2.Z69", "H2.Z70", "H3.Z42"] if c in merged]].sum(axis=1)
    merged["local_cooling_kw"] = merged[[c for c in ["H2.Z66", "H2.Z67", "H2.ZE66", "H2.ZE67", "H3.Z45"] if c in merged]].sum(axis=1)
    cand_start = pd.Timestamp("2023-08-21 08:00:00", tz=TZ)
    cand_end = pd.Timestamp("2023-08-21 23:00:00", tz=TZ)

    fig, axes = plt.subplots(4, 1, figsize=(15.2, 11.2), sharex=True)
    x = merged["local_ts"]
    axes[0].plot(x, merged["actual_kw"], color="#266b8f", linewidth=2.2, label="actual cooling electric")
    axes[0].plot(x, merged["pred_kw"], color="#c56b38", linewidth=2.0, linestyle="--", label="expected electric")
    axes[0].plot(x, merged["thermal_kw"], color="#4b3f72", linewidth=1.8, label="thermal P")
    axes[0].set_ylabel("kW")
    axes[0].set_title("2023-08-21: low thermal context with high cooling electric demand")
    axes[0].legend(ncol=3, loc="upper left")

    axes[1].fill_between(x, 0, merged["cm2_kw"], color="#2e6f95", alpha=0.85, label="CM2 H1.Z11+H1.Z12")
    axes[1].fill_between(x, merged["cm2_kw"], merged["cm2_kw"] + merged["cm1_cm3_kw"], color="#c69c6d", alpha=0.8, label="CM1+CM3")
    axes[1].set_ylabel("central kW")
    axes[1].set_title("Central cooling attribution: CM2 explains almost all electric load")
    axes[1].legend(ncol=2, loc="upper left")

    axes[2].bar(x, merged["residual_kw"], width=0.035, color="#7f4f9f", alpha=0.55, label="actual - expected")
    axes[2].plot(x, merged["robust_z"], color="#472d64", linewidth=1.5, label="robust z")
    axes[2].axhline(6, color="#b33f2d", linestyle="--", linewidth=1.1, label="severe threshold")
    axes[2].set_ylabel("kW / z")
    axes[2].set_title("Residual accumulates before and during the candidate window")
    axes[2].legend(ncol=3, loc="upper left")

    axes[3].plot(x, merged["vent_kw"], color="#d28b45", linewidth=2.0, label="ventilation context")
    axes[3].plot(x, merged["local_cooling_kw"], color="#7a9e7e", linewidth=2.0, label="local cooling context")
    axes[3].plot(x, merged["Ta"], color="#222", linewidth=1.5, label="Ta °C")
    axes[3].set_ylabel("kW / °C")
    axes[3].set_title("Co-occurring HVAC context")
    axes[3].legend(ncol=3, loc="upper left")

    for ax in axes:
        ax.axvspan(cand_start, cand_end, color="#b33f2d", alpha=0.12)
        ax.grid(True, alpha=0.35)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=x.dt.tz))
    axes[-1].set_xlabel("local time (Europe/Berlin)")
    fig.tight_layout()
    path = OUT_FIG / "html_polished_cooling_2023_08_21_window.png"
    fig.savefig(path, dpi=190, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_chp_candidates(precursor: pd.DataFrame) -> Path:
    chp = precursor[precursor["event_id"].isin(CHP_EVENT_IDS)].copy()
    feats = parse_feature_notes(chp["feature_notes"])
    chp = pd.concat([chp.reset_index(drop=True), feats], axis=1)
    chp["short"] = chp["start_local_ts"].apply(lambda x: local_label(x)[:10]) + "\n" + chp["candidate_type"].str.replace("_candidate", "", regex=False).str.replace("_", " ")
    x = np.arange(len(chp))
    fig, axes = plt.subplots(2, 1, figsize=(14.8, 8.8), gridspec_kw={"height_ratios": [1.1, 0.9]})
    width = 0.34
    axes[0].bar(x - width / 2, chp["heat_event_mean_kw"].fillna(0), width, color="#4b3f72", label="CHP heat mean")
    axes[0].bar(x + width / 2, chp["abs_elec_event_mean_kw"].fillna(0), width, color="#b46645", label="abs CHP electric mean")
    axes[0].set_xticks(x, chp["short"], fontsize=9)
    axes[0].set_ylabel("mean kW")
    axes[0].set_title("CHP strict mismatch: heat and electric production lose co-occurrence")
    axes[0].legend(ncol=2, loc="upper left")
    axes[0].grid(axis="y", alpha=0.4)

    axes[1].bar(x, chp["event_max_abs_z"], color="#6f5f90", alpha=0.86, label="event max z")
    axes[1].plot(x, chp["pre24_moderate_hours"], color="#d28b45", marker="o", linewidth=2.1, label="pre-24h moderate hours")
    axes[1].axhline(6, color="#b33f2d", linestyle="--", linewidth=1, label="severe threshold")
    for i, r in chp.iterrows():
        ratio = r.get("ratio_event_mean", np.nan)
        label = "ratio n/a" if not np.isfinite(ratio) or ratio > 10 else f"ratio {ratio:.3f}"
        axes[1].text(i, r["event_max_abs_z"] + 3, label, ha="center", fontsize=9)
    axes[1].set_xticks(x, chp["short"], fontsize=9)
    axes[1].set_ylabel("z / hours")
    axes[1].set_title(f"Severity and lead-up evidence; public power-to-heat ratio reference = {CHP_PUBLIC_POWER_TO_HEAT_RATIO}")
    axes[1].legend(ncol=3, loc="upper right")
    axes[1].grid(axis="y", alpha=0.4)
    fig.tight_layout()
    path = OUT_FIG / "html_polished_chp_strict_candidates.png"
    fig.savefig(path, dpi=190, bbox_inches="tight")
    plt.close(fig)
    return path


def make_table(headers: list[str], rows: list[list[str]], *, cls: str = "data-table") -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table class='{cls}'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def build_html(figs: dict[str, Path], events: pd.DataFrame, precursor: pd.DataFrame, components: pd.DataFrame) -> str:
    counts = (
        events.groupby(["module", "candidate_type"])
        .agg(events=("event_id", "count"), hours=("duration_hours_observed", "sum"), max_z=("max_abs_robust_z", "max"))
        .reset_index()
    )
    count_rows = []
    for r in counts.itertuples(index=False):
        count_rows.append(
            [
                html.escape(str(r.module)),
                html.escape(str(r.candidate_type).replace("_candidate", "").replace("_", " ")),
                f"{int(r.events):,}",
                f"{int(r.hours):,} h",
                f"{float(r.max_z):.2f}",
            ]
        )

    availability_rows = [
        ["BMS on/off state", "확인 안 됨", "공개 논문·Dryad·GitHub에서 state log 파일/필드 미확인"],
        ["alarm log", "확인 안 됨", "alarm/event ticket 형태의 공개 자료 미확인"],
        ["maintenance ticket", "확인 안 됨", "maintenance 언급은 있으나 ticket 상세 공개 미확인"],
        ["setpoint / run command", "확인 안 됨", "setpoint, command, CHP run command 미확인"],
        ["manual/auto mode", "확인 안 됨", "manual/automatic은 issue labeling 방식 의미로 확인"],
        ["compressor/pump/fan state", "부분 맥락", "recooler fan 전력은 ventilation 계통 설명에 존재. state log는 없음"],
        ["정비·교체 일자", "부분 존재", "meter replacement, gateway replacement, CHP control logic update, modernization 날짜 존재"],
    ]

    external_event_rows = [
        ["V.K21 main cooling meter failure", "mechanical flow sensor broke twice", "계측 센서 failure. CM1/CM2/CM3 고장 라벨로 사용 불가"],
        ["Design studio meters powered off", "2018-05-14 ~ 2018-06-04", "데이터 gap/계량 중단 맥락"],
        ["Office transformer meter replacement", "2020-09-09 12:00 UTC ~ 2020-09-15 10:00 UTC", "H2.Z35/36 → H2.Z351/361 교체"],
        ["Gateway hardware failures", "2020, 2021, 2022 네 차례", "Tixi gateway hardware defect 및 replacement"],
        ["CHP control logic update", "2019-02-13", "0/100% 운전에서 50~100% modulation 가능 상태로 전환"],
        ["Heating/ventilation modernization", "2023-06", "CHP integration/control 맥락 변화"],
        ["Cooling machines not running", "2023-09-20 18:00~06:00", "thermal P scaling issue 맥락. 설비 고장 label로 사용 불가"],
    ]

    cooling_rows = []
    for event_id in COOLING_EVENT_IDS:
        g = components[components["event_id"] == event_id]
        if g.empty:
            continue
        ag = g[g["equipment_group"] == "aggregate"].set_index("meter_urn")
        meters = g[g["equipment_group"] == "central_cooling"].set_index("meter_urn")
        central = float(ag.loc["central_cooling_sum", "mean_kw"]) if "central_cooling_sum" in ag.index else np.nan
        cm2 = float(meters.loc[[m for m in ["H1.Z11", "H1.Z12"] if m in meters.index], "mean_kw"].sum())
        thermal = float(g[g["equipment_group"] == "cooling_thermal"]["mean_kw"].mean())
        event_row = precursor[precursor["event_id"] == event_id].iloc[0]
        cooling_rows.append(
            [
                local_label(event_row["start_local_ts"]),
                f"{int(event_row['duration_hours_observed'])} h",
                f"{float(event_row['event_max_abs_z']):.2f}",
                f"{central:.2f} kW",
                f"{cm2:.2f} kW ({cm2 / central * 100:.1f}%)" if central else "-",
                f"{thermal:.2f} kW",
                f"{float(event_row['pre24_moderate_hours']):.0f} h",
            ]
        )

    chp_rows = []
    chp_prec = precursor[precursor["event_id"].isin(CHP_EVENT_IDS)].copy()
    feats = parse_feature_notes(chp_prec["feature_notes"])
    chp_prec = pd.concat([chp_prec.reset_index(drop=True), feats], axis=1)
    for r in chp_prec.itertuples(index=False):
        ratio = getattr(r, "ratio_event_mean", np.nan)
        ratio_text = "n/a" if not np.isfinite(ratio) or ratio > 10 else f"{ratio:.3f}"
        chp_rows.append(
            [
                local_label(r.start_local_ts),
                html.escape(str(r.candidate_type).replace("_candidate", "").replace("_", " ")),
                f"{int(r.duration_hours_observed)} h",
                f"{float(r.event_max_abs_z):.2f}",
                f"{float(getattr(r, 'heat_event_mean_kw', np.nan)):.2f} kW",
                f"{float(getattr(r, 'abs_elec_event_mean_kw', np.nan)):.2f} kW",
                ratio_text,
                f"{int(r.pre24_moderate_hours)} h",
            ]
        )

    css = """
:root{
  --bg:#f5f1e9; --paper:#fffdf8; --ink:#1d1a16; --muted:#6f675c; --line:#ded5c8;
  --cool:#2e6f95; --chp:#b46645; --gold:#c49343; --soft:#ece4d6; --warn:#8f3f2f;
  --shadow:0 24px 60px rgba(45,35,20,.10);
}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR","Apple SD Gothic Neo",Arial,sans-serif;line-height:1.65;}
a{color:var(--cool);text-decoration:none;border-bottom:1px solid color-mix(in srgb,var(--cool),transparent 60%)}
.layout{display:grid;grid-template-columns:280px minmax(0,1fr);min-height:100vh}.toc{position:sticky;top:0;height:100vh;padding:28px 22px;border-right:1px solid var(--line);background:#efe8dc;overflow:auto}.brand{font-size:12px;letter-spacing:.15em;text-transform:uppercase;color:var(--muted);font-weight:800}.toc h1{font-size:21px;line-height:1.25;margin:16px 0 24px}.toc a{display:block;padding:9px 0;color:#3a332b;border-bottom:0;font-size:14px}.doc{max-width:1180px;width:100%;padding:40px 56px 80px}.hero{background:linear-gradient(135deg,#fffdf8 0%,#f4ead9 100%);border:1px solid var(--line);border-radius:28px;padding:42px;box-shadow:var(--shadow);margin-bottom:28px}.eyebrow{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--cool);font-weight:800;margin:0 0 12px}.hero h2{font-size:42px;line-height:1.12;margin:0 0 18px;letter-spacing:-.04em}.deck{font-size:18px;color:#423b33;max-width:880px}.summary-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:28px}.metric{background:#171512;color:#fff;border-radius:20px;padding:20px}.metric span{display:block;color:#cfc3b5;font-size:13px}.metric strong{display:block;font-size:25px;line-height:1.15;margin-top:7px}.section{background:var(--paper);border:1px solid var(--line);border-radius:24px;padding:30px;margin:24px 0;box-shadow:0 10px 30px rgba(45,35,20,.05)}.section h2{font-size:28px;margin:0 0 14px;letter-spacing:-.025em}.section h3{font-size:20px;margin:26px 0 10px}.lead{font-size:17px;color:#403930}.callout{border-radius:18px;padding:18px 20px;background:#f0e7d9;border:1px solid #decfba;margin:18px 0}.callout strong{color:#1f4f67}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:18px}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.card{border:1px solid var(--line);background:#fbf6ee;border-radius:18px;padding:18px}.card h4{margin:0 0 8px;font-size:16px}.card p{margin:0;color:var(--muted)}.figure{margin:22px 0;border:1px solid var(--line);background:#fbf6ee;border-radius:22px;overflow:hidden}.figure img{display:block;width:100%;height:auto}.figcap{padding:14px 18px;color:#5d554b;font-size:14px;border-top:1px solid var(--line);background:#f7efe4}.data-table{width:100%;border-collapse:separate;border-spacing:0;margin:16px 0;border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#fffaf1}.data-table th{background:#ede1d0;text-align:left;font-size:13px;color:#463d33;padding:11px 12px;border-bottom:1px solid var(--line)}.data-table td{padding:11px 12px;border-bottom:1px solid #ebe2d7;vertical-align:top}.data-table tr:nth-child(even) td{background:#fcf5ea}.data-table tr:last-child td{border-bottom:0}.tag{display:inline-block;border-radius:999px;padding:3px 9px;background:#e6f0f3;color:#235873;font-size:12px;font-weight:700}.warn{background:#f4e4dc;color:#7a351f}.ok{background:#e8f1e4;color:#3f6f33}.footnotes{font-size:13px;color:#62594e}.decision{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.decision .card{min-height:142px}.src-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.src{padding:14px;border:1px solid var(--line);border-radius:16px;background:#fbf6ee}.src b{display:block}.src small{color:var(--muted)}
@media (max-width:980px){.layout{display:block}.toc{position:relative;height:auto}.doc{padding:26px 18px}.hero h2{font-size:30px}.summary-grid,.two-col,.cards,.decision,.src-list{grid-template-columns:1fr}}
@media print{.toc{display:none}.layout{display:block}.doc{padding:0}.section,.hero{box-shadow:none;break-inside:avoid}.figure{break-inside:avoid}}
"""

    html_doc = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EMS 설비계통 이상 후보 심층 검토</title>
<style>{css}</style>
</head>
<body>
<div class="layout">
  <aside class="toc">
    <div class="brand">EMS Evidence Report</div>
    <h1>설비계통 이상 후보 심층 검토</h1>
    <a href="#summary">판단 요약</a>
    <a href="#data">공개 데이터 확인</a>
    <a href="#events">외부 event 맥락</a>
    <a href="#overview">후보 분포</a>
    <a href="#cooling">Cooling CM2</a>
    <a href="#chp">CHP</a>
    <a href="#boundary">해석 경계</a>
    <a href="#sources">출처</a>
  </aside>
  <main class="doc">
    <section class="hero" id="summary">
      <p class="eyebrow">Honda R&amp;D Europe · EMS · 2018–2023</p>
      <h2>고장 확정 로그 없이 설비계통 이상 후보를 어디까지 말할 수 있는가</h2>
      <p class="deck">Nature Scientific Data, Dryad, HRI-EU GitHub, 로컬 EMS DB를 함께 확인했다. 공개 범위에는 BMS state, alarm log, setpoint, run command가 보이지 않는다. 설비 고장 라벨 검증은 닫히지 않는다. 대신 계측 scaling 후보를 제외한 뒤에도 Cooling CM2와 CHP heat/electric mismatch는 운영 점검 후보로 남는다.</p>
      <div class="summary-grid">
        <div class="metric"><span>공개 BMS/알람/정비 ticket</span><strong>미확인</strong></div>
        <div class="metric"><span>Cooling over-electricity</span><strong>156 events · 789 h</strong></div>
        <div class="metric"><span>CHP strict mismatch</span><strong>37 typed events · 88 h*</strong></div>
      </div>
      <p class="footnotes">* CHP typed events는 유형별 count 합계다. 시간 union 기준은 기존 strict physical/ratio union 88 h / 34 events 범위다.</p>
    </section>

    <section class="section" id="data">
      <h2>1. 공개 자료에서 확인한 데이터 존재 여부</h2>
      <p class="lead">고장 전조를 supervised 방식으로 검증하려면 state·alarm·ticket 계열 데이터가 필요하다. 공개 자료에서 확인된 항목은 meter time series와 issue labels 중심이다.</p>
      {make_table(["필요 데이터", "공개 확인", "판단"], availability_rows)}
      <div class="callout"><strong>판단:</strong> 현재 공개 데이터만으로 “고장 전조 → 실제 고장 발생” 연결 검증은 수행할 수 없다. 분석 결과는 설비계통 운영 점검 후보로 다룬다.</div>
    </section>

    <section class="section" id="events">
      <h2>2. 외부 자료에서 확인되는 event 맥락</h2>
      <p class="lead">논문과 Dryad 설명에는 장비 교체, gateway defect, CHP control logic 변경, heating/ventilation modernization, cooling thermal scaling anomaly가 있다. 이들은 분석 구간을 분리하는 기준으로 유용하다.</p>
      {make_table(["event", "공개 자료 요지", "분석에서의 사용"], external_event_rows)}
    </section>

    <section class="section" id="overview">
      <h2>3. 계측 scaling 후보 제외 후 남은 설비계통 후보</h2>
      <p class="lead">Cooling은 2023년 9월 thermal scaling 맥락을 제외하고, 실제 전력이 기대값보다 높은 방향만 남겼다. CHP는 heat/electric co-occurrence와 power-to-heat ratio 규칙으로 좁혔다.</p>
      {make_table(["module", "candidate type", "event 수", "시간 수", "max z"], count_rows)}
      <div class="figure"><img src="{image_data_uri(figs['overview'])}" alt="candidate overview"><div class="figcap">후보 시간 수와 반복 분포. Cooling 후보는 2020년 9월 및 2023년 8월에 강한 cluster가 보인다. CHP 후보는 strict mismatch 기준으로 적은 수의 event가 남는다.</div></div>
    </section>

    <section class="section" id="cooling">
      <h2>4. Cooling CM2: 가장 강한 운영 점검 후보</h2>
      <p class="lead">대표 Cooling 후보 대부분에서 central cooling 전력의 89–99%가 CM2 계통 H1.Z11/H1.Z12로 설명된다. 이 패턴은 thermal load, 외기온, 시간대 기준 기대 전력보다 실제 전력이 큰 방향이다.</p>
      {make_table(["시작", "지속", "max z", "central 평균", "CM2 평균", "thermal 평균", "직전 24h moderate"], cooling_rows)}
      <div class="figure"><img src="{image_data_uri(figs['cooling_components'])}" alt="cooling component summary"><div class="figcap">CM2 집중도. 2023-08-21 후보는 central cooling 평균 93.01 kW 중 CM2가 92.24 kW를 차지한다. 2020년 9월 대표 후보들도 CM2 비중이 높다.</div></div>
      <div class="figure"><img src="{image_data_uri(figs['cooling_window'])}" alt="cooling 2023 window"><div class="figcap">2023-08-21 상세 window. thermal P는 낮고 안정적인 편이며, 실제 cooling electric P가 기대 전력을 장시간 초과한다. CM2가 전력 상승을 거의 전부 설명한다.</div></div>
      <div class="cards">
        <div class="card"><h4>가능한 해석</h4><p>CM2 계통 제어 상태, 부분부하 효율, 보조부하, 스케줄 운전, thermal coverage 문제를 점검할 후보다.</p></div>
        <div class="card"><h4>필요한 후속 데이터</h4><p>CM2 compressor/pump/fan state, setpoint, manual/auto mode, alarm, 정비 이력이 붙어야 fault precursor 검증이 가능하다.</p></div>
      </div>
    </section>

    <section class="section" id="chp">
      <h2>5. CHP: control regime을 분리해야 하는 mismatch 후보</h2>
      <p class="lead">CHP는 부호 규약과 control regime 영향이 크다. 공개 자료에는 2019-02-13 control logic update와 2023-06 heating/ventilation modernization이 명시된다. 이 이벤트를 반영한 뒤 heat/electric mismatch를 봐야 한다.</p>
      {make_table(["시작", "유형", "지속", "max z", "heat 평균", "abs electric 평균", "ratio", "직전 24h moderate"], chp_rows)}
      <div class="figure"><img src="{image_data_uri(figs['chp'])}" alt="chp strict candidates"><div class="figcap">CHP strict 후보. electricity-without-heat, heat-without-electricity, ratio deviation은 서로 다른 운전·제어 상태를 시사한다. run command와 BMS state가 없으면 고장 원인을 확정할 수 없다.</div></div>
    </section>

    <section class="section" id="boundary">
      <h2>6. 결론과 해석 경계</h2>
      <div class="decision">
        <div class="card"><h4>확정 가능</h4><p>공개 자료에는 meter/gateway/weather-station issue, device replacement, CHP control logic change, modernization 맥락이 있다.</p></div>
        <div class="card"><h4>후보화 가능</h4><p>Cooling CM2와 CHP heat/electric mismatch는 물리 관계 이탈과 반복성에 기반한 운영 점검 후보로 정의할 수 있다.</p></div>
        <div class="card"><h4>확정 불가</h4><p>공개 자료만으로 설비 고장 원인, 고장 발생, 고장 전조 검증 완료를 주장할 수 없다.</p></div>
      </div>
      <div class="callout"><strong>권장 표현:</strong> “현장 BMS·알람·정비 이력 결합 시 고장 전조 검증으로 확장 가능한 설비계통 이상 후보”</div>
    </section>

    <section class="section" id="sources">
      <h2>7. 확인 출처</h2>
      <div class="src-list">
        <div class="src"><b>Nature Scientific Data article</b><small>A Real-World Energy Management Dataset from a Smart Company Building for Optimization and Machine Learning</small><br><a href="https://doi.org/10.1038/s41597-025-05186-3">DOI 10.1038/s41597-025-05186-3</a></div>
        <div class="src"><b>Dryad dataset</b><small>Data and issues archive, DOI 10.5061/dryad.73n5tb363</small><br><a href="https://datadryad.org/dataset/doi:10.5061/dryad.73n5tb363">Dryad dataset page</a></div>
        <div class="src"><b>Honda RI PDF</b><small>Accepted article PDF used for text search and event snippets</small><br><a href="https://www.honda-ri.de/pubs/pdf/6282.pdf">Honda RI publication PDF</a></div>
        <div class="src"><b>HRI-EU GitHub</b><small>MonitoringDatasetAnalysis code, issue template and meter category files</small><br><a href="https://github.com/HRI-EU/MonitoringDatasetAnalysis">GitHub repository</a></div>
      </div>
      <p class="footnotes">로컬 산출 표: <code>09_non_meter_equipment_candidate_events_1h.csv</code>, <code>09_precursor_summary_1h.csv</code>, <code>09_cooling_selected_component_summary_1h.csv</code>. DB 접속 정보는 문서에 포함하지 않았다.</p>
    </section>
  </main>
</div>
</body>
</html>"""
    return html_doc


def main() -> None:
    set_style()
    OUT_FIG.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    events, selected, precursor, components = read_inputs()
    cooling, chp = build_model_frames()
    figs = {
        "overview": fig_candidate_overview(events),
        "cooling_components": fig_cooling_component_summary(components),
        "cooling_window": fig_cooling_representative_window(cooling),
        "chp": fig_chp_candidates(precursor),
    }
    html_doc = build_html(figs, events, precursor, components)
    REPORT_PATH.write_text(html_doc, encoding="utf-8")
    # Lightweight validation outputs.
    print({
        "report": str(REPORT_PATH),
        "figures": {k: str(v) for k, v in figs.items()},
        "html_bytes": REPORT_PATH.stat().st_size,
    })


if __name__ == "__main__":
    main()
