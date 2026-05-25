from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "raw_eda" / "correlation" / "plotly"
OUTPUT_HTML = OUTPUT_DIR / "thermal_correlation_dashboard.html"
LONG_CSV_PATHS = {
    "6year": PROJECT_ROOT / "outputs" / "raw_eda" / "correlation" / "static" / "csv" / "thermal" / "6year" / "long.csv",
    "yearly": PROJECT_ROOT / "outputs" / "raw_eda" / "correlation" / "static" / "csv" / "thermal" / "yearly" / "long.csv",
    "seasonal": PROJECT_ROOT / "outputs" / "raw_eda" / "correlation" / "static" / "csv" / "thermal" / "seasonal" / "long.csv",
}
VIEW_LABELS = {
    "6year": "6-Year",
    "yearly": "Yearly",
    "seasonal": "Seasonal",
}
METADATA_PATH = PROJECT_ROOT / "config" / "meter_metadata.json"


def load_long_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def load_metadata() -> dict[str, dict]:
    return json.loads(METADATA_PATH.read_text(encoding="utf-8"))


def build_matrix_payload(df: pd.DataFrame) -> dict[str, dict[str, dict[str, object]]]:
    payload: dict[str, dict[str, dict[str, object]]] = {}

    grouped = df.groupby(["meter_urn", "period_type", "period_label"], sort=True)
    for (meter_urn, period_type, period_label), group_df in grouped:
        columns = sorted(set(group_df["column_a"].astype(str)).union(group_df["column_b"].astype(str)))
        matrix = pd.DataFrame(1.0, index=columns, columns=columns, dtype=float)
        for row in group_df.itertuples(index=False):
            corr = float(row.corr)
            matrix.loc[row.column_a, row.column_b] = corr
            matrix.loc[row.column_b, row.column_a] = corr

        payload.setdefault(str(meter_urn), {}).setdefault(str(period_type), {})[str(period_label)] = {
            "columns": columns,
            "z": [[round(float(matrix.loc[row, col]), 6) for col in columns] for row in columns],
        }
    return payload


def collect_payload() -> dict[str, dict[str, dict[str, object]]]:
    frames = []
    for period_type, path in LONG_CSV_PATHS.items():
        df = load_long_csv(path)
        df["period_type"] = period_type
        frames.append(df)
    merged = pd.concat(frames, ignore_index=True)
    return build_matrix_payload(merged)


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
            "thermal_mode": info.get("thermal_mode") or "-",
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
  <title>Thermal Correlation Dashboard</title>
  <script src="https://cdn.plot.ly/plotly-3.1.0.min.js"></script>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #f8f4ed 0%, #efe3d1 100%);
      color: #20170d;
    }}
    .wrap {{
      max-width: 1480px;
      margin: 0 auto;
      padding: 24px;
    }}
    .panel {{
      background: rgba(255, 252, 247, 0.95);
      border: 1px solid #e6d7bf;
      border-radius: 20px;
      box-shadow: 0 18px 50px rgba(81, 54, 19, 0.08);
      padding: 20px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 28px;
    }}
    p {{
      margin: 0 0 18px;
      color: #6b573c;
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
      color: #7a5b2a;
      letter-spacing: 0.02em;
    }}
    select {{
      width: 100%;
      padding: 10px 12px;
      border-radius: 10px;
      border: 1px solid #d8c39c;
      background: #fff;
      color: #20170d;
      font-size: 14px;
    }}
    .meta {{
      margin-bottom: 8px;
      font-size: 13px;
      color: #7a6440;
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
      <h1>Thermal Correlation Dashboard</h1>
      <p>열 계량기 raw correlation 산출물을 period/column 선택형 Heatmap으로 통합했습니다.</p>
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
        `description=${{info.description}} | group=${{info.group}} | thermal_mode=${{info.thermal_mode}} | ` +
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
        font: {{ family: "Segoe UI, sans-serif", color: "#20170d" }},
        xaxis: {{
          tickangle: 0,
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
