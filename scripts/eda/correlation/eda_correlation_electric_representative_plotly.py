from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "raw_eda" / "correlation" / "analysis"

import sys

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.meter_metadata import load_metadata
from scripts.pipeline.fetch_h1z16_with_weather import build_engine, fetch_meter_data


CONSUMPTION_FEATURES = ["P", "U1", "PF"]
PRODUCTION_FEATURES = ["P", "U1", "PF"]
ALL_FEATURES = ["P", "U1", "PF"]
ANALYSIS_YEARS = set(range(2018, 2024))
HIGH_CORR_THRESHOLD = 0.7

@dataclass
class GroupResult:
    group_name: str
    feature_matrices: dict[str, pd.DataFrame]
    overall_matrix: pd.DataFrame


def get_electric_meter_groups() -> tuple[list[str], list[str]]:
    metadata = load_metadata()
    electric_meters = {
        meter_urn: info
        for meter_urn, info in metadata.items()
        if info.get("meter_type") == "electric"
    }
    production = sorted(
        m for m, info in electric_meters.items() if info.get("energy_type") == "production"
    )
    consumption = sorted(
        m for m, info in electric_meters.items() if info.get("energy_type") != "production"
    )
    return consumption, production


def prepare_daily_feature_series(df: pd.DataFrame, feature: str) -> pd.Series | None:
    if feature not in df.columns:
        return None

    working = df[["ts", feature]].copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True, errors="coerce")
    working[feature] = pd.to_numeric(working[feature], errors="coerce")
    working = working.dropna(subset=["ts"]).sort_values("ts")
    working = working.loc[working["ts"].dt.year.isin(ANALYSIS_YEARS)].copy()
    if working.empty:
        return None

    daily = (
        working.assign(date=working["ts"].dt.floor("D"))
        .groupby("date")[feature]
        .median()
        .sort_index()
    )
    if daily.dropna().empty:
        return None
    return daily


def load_daily_feature_map(
    meter_urns: list[str],
    features: list[str],
) -> dict[str, dict[str, pd.Series]]:
    engine = build_engine()
    result: dict[str, dict[str, pd.Series]] = {}

    for meter_urn in meter_urns:
        print(f"[load] {meter_urn}")
        try:
            df = fetch_meter_data(engine, meter_urn).copy()
        except Exception as exc:
            print(f"  -> skip ({exc})")
            continue

        feature_map: dict[str, pd.Series] = {}
        for feature in features:
            series = prepare_daily_feature_series(df, feature)
            if series is not None:
                feature_map[feature] = series

        if feature_map:
            result[meter_urn] = feature_map
            print(f"  -> features: {sorted(feature_map)}")
        else:
            print("  -> no usable features")

    return result


def build_feature_matrix(
    meter_feature_map: dict[str, dict[str, pd.Series]],
    feature: str,
    meter_urns: list[str],
) -> pd.DataFrame:
    aligned: dict[str, pd.Series] = {}
    for meter_urn in meter_urns:
        series = meter_feature_map.get(meter_urn, {}).get(feature)
        if series is not None and series.dropna().shape[0] >= 2:
            aligned[meter_urn] = series

    if not aligned:
        return pd.DataFrame()

    combined = pd.concat(aligned, axis=1)
    combined.columns = list(aligned.keys())
    corr = combined.corr(method="pearson").abs()
    corr = corr.loc[sorted(corr.index), sorted(corr.columns)]
    return corr


def build_overall_matrix(feature_matrices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if not feature_matrices:
        return pd.DataFrame()

    meter_order = sorted(
        set().union(*[set(matrix.index) for matrix in feature_matrices.values() if not matrix.empty])
    )
    if not meter_order:
        return pd.DataFrame()

    accumulator = pd.DataFrame(0.0, index=meter_order, columns=meter_order)
    counts = pd.DataFrame(0, index=meter_order, columns=meter_order)

    for matrix in feature_matrices.values():
        if matrix.empty:
            continue
        aligned = matrix.reindex(index=meter_order, columns=meter_order)
        mask = aligned.notna()
        accumulator = accumulator.add(aligned.fillna(0.0), fill_value=0.0)
        counts = counts.add(mask.astype(int), fill_value=0)

    overall = accumulator.divide(counts.where(counts > 0), fill_value=np.nan)
    return overall


def build_group_result(
    group_name: str,
    meter_urns: list[str],
    meter_feature_map: dict[str, dict[str, pd.Series]],
    features: list[str],
) -> GroupResult:
    feature_matrices = {
        feature: build_feature_matrix(meter_feature_map, feature, meter_urns)
        for feature in features
    }
    overall_matrix = build_overall_matrix(feature_matrices)
    return GroupResult(
        group_name=group_name,
        feature_matrices=feature_matrices,
        overall_matrix=overall_matrix,
    )


def matrix_to_heatmap_payload(matrix: pd.DataFrame) -> tuple[list[str], list[list[float | None]], list[str]]:
    if matrix.empty:
        return [], [], []

    x_labels = list(matrix.columns)
    z_values = []
    for row_meter in matrix.index:
        row_values: list[float | None] = []
        for col_meter in matrix.columns:
            value = matrix.loc[row_meter, col_meter]
            row_values.append(None if pd.isna(value) else round(float(value), 4))
        z_values.append(row_values)
    return x_labels, z_values, list(matrix.index)


def build_heatmap_figure(
    consumption_result: GroupResult,
    production_result: GroupResult,
    output_path: Path,
) -> None:
    matrix_lookup: dict[str, dict[str, pd.DataFrame]] = {
        "Consumption": {
            feature: consumption_result.feature_matrices.get(feature, pd.DataFrame())
            for feature in ALL_FEATURES
        },
        "Production": {
            feature: production_result.feature_matrices.get(feature, pd.DataFrame())
            for feature in ALL_FEATURES
        },
    }
    matrix_lookup["Consumption"]["overall"] = consumption_result.overall_matrix
    matrix_lookup["Production"]["overall"] = production_result.overall_matrix

    features_with_overall = [*ALL_FEATURES, "overall"]
    groups = ["Consumption", "Production"]

    options: list[tuple[str, str, pd.DataFrame]] = [
        (g, f, matrix_lookup[g][f])
        for g in groups
        for f in features_with_overall
    ]

    traces: list[go.BaseTraceType] = []
    heatmap_trace_map: dict[str, list[int]] = {}  # "Consumption|P" -> [heatmap_idx, highlight_idx]

    # --- heatmap + highlight traces (2 * 10 = 20) ---
    for group_name, feature_name, matrix in options:
        key = f"{group_name}|{feature_name}"
        x_labels, z_values, y_labels = matrix_to_heatmap_payload(matrix)
        text_values = [
            ["" if v is None else f"{v:.2f}" for v in row]
            for row in z_values
        ]

        heatmap_idx = len(traces)
        traces.append(go.Heatmap(
            z=z_values,
            x=x_labels,
            y=y_labels,
            zmin=0,
            zmax=1,
            colorscale="RdBu_r",
            reversescale=False,
            hovertemplate="%{y} vs %{x}: %{z:.3f}<extra></extra>",
            colorbar={"title": "|corr|"},
            visible=(key == "Consumption|P"),
        ))

        heatmap_trace_map[key] = heatmap_idx

    # --- meter focus 1-row heatmap traces (overall matrix 기준) ---
    consumption_meters_sorted = (
        sorted(consumption_result.overall_matrix.index.tolist())
        if not consumption_result.overall_matrix.empty else []
    )
    production_meters_sorted = (
        sorted(production_result.overall_matrix.index.tolist())
        if not production_result.overall_matrix.empty else []
    )

    meter_trace_map: dict[str, dict[str, int]] = {}

    for _group_label, meters_sorted, result in [
        ("Consumption", consumption_meters_sorted, consumption_result),
        ("Production", production_meters_sorted, production_result),
    ]:
        for meter_urn in meters_sorted:
            meter_trace_map[meter_urn] = {}

            for feature_name in features_with_overall:
                idx = len(traces)
                meter_trace_map[meter_urn][feature_name] = idx

                if feature_name == "overall":
                    matrix = result.overall_matrix
                else:
                    matrix = result.feature_matrices.get(feature_name, pd.DataFrame())

                if matrix.empty or meter_urn not in matrix.index:
                    traces.append(go.Heatmap(z=[[]], x=[], y=[meter_urn], visible=False, showscale=False))
                    continue

                row = matrix.loc[meter_urn].drop(meter_urn, errors="ignore").dropna()
                row_sorted = row.sort_values(ascending=False)
                traces.append(go.Heatmap(
                    z=[list(row_sorted.values)],
                    x=list(row_sorted.index),
                    y=[meter_urn],
                    zmin=0,
                    zmax=1,
                    colorscale="RdBu_r",
                    reversescale=False,
                    hovertemplate="%{x}: %{z:.3f}<extra></extra>",
                    colorbar={"title": "|corr|"},
                    visible=False,
                    showscale=True,
                ))

    n_total = len(traces)

    # --- 3 separate dropdowns (method: "skip", JS handles state) ---
    group_buttons = [{"label": g, "method": "skip", "args": []} for g in groups]
    feature_buttons = [{"label": f, "method": "skip", "args": []} for f in features_with_overall]
    meter_buttons: list[dict] = [{"label": "All", "method": "skip", "args": []}]
    for gl, meters_sorted in [("Consumption", consumption_meters_sorted), ("Production", production_meters_sorted)]:
        prefix = "C" if gl == "Consumption" else "P"
        for meter_urn in meters_sorted:
            meter_buttons.append({"label": f"{prefix}: {meter_urn}", "method": "skip", "args": []})

    fig = go.Figure(data=traces)
    fig.update_layout(
        width=1600,
        height=1200,
        template="plotly_white",
        title="",
        margin={"r": 40, "t": 70, "l": 80, "b": 120},
        xaxis={"side": "bottom", "tickangle": -45, "tickfont": {"size": 9}},
        yaxis={"autorange": "reversed", "tickfont": {"size": 9}},
        updatemenus=[
            {
                "type": "dropdown",
                "x": 0.0,
                "y": 1.02,
                "xanchor": "left",
                "yanchor": "top",
                "buttons": group_buttons,
                "active": 0,
            },
            {
                "type": "dropdown",
                "x": 0.22,
                "y": 1.02,
                "xanchor": "left",
                "yanchor": "top",
                "buttons": feature_buttons,
                "active": 0,
            },
            {
                "type": "dropdown",
                "x": 0.50,
                "y": 1.02,
                "xanchor": "left",
                "yanchor": "top",
                "buttons": meter_buttons,
                "active": 0,
            },
        ],
        annotations=[
            {"xref": "paper", "yref": "paper", "x": 0.0, "y": 1.06, "showarrow": False, "text": "Group", "font": {"size": 13}},
            {"xref": "paper", "yref": "paper", "x": 0.22, "y": 1.06, "showarrow": False, "text": "Feature", "font": {"size": 13}},
            {"xref": "paper", "yref": "paper", "x": 0.50, "y": 1.06, "showarrow": False, "text": "Meter Focus", "font": {"size": 13}},
        ],
    )

    # JS state data embedded in post_script
    js_data = {
        "heatmap": heatmap_trace_map,
        "meter": meter_trace_map,
        "n_total": n_total,
        "groups": groups,
        "features": features_with_overall,
    }

    post_script = """
(function() {
    var gd = document.querySelector('.plotly-graph-div');
    if (!gd) return;
    var D = """ + json.dumps(js_data) + """;
    var state = {group: "Consumption", feature: "P", meter: "All"};

    function applyState() {
        var vis = new Array(D.n_total).fill(false);
        var newLayout = {};

        if (state.meter !== "All") {
            var meterFeatureMap = D.meter[state.meter];
            if (meterFeatureMap) {
                var idx = meterFeatureMap[state.feature];
                if (idx !== undefined) vis[idx] = true;
            }
            newLayout = {
                'yaxis.autorange': true,
                'margin.t': 110,
                'margin.b': 150,
                height: 820,
                'title.text': ''
            };
        } else {
            var key = state.group + '|' + state.feature;
            var idx = D.heatmap[key];
            if (idx !== undefined) vis[idx] = true;
            newLayout = {
                'yaxis.autorange': 'reversed',
                'margin.t': 70,
                'margin.b': 120,
                height: 1200,
                'title.text': '',
                'updatemenus[2].active': 0
            };
        }

        Plotly.restyle(gd, {visible: vis});
        Plotly.relayout(gd, newLayout);
    }

    gd.on('plotly_buttonclicked', function(data) {
        var label = data.button.label;
        if (D.groups.indexOf(label) >= 0) {
            state.group = label;
            state.meter = "All";
        } else if (D.features.indexOf(label) >= 0) {
            state.feature = label;
        } else if (label === "All") {
            state.meter = "All";
        } else {
            state.meter = label.replace(/^[CP]: /, '');
        }
        applyState();
    });
})();
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(output_path), post_script=post_script)
    print(f"saved: {output_path}")


def extract_high_corr_pairs(matrix: pd.DataFrame, threshold: float) -> list[tuple[str, str, float]]:
    pairs: list[tuple[str, str, float]] = []
    if matrix.empty:
        return pairs

    labels = list(matrix.index)
    for idx, left in enumerate(labels):
        for right in labels[idx + 1 :]:
            value = matrix.loc[left, right]
            if pd.isna(value):
                continue
            if float(value) >= threshold:
                pairs.append((left, right, float(value)))
    pairs.sort(key=lambda item: item[2], reverse=True)
    return pairs


def print_high_corr_pairs(group_result: GroupResult) -> None:
    count_summary: dict[str, int] = {}
    for feature, matrix in group_result.feature_matrices.items():
        pairs = extract_high_corr_pairs(matrix, HIGH_CORR_THRESHOLD)
        print(f"\n[{group_result.group_name} - {feature} 기준 상관관계 높은 쌍]")
        if not pairs:
            print("  없음")
        for left, right, value in pairs:
            print(f"  {left} ↔ {right}: {value:.3f}")
        count_summary[feature] = len(pairs)

    overall_pairs = extract_high_corr_pairs(group_result.overall_matrix, HIGH_CORR_THRESHOLD)
    print(f"\n[{group_result.group_name} - overall 기준 상관관계 높은 쌍]")
    if not overall_pairs:
        print("  없음")
    for left, right, value in overall_pairs:
        print(f"  {left} ↔ {right}: {value:.3f}")
    count_summary["overall"] = len(overall_pairs)

    print("\n0.7 이상 쌍 수:")
    for feature in [*ALL_FEATURES, "overall"]:
        print(f"  {group_result.group_name} {feature}: {count_summary.get(feature, 0)}쌍")


def build_components(matrix: pd.DataFrame, threshold: float) -> list[list[str]]:
    if matrix.empty:
        return []

    labels = list(matrix.index)
    adjacency: dict[str, set[str]] = {label: set() for label in labels}
    for idx, left in enumerate(labels):
        for right in labels[idx + 1 :]:
            value = matrix.loc[left, right]
            if pd.isna(value):
                continue
            if float(value) >= threshold:
                adjacency[left].add(right)
                adjacency[right].add(left)

    visited: set[str] = set()
    components: list[list[str]] = []
    for node in labels:
        if node in visited:
            continue
        stack = [node]
        component: list[str] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            stack.extend(sorted(adjacency[current] - visited))
        components.append(sorted(component))
    return components


def print_representative_candidates(group_result: GroupResult, basis_name: str, matrix: pd.DataFrame) -> None:
    print(f"\n[대표 계량기 후보 - {group_result.group_name} / {basis_name} 기준]")
    components = build_components(matrix, HIGH_CORR_THRESHOLD)
    if not components:
        print("  usable group 없음")
        return

    group_idx = 1
    for component in components:
        if len(component) == 1:
            print(f"단독: {component[0]} -> 상관 높은 그룹 없음, 개별 유지")
            continue

        avg_scores: dict[str, float] = {}
        for meter_urn in component:
            others = [other for other in component if other != meter_urn]
            values = [
                float(matrix.loc[meter_urn, other])
                for other in others
                if pd.notna(matrix.loc[meter_urn, other])
            ]
            avg_scores[meter_urn] = float(np.mean(values)) if values else float("nan")

        representative = max(avg_scores, key=lambda key: (-np.inf if pd.isna(avg_scores[key]) else avg_scores[key]))
        print(f"그룹 {group_idx}: {', '.join(component)}")
        print(f"  -> 대표 후보: {representative} (평균 상관 {avg_scores[representative]:.3f})")
        group_idx += 1

    print("* 최종 대표 계량기 선정은 사람이 판단")


def main() -> None:
    print(f"[info] output root (reserved): {OUTPUT_ROOT}")

    consumption_meters, production_meters = get_electric_meter_groups()

    print("[Consumption meters]")
    print(consumption_meters)
    print("\n[Production meters]")
    print(production_meters)

    all_target_meters = sorted(set(consumption_meters + production_meters))
    meter_feature_map = load_daily_feature_map(all_target_meters, ALL_FEATURES)

    consumption_result = build_group_result(
        group_name="Consumption",
        meter_urns=consumption_meters,
        meter_feature_map=meter_feature_map,
        features=CONSUMPTION_FEATURES,
    )
    production_result = build_group_result(
        group_name="Production",
        meter_urns=production_meters,
        meter_feature_map=meter_feature_map,
        features=PRODUCTION_FEATURES,
    )

    output_path = OUTPUT_ROOT / "electric_meter_correlation_representative.html"
    build_heatmap_figure(consumption_result, production_result, output_path)

    print_high_corr_pairs(consumption_result)
    print_high_corr_pairs(production_result)

    for group_result in [consumption_result, production_result]:
        for feature_name in ALL_FEATURES:
            print_representative_candidates(
                group_result,
                basis_name=feature_name,
                matrix=group_result.feature_matrices.get(feature_name, pd.DataFrame()),
            )
        print_representative_candidates(
            group_result,
            basis_name="overall",
            matrix=group_result.overall_matrix,
        )


if __name__ == "__main__":
    main()
