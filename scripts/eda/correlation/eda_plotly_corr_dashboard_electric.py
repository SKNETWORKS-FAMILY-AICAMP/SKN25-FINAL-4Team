from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from config.meter_metadata import load_metadata
from scripts.eda.correlation.eda_raw_correlation_representative_electric import (
    MIN_PERIOD_ROWS,
    SEASON_ORDER,
    add_period_columns,
    build_corr_matrix,
    fetch_meter_raw_df,
    fetch_weather_df,
    select_usable_columns,
)
from scripts.pipeline.fetch_h1z16_with_weather import build_engine


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "raw_eda" / "correlation" / "plotly"
OUTPUT_HTML = OUTPUT_DIR / "electric_correlation_dashboard.html"
VIEW_LABELS = {
    "6year": "6-Year",
    "yearly": "Yearly",
    "seasonal": "Seasonal",
}


def get_electric_meters() -> list[str]:
    metadata = load_metadata()
    return sorted([meter for meter, info in metadata.items() if info.get("meter_type") == "electric"])


def build_block_payload(period_df: pd.DataFrame, candidate_columns: list[str]) -> dict[str, object] | None:
    usable_columns = select_usable_columns(period_df, candidate_columns)
    if len(period_df) < MIN_PERIOD_ROWS or len(usable_columns) < 2:
        return None

    corr_df = build_corr_matrix(period_df, usable_columns)
    columns = list(corr_df.columns)
    return {
        "columns": columns,
        "z": [[round(float(corr_df.loc[row, col]), 6) for col in columns] for row in columns],
    }


def collect_payload() -> dict[str, dict[str, dict[str, object]]]:
    engine = build_engine()
    weather_df, weather_columns = fetch_weather_df(engine)
    payload: dict[str, dict[str, dict[str, object]]] = {}

    for meter_urn in get_electric_meters():
        try:
            raw_df, measurements = fetch_meter_raw_df(engine, meter_urn)
        except Exception as exc:
            print(f"{meter_urn} raw fetch 실패: {exc}")
            continue

        merged_df = add_period_columns(raw_df.merge(weather_df, on="ts", how="left"))
        candidate_columns = measurements + weather_columns
        meter_payload: dict[str, dict[str, object]] = {}

        sixyear_block = build_block_payload(merged_df, candidate_columns)
        if sixyear_block is not None:
            meter_payload["6year"] = {"all": sixyear_block}

        yearly_blocks: dict[str, object] = {}
        for year in sorted(int(year) for year in merged_df["year"].dropna().unique()):
            block = build_block_payload(merged_df.loc[merged_df["year"] == year].copy(), candidate_columns)
            if block is not None:
                yearly_blocks[str(year)] = block
        if yearly_blocks:
            meter_payload["yearly"] = yearly_blocks

        seasonal_blocks: dict[str, object] = {}
        for season in SEASON_ORDER:
            block = build_block_payload(merged_df.loc[merged_df["season"] == season].copy(), candidate_columns)
            if block is not None:
                seasonal_blocks[season] = block
        if seasonal_blocks:
            meter_payload["seasonal"] = seasonal_blocks

        if meter_payload:
            payload[meter_urn] = meter_payload
            print(f"{meter_urn} correlation payload 저장 준비 완료")

    return payload


def build_meter_meta(payload: dict[str, dict[str, dict[str, object]]]) -> dict[str, dict[str, str]]:
    metadata = load_metadata()
    result: dict[str, dict[str, str]] = {}
    for meter in payload.keys():
        info = metadata.get(meter, {})
        description = info.get("description") or meter
        result[meter] = {
            "label": f"{meter} - {description}",
            "description": description,
            "group": info.get("group_name") or "-",
            "energy_type": info.get("energy_type") or "-",
        }
    return result


def build_html(
    payload: dict[str, dict[str, dict[str, object]]],
    meter_meta: dict[str, dict[str, str]],
) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    view_labels_json = json.dumps(VIEW_LABELS, ensure_ascii=False)
    meter_meta_json = json.dumps(meter_meta, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Electric Correlation Dashboard</title>
  <script src="https://cdn.plot.ly/plotly-3.1.0.min.js"></script>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #f3f7fc 0%, #e8eef8 100%);
      color: #182235;
    }}
    .wrap {{
      max-width: 1480px;
      margin: 0 auto;
      padding: 24px;
    }}
    .panel {{
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid #d6e0ef;
      border-radius: 20px;
      box-shadow: 0 18px 50px rgba(25, 43, 84, 0.08);
      padding: 20px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 28px;
    }}
    p {{
      margin: 0 0 18px;
      color: #53647f;
    }}
    .controls {{
      display: grid;
      grid-template-columns: repeat(4, minmax(180px, 240px));
      gap: 12px;
      margin-bottom: 18px;
    }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 6px;
      color: #35507e;
      letter-spacing: 0.02em;
    }}
    select {{
      width: 100%;
      padding: 10px 12px;
      border-radius: 10px;
      border: 1px solid #bfd0ea;
      background: #fff;
      color: #182235;
      font-size: 14px;
    }}
    .meta {{
      margin-bottom: 8px;
      font-size: 13px;
      color: #61728e;
    }}
    #plot {{
      width: 100%;
      height: 960px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1>Electric Correlation Dashboard</h1>
      <p>DB raw 기준 전체 전기 계량기 correlation matrix를 하나의 Heatmap 뷰어로 통합했습니다.</p>
      <div class="controls">
        <div>
          <label for="meter-select">Meter</label>
          <select id="meter-select"></select>
        </div>
        <div>
          <label for="view-select">View</label>
          <select id="view-select"></select>
        </div>
        <div>
          <label for="detail-select" id="detail-label">Detail</label>
          <select id="detail-select"></select>
        </div>
        <div>
          <label for="column-select">Column</label>
          <select id="column-select"></select>
        </div>
      </div>
      <div class="meta" id="meta-text"></div>
      <div id="plot"></div>
    </div>
  </div>
  <script>
    const dashboardData = {payload_json};
    const viewLabels = {view_labels_json};
    const meterMeta = {meter_meta_json};
    const meterSelect = document.getElementById("meter-select");
    const viewSelect = document.getElementById("view-select");
    const detailSelect = document.getElementById("detail-select");
    const columnSelect = document.getElementById("column-select");
    const detailLabel = document.getElementById("detail-label");
    const metaText = document.getElementById("meta-text");

    function setOptions(selectEl, options) {{
      selectEl.innerHTML = "";
      options.forEach((option) => {{
        const el = document.createElement("option");
        if (typeof option === "string") {{
          el.value = option;
          el.textContent = option;
        }} else {{
          el.value = option.value;
          el.textContent = option.label;
        }}
        selectEl.appendChild(el);
      }});
    }}

    function initMeterOptions() {{
      setOptions(
        meterSelect,
        Object.keys(dashboardData).map((meter) => ({{ value: meter, label: meterMeta[meter].label }}))
      );
      setOptions(viewSelect, Object.entries(viewLabels).map(([value, label]) => ({{ value, label }})));
    }}

    function getSelectedBlock() {{
      return dashboardData[meterSelect.value][viewSelect.value][detailSelect.value];
    }}

    function updateDetailOptions() {{
      const details = Object.keys(dashboardData[meterSelect.value][viewSelect.value]);
      const label = viewSelect.value === "yearly" ? "Year" : (viewSelect.value === "seasonal" ? "Season" : "Detail");
      detailLabel.textContent = label;
      setOptions(detailSelect, details);
      detailSelect.disabled = viewSelect.value === "6year";
    }}

    function updateColumnOptions() {{
      const block = getSelectedBlock();
      const options = [{{ value: "__all__", label: "All Columns" }}].concat(
        block.columns.map((column) => ({{ value: column, label: column }}))
      );
      setOptions(columnSelect, options);
    }}

    function buildHeatmapData(block, selectedColumn) {{
      if (selectedColumn === "__all__") {{
        return {{
          x: block.columns,
          y: block.columns,
          z: block.z,
          titleSuffix: "Full Matrix",
        }};
      }}

      const idx = block.columns.indexOf(selectedColumn);
      const columnValues = block.z.map((row) => row[idx]);
      return {{
        x: block.columns,
        y: [selectedColumn],
        z: [columnValues],
        titleSuffix: `${{selectedColumn}} vs All`,
      }};
    }}

    function renderPlot() {{
      const meter = meterSelect.value;
      const view = viewSelect.value;
      const detail = detailSelect.value;
      const selectedColumn = columnSelect.value;
      const block = getSelectedBlock();
      const heatmapData = buildHeatmapData(block, selectedColumn);
      const info = meterMeta[meter];

      metaText.textContent =
        `description=${{info.description}} | group=${{info.group}} | energy=${{info.energy_type}} | ` +
        `view=${{view}} | detail=${{detail}} | columns=${{block.columns.length}} | ` +
        `mode=${{selectedColumn === "__all__" ? "full" : "single-column"}}`;

      Plotly.react("plot", [{{
        type: "heatmap",
        x: heatmapData.x,
        y: heatmapData.y,
        z: heatmapData.z,
        zmin: -1,
        zmax: 1,
        colorscale: [
          [0.0, "rgb(103,0,31)"],
          [0.1, "rgb(178,24,43)"],
          [0.2, "rgb(214,96,77)"],
          [0.3, "rgb(244,165,130)"],
          [0.4, "rgb(253,219,199)"],
          [0.5, "rgb(247,247,247)"],
          [0.6, "rgb(209,229,240)"],
          [0.7, "rgb(146,197,222)"],
          [0.8, "rgb(67,147,195)"],
          [0.9, "rgb(33,102,172)"],
          [1.0, "rgb(5,48,97)"]
        ],
        reversescale: true,
        hovertemplate: "x=%{{x}}<br>y=%{{y}}<br>corr=%{{z:.4f}}<extra></extra>",
        text: heatmapData.z,
        texttemplate: "%{{text:.2f}}",
        textfont: {{ size: selectedColumn === "__all__" ? 10 : 12 }},
        colorbar: {{ title: {{ text: "corr" }} }},
      }}], {{
        title: {{ text: `${{meterMeta[meter].label}} | ${{viewLabels[view]}} | ${{detail}} | ${{heatmapData.titleSuffix}}` }},
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "#ffffff",
        font: {{ family: "Segoe UI, sans-serif", color: "#182235" }},
        xaxis: {{
          tickangle: 45,
          showgrid: false,
        }},
        yaxis: {{
          autorange: "reversed",
          showgrid: false,
        }},
        margin: {{ l: 100, r: 40, t: 80, b: 140 }},
        width: 1320,
        height: selectedColumn === "__all__" ? 920 : 360,
      }}, {{
        responsive: true,
        displaylogo: false,
      }});
    }}

    meterSelect.addEventListener("change", () => {{
      updateDetailOptions();
      updateColumnOptions();
      renderPlot();
    }});

    viewSelect.addEventListener("change", () => {{
      updateDetailOptions();
      updateColumnOptions();
      renderPlot();
    }});

    detailSelect.addEventListener("change", () => {{
      updateColumnOptions();
      renderPlot();
    }});

    columnSelect.addEventListener("change", renderPlot);

    initMeterOptions();
    updateDetailOptions();
    updateColumnOptions();
    renderPlot();
  </script>
</body>
</html>
"""


def main() -> None:
    payload = collect_payload()
    meter_meta = build_meter_meta(payload)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(build_html(payload, meter_meta), encoding="utf-8")
    print(f"saved: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
