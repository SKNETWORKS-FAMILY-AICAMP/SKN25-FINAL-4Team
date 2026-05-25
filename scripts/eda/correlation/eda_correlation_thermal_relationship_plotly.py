from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "raw_eda" / "correlation" / "analysis"
OUTPUT_HTML = OUTPUT_DIR / "thermal_meter_relationship_dashboard.html"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.meter_metadata import get_metadata
from scripts.pipeline.fetch_h1z16_with_weather import (  # noqa: E402
    build_engine,
    fetch_meter_data,
    fetch_weather_data,
)


COOLING_METERS = [
    "V.K21",
    "H1.K11",
    "H1.K12",
    "H1.K14",
    "H1.K15",
    "H1.K16",
    "H2.K21",
]

HEATING_METERS = [
    "H1.W11",
    "H1.W12",
]

COOLING_FEATURES = ["P", "qv", "Tdiff"]
HEATING_FEATURES = ["P", "qv", "Tdiff"]
OVERALL_FEATURES = ["P", "qv", "Tdiff"]
ANALYSIS_YEARS = set(range(2018, 2024))
HIGH_CORR_THRESHOLD = 0.7


@dataclass
class GroupResult:
    group_name: str
    features: list[str]
    daily_feature_map: dict[str, dict[str, pd.Series]]
    feature_matrices: dict[str, pd.DataFrame]
    overall_matrix: pd.DataFrame
    high_pairs: dict[str, list[dict[str, object]]]


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
    if daily.dropna().shape[0] < 2:
        return None
    return daily


def load_daily_feature_map(
    meter_urns: list[str],
    features: list[str],
) -> dict[str, dict[str, pd.Series]]:
    engine = build_engine()
    weather_df = fetch_weather_data(engine).copy()
    weather_df["ts"] = pd.to_datetime(weather_df["ts"], utc=True, errors="coerce")
    for column in ["Ta", "Igm"]:
        if column in weather_df.columns:
            weather_df[column] = pd.to_numeric(weather_df[column], errors="coerce")

    result: dict[str, dict[str, pd.Series]] = {}

    for meter_urn in meter_urns:
        print(f"[load] {meter_urn}")
        try:
            meter_df = fetch_meter_data(engine, meter_urn).copy()
        except Exception as exc:
            print(f"  -> skip ({exc})")
            continue

        meter_df["ts"] = pd.to_datetime(meter_df["ts"], utc=True, errors="coerce")
        merged = meter_df.merge(weather_df[["ts", "Ta", "Igm"]], on="ts", how="left")

        feature_map: dict[str, pd.Series] = {}
        for feature in features:
            series = prepare_daily_feature_series(merged, feature)
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
    meter_urns: list[str],
    feature: str,
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
    corr = corr.loc[list(aligned.keys()), list(aligned.keys())]
    return corr


def collect_high_corr_pairs(matrix: pd.DataFrame, threshold: float) -> list[dict[str, object]]:
    if matrix.empty:
        return []

    pairs: list[dict[str, object]] = []
    for row_idx, meter_a in enumerate(matrix.index):
        for col_idx, meter_b in enumerate(matrix.columns):
            if col_idx <= row_idx:
                continue
            value = matrix.loc[meter_a, meter_b]
            if pd.notna(value) and float(value) >= threshold:
                pairs.append(
                    {
                        "a": meter_a,
                        "b": meter_b,
                        "corr": round(float(value), 3),
                    }
                )
    pairs.sort(key=lambda item: item["corr"], reverse=True)
    return pairs


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

    overall = accumulator.divide(counts.where(counts > 0), fill_value=pd.NA)
    return overall


def build_group_result(
    group_name: str,
    meter_urns: list[str],
    features: list[str],
) -> GroupResult:
    daily_feature_map = load_daily_feature_map(meter_urns, features)
    feature_matrices = {
        feature: build_feature_matrix(daily_feature_map, meter_urns, feature)
        for feature in features
    }
    overall_matrices = {
        feature: matrix
        for feature, matrix in feature_matrices.items()
        if feature in OVERALL_FEATURES
    }
    overall_matrix = build_overall_matrix(overall_matrices)
    high_pairs = {
        feature: collect_high_corr_pairs(feature_matrices[feature], HIGH_CORR_THRESHOLD)
        for feature in features
    }
    high_pairs["overall"] = collect_high_corr_pairs(overall_matrix, HIGH_CORR_THRESHOLD)
    return GroupResult(
        group_name=group_name,
        features=features,
        daily_feature_map=daily_feature_map,
        feature_matrices=feature_matrices,
        overall_matrix=overall_matrix,
        high_pairs=high_pairs,
    )


def matrix_to_payload(matrix: pd.DataFrame) -> dict[str, object]:
    if matrix.empty:
        return {
            "x": [],
            "y": [],
            "z": [],
            "text": [],
        }

    x_labels = list(matrix.columns)
    y_labels = list(matrix.index)
    z_values: list[list[float | None]] = []
    text_values: list[list[str]] = []

    for row_meter in y_labels:
        z_row: list[float | None] = []
        text_row: list[str] = []
        for col_meter in x_labels:
            value = matrix.loc[row_meter, col_meter]
            if pd.isna(value):
                z_row.append(None)
                text_row.append("")
                continue
            numeric_value = round(float(value), 4)
            z_row.append(numeric_value)
            text_row.append(f"{numeric_value:.2f}")
        z_values.append(z_row)
        text_values.append(text_row)

    return {
        "x": x_labels,
        "y": y_labels,
        "z": z_values,
        "text": text_values,
    }


def interpret_strength(value: float | None) -> str:
    if value is None:
        return "해석 불가 (공통 유효 구간 부족)"
    abs_value = abs(value)
    if abs_value >= 0.9:
        return "매우 강한 관계"
    if abs_value >= 0.7:
        return "강한 관계"
    if abs_value >= 0.5:
        return "중간 수준 관계"
    return "약한 관계"


def build_relationship_summary(
    cooling_result: GroupResult,
    heating_result: GroupResult,
) -> dict[str, object]:
    cooling_p = cooling_result.feature_matrices.get("P", pd.DataFrame())
    heating_p = heating_result.feature_matrices.get("P", pd.DataFrame())

    downstream_values: list[float] = []
    downstream_pairs: list[dict[str, object]] = []
    if not cooling_p.empty and "V.K21" in cooling_p.index:
        for meter in [m for m in COOLING_METERS if m != "V.K21" and m in cooling_p.columns]:
            value = cooling_p.loc["V.K21", meter]
            if pd.notna(value):
                numeric_value = round(float(value), 3)
                downstream_values.append(float(value))
                downstream_pairs.append({"meter": meter, "corr": numeric_value})

    h1k15_values: list[float] = []
    h1k15_pairs: list[dict[str, object]] = []
    if not cooling_p.empty and "H1.K15" in cooling_p.index:
        for meter in [m for m in COOLING_METERS if m != "H1.K15" and m in cooling_p.columns]:
            value = cooling_p.loc["H1.K15", meter]
            if pd.notna(value):
                numeric_value = round(float(value), 3)
                h1k15_values.append(float(value))
                h1k15_pairs.append({"meter": meter, "corr": numeric_value})

    h1w_pair_corr = None
    if not heating_p.empty and {"H1.W11", "H1.W12"}.issubset(set(heating_p.index)):
        h1w_pair_corr = round(float(heating_p.loc["H1.W11", "H1.W12"]), 3)

    avg_vk21 = sum(downstream_values) / len(downstream_values) if downstream_values else None
    avg_h1k15 = sum(h1k15_values) / len(h1k15_values) if h1k15_values else None

    return {
        "vk21_downstream": {
            "avg_corr_p": None if avg_vk21 is None else round(avg_vk21, 3),
            "pairs": downstream_pairs,
            "interpretation": (
                "상위 집계가 하위 냉각 계량기 패턴을 비교적 잘 대표"
                if avg_vk21 is not None and avg_vk21 >= HIGH_CORR_THRESHOLD
                else "하위 계량기 개별 패턴 차이가 커서 개별 분석 필요"
            ),
        },
        "h1k15": {
            "avg_corr_p": None if avg_h1k15 is None else round(avg_h1k15, 3),
            "pairs": h1k15_pairs,
            "interpretation": (
                "장기 미가동 영향으로 다른 냉각 계량기와 독립적일 가능성"
                if avg_h1k15 is not None and avg_h1k15 < 0.5
                else "다른 냉각 계량기와 유사 패턴이 일부 존재"
            ),
        },
        "heating_pair": {
            "corr_p": h1w_pair_corr,
            "interpretation": (
                "CHP 열 생산량이 총 열 생산량과 매우 유사하게 움직임"
                if h1w_pair_corr is not None and h1w_pair_corr >= HIGH_CORR_THRESHOLD
                else "총 열 생산량과 CHP 열 생산량의 패턴 차이를 추가 확인할 필요"
            ),
        },
    }


def build_meter_meta() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for meter_urn in COOLING_METERS + HEATING_METERS:
        info = get_metadata(meter_urn) or {}
        result[meter_urn] = {
            "description": info.get("description") or meter_urn,
            "group": info.get("group_name") or "-",
            "thermal_mode": info.get("thermal_mode") or "-",
        }
    return result


def print_high_corr_report(result: GroupResult) -> None:
    for feature in [*result.features, "overall"]:
        print(f"\n[{result.group_name} — {feature} 기준 상관관계 높은 쌍]")
        pairs = result.high_pairs.get(feature, [])
        if not pairs:
            print("  없음")
            continue
        for pair in pairs:
            print(f"  {pair['a']} ↔ {pair['b']}: {pair['corr']:.3f}")

    print(f"\n[{result.group_name} feature별 0.7 이상 쌍 수]")
    for feature in [*result.features, "overall"]:
        print(f"  {result.group_name} {feature}: {len(result.high_pairs.get(feature, []))}쌍")


def print_relationship_summary(summary: dict[str, object]) -> None:
    vk21 = summary["vk21_downstream"]
    h1k15 = summary["h1k15"]
    heating_pair = summary["heating_pair"]

    print("\n[관계 해석]")
    print("V.K21 ↔ 하위 계량기 평균 상관:")
    print(f"  P 기준: {vk21['avg_corr_p']}")
    print(f"  → 해석: {vk21['interpretation']}")

    print("\nH1.K15 평균 상관 (다른 냉각 계량기와):")
    print(f"  P 기준: {h1k15['avg_corr_p']}")
    print(f"  → 해석: {h1k15['interpretation']}")

    print("\nH1.W11 ↔ H1.W12:")
    print(f"  P 기준: {heating_pair['corr_p']}")
    print(f"  → 해석: {heating_pair['interpretation']}")


def build_html(
    dashboard_payload: dict[str, dict[str, dict[str, object]]],
    meter_meta: dict[str, dict[str, str]],
    relationship_summary: dict[str, object],
) -> str:
    payload_json = json.dumps(dashboard_payload, ensure_ascii=False)
    meter_meta_json = json.dumps(meter_meta, ensure_ascii=False)
    summary_json = json.dumps(relationship_summary, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Thermal Meter Relationship Dashboard</title>
  <script src="https://cdn.plot.ly/plotly-3.1.0.min.js"></script>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #f6f3ee 0%, #ece2d5 100%);
      color: #1f1a16;
    }}
    .wrap {{
      max-width: 1540px;
      margin: 0 auto;
      padding: 24px;
    }}
    .panel {{
      background: rgba(255, 252, 248, 0.96);
      border: 1px solid #e1d5c3;
      border-radius: 18px;
      box-shadow: 0 16px 44px rgba(72, 53, 26, 0.08);
      padding: 22px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
    }}
    .subtitle {{
      margin: 0 0 18px;
      color: #6d5b43;
      line-height: 1.5;
    }}
    .controls {{
      display: grid;
      grid-template-columns: repeat(3, minmax(220px, 280px));
      gap: 12px;
      margin-bottom: 14px;
      align-items: end;
    }}
    label {{
      display: block;
      margin-bottom: 6px;
      font-size: 13px;
      font-weight: 700;
      color: #6f542b;
      letter-spacing: 0.02em;
    }}
    select {{
      width: 100%;
      padding: 10px 12px;
      border: 1px solid #d7c4aa;
      border-radius: 10px;
      background: #fff;
      font-size: 14px;
      color: #1f1a16;
    }}
    .meta {{
      margin-bottom: 10px;
      color: #6e614e;
      font-size: 13px;
    }}
    #plot {{
      width: 100%;
      height: 860px;
    }}
    .notes {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(2, minmax(280px, 1fr));
      gap: 14px;
    }}
    .note-card {{
      background: #fffdf9;
      border: 1px solid #eadcc8;
      border-radius: 14px;
      padding: 14px 16px;
    }}
    .note-card h2 {{
      margin: 0 0 8px;
      font-size: 16px;
    }}
    .note-card p, .note-card li {{
      margin: 0;
      color: #5d5141;
      line-height: 1.5;
      font-size: 13px;
    }}
    .note-card ul {{
      margin: 8px 0 0;
      padding-left: 18px;
    }}
    @media (max-width: 900px) {{
      .controls, .notes {{
        grid-template-columns: 1fr;
      }}
      #plot {{
        height: 640px;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1>Thermal Meter Relationship Dashboard</h1>
      <p class="subtitle">
        열량계 9개를 cooling / heating으로 나눠 raw daily median 기준 상관관계를 확인합니다.
        feature는 P, qv, Tdiff와 그 평균인 overall만 사용합니다.
        대표 선정이 아니라 상위/하위 관계, 미가동 계량기 분리, 냉방/난방 패턴 차이 파악이 목적입니다.
      </p>
      <div class="controls">
        <div>
          <label for="group-select">Group</label>
          <select id="group-select">
            <option value="Cooling">Cooling</option>
            <option value="Heating">Heating</option>
          </select>
        </div>
        <div>
          <label for="feature-select">Feature</label>
          <select id="feature-select"></select>
        </div>
        <div>
          <label for="meter-select">Meter Focus</label>
          <select id="meter-select"></select>
        </div>
      </div>
      <div class="meta" id="meta-text"></div>
      <div id="plot"></div>
      <div class="notes">
        <div class="note-card">
          <h2>관계 해석 포인트</h2>
          <ul id="relationship-list"></ul>
        </div>
        <div class="note-card">
          <h2>현재 선택 요약</h2>
          <ul id="selection-list"></ul>
        </div>
      </div>
    </div>
  </div>
  <script>
    const dashboardData = {payload_json};
    const meterMeta = {meter_meta_json};
    const relationshipSummary = {summary_json};
    const threshold = {HIGH_CORR_THRESHOLD};

    const groupSelect = document.getElementById("group-select");
    const featureSelect = document.getElementById("feature-select");
    const meterSelect = document.getElementById("meter-select");
    const metaText = document.getElementById("meta-text");
    const relationshipList = document.getElementById("relationship-list");
    const selectionList = document.getElementById("selection-list");

    const groupFeatures = {{
      Cooling: ["P", "qv", "Tdiff", "overall"],
      Heating: ["P", "qv", "Tdiff", "overall"],
    }};

    const groupMeters = {{
      Cooling: {json.dumps(COOLING_METERS, ensure_ascii=False)},
      Heating: {json.dumps(HEATING_METERS, ensure_ascii=False)},
    }};

    function setOptions(selectEl, values) {{
      selectEl.innerHTML = "";
      values.forEach((value) => {{
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        selectEl.appendChild(option);
      }});
    }}

    function formatMeterLabel(meter) {{
      const info = meterMeta[meter] || {{}};
      return `${{meter}} - ${{info.description || meter}}`;
    }}

    function renderRelationshipNotes() {{
      const items = [
        `V.K21 ↔ 하위 냉각 계량기 평균(P): ${{relationshipSummary.vk21_downstream.avg_corr_p}} | ${{relationshipSummary.vk21_downstream.interpretation}}`,
        `H1.K15 평균(P): ${{relationshipSummary.h1k15.avg_corr_p}} | ${{relationshipSummary.h1k15.interpretation}}`,
        `H1.W11 ↔ H1.W12(P): ${{relationshipSummary.heating_pair.corr_p}} | ${{relationshipSummary.heating_pair.interpretation}}`,
      ];
      relationshipList.innerHTML = "";
      items.forEach((item) => {{
        const li = document.createElement("li");
        li.textContent = item;
        relationshipList.appendChild(li);
      }});
    }}

    function renderSelectionSummary(groupName, featureName, block) {{
      selectionList.innerHTML = "";
      const pairs = block.high_pairs || [];
      const infoLines = [
        `계량기 수: ${{block.x.length}}`,
        `0.7 이상 쌍 수: ${{pairs.length}}`,
      ];
      infoLines.forEach((line) => {{
        const li = document.createElement("li");
        li.textContent = line;
        selectionList.appendChild(li);
      }});
      pairs.slice(0, 8).forEach((pair) => {{
        const li = document.createElement("li");
        li.textContent = `${{pair.a}} ↔ ${{pair.b}}: ${{pair.corr.toFixed(3)}}`;
        selectionList.appendChild(li);
      }});
      if (pairs.length === 0) {{
        const li = document.createElement("li");
        li.textContent = "threshold 이상 쌍 없음";
        selectionList.appendChild(li);
      }}
    }}

    function updateMeterOptions() {{
      const groupName = groupSelect.value;
      setOptions(meterSelect, ["__all__", ...groupMeters[groupName]]);
      meterSelect.options[0].textContent = "All Meters";
    }}

    function buildHeatmapData(block, focusedMeter) {{
      if (focusedMeter === "__all__") {{
        return {{
          x: block.x,
          y: block.y,
          z: block.z,
          text: block.text,
          titleSuffix: "Full Matrix",
          singleRow: false,
        }};
      }}

      const rowIndex = block.y.indexOf(focusedMeter);
      if (rowIndex === -1) {{
        return {{
          x: [],
          y: [],
          z: [],
          text: [],
          titleSuffix: `${{focusedMeter}} not available`,
          singleRow: true,
        }};
      }}

      return {{
        x: block.x,
        y: [focusedMeter],
        z: [block.z[rowIndex]],
        text: [block.text[rowIndex]],
        titleSuffix: `${{focusedMeter}} vs All`,
        singleRow: true,
      }};
    }}

    function buildPlot(groupName, featureName) {{
      const block = dashboardData[groupName][featureName];
      const focusedMeter = meterSelect.value;
      const heatmapData = buildHeatmapData(block, focusedMeter);
      const heatmapTrace = {{
        type: "heatmap",
        z: heatmapData.z,
        x: heatmapData.x,
        y: heatmapData.y,
        zmin: 0,
        zmax: 1,
        colorscale: "RdBu_r",
        reversescale: false,
        text: heatmapData.text,
        texttemplate: "%{{text}}",
        textfont: {{ size: heatmapData.singleRow ? 13 : (groupName === "Heating" ? 16 : 10) }},
        hovertemplate: "%{{y}} vs %{{x}}: %{{z:.3f}}<extra></extra>",
        colorbar: {{ title: "|corr|" }},
      }};

      const layout = {{
        title: `${{groupName}} | ${{featureName}} | Daily Median Absolute Pearson Correlation | ${{heatmapData.titleSuffix}}`,
        width: 1320,
        height: heatmapData.singleRow ? 360 : (groupName === "Heating" ? 680 : 980),
        margin: {{ l: 120, r: 40, t: 70, b: 110 }},
        template: "plotly_white",
        xaxis: {{
          side: "bottom",
          tickangle: -45,
        }},
        yaxis: {{
          autorange: "reversed",
        }},
      }};

      Plotly.react("plot", [heatmapTrace], layout, {{ responsive: true }});

      const meterSummary = block.x.map((meter) => formatMeterLabel(meter)).join(" | ");
      metaText.textContent = `group=${{groupName}} | feature=${{featureName}} | focus=${{focusedMeter}} | threshold=${{threshold}} | meters=${{meterSummary}}`;
      renderSelectionSummary(groupName, featureName, block);
    }}

    function updateFeatureOptions() {{
      const groupName = groupSelect.value;
      setOptions(featureSelect, groupFeatures[groupName]);
    }}

    function render() {{
      buildPlot(groupSelect.value, featureSelect.value);
    }}

    groupSelect.addEventListener("change", () => {{
      updateFeatureOptions();
      updateMeterOptions();
      render();
    }});
    featureSelect.addEventListener("change", render);
    meterSelect.addEventListener("change", render);

    updateFeatureOptions();
    updateMeterOptions();
    renderRelationshipNotes();
    render();
  </script>
</body>
</html>
"""


def main() -> None:
    cooling_result = build_group_result("Cooling", COOLING_METERS, COOLING_FEATURES)
    heating_result = build_group_result("Heating", HEATING_METERS, HEATING_FEATURES)

    print_high_corr_report(cooling_result)
    print_high_corr_report(heating_result)

    relationship_summary = build_relationship_summary(cooling_result, heating_result)
    print_relationship_summary(relationship_summary)

    dashboard_payload = {
        "Cooling": {
            feature: {
                **matrix_to_payload(
                    cooling_result.overall_matrix
                    if feature == "overall"
                    else cooling_result.feature_matrices.get(feature, pd.DataFrame())
                ),
                "high_pairs": cooling_result.high_pairs.get(feature, []),
            }
            for feature in [*COOLING_FEATURES, "overall"]
        },
        "Heating": {
            feature: {
                **matrix_to_payload(
                    heating_result.overall_matrix
                    if feature == "overall"
                    else heating_result.feature_matrices.get(feature, pd.DataFrame())
                ),
                "high_pairs": heating_result.high_pairs.get(feature, []),
            }
            for feature in [*HEATING_FEATURES, "overall"]
        },
    }

    meter_meta = build_meter_meta()
    html = build_html(dashboard_payload, meter_meta, relationship_summary)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"\nsaved: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
