from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eda_raw_correlation_representative_electric import (
    REPRESENTATIVE_METERS,
    add_period_columns,
    fetch_meter_raw_df,
)
from scripts.fetch_h1z16_with_weather import build_engine


OUTPUT_DIR = PROJECT_ROOT / "outputs" / "raw_eda" / "plotly_eda" / "electric"
OUTPUT_HTML = OUTPUT_DIR / "electric_boxplot_dashboard.html"
TARGET_COL = "P"
MONTH_ORDER = list(range(1, 13))
MONTH_LABELS = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Aug",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}
SEASON_ORDER = ["Winter", "Spring", "Summer", "Autumn"]


def build_period_payload(df: pd.DataFrame) -> dict[str, object]:
    years = sorted(int(year) for year in df["year"].dropna().unique())

    six_year_labels: list[str] = []
    six_year_values: list[list[float]] = []
    for year in years:
        year_values = df.loc[df["year"] == year, TARGET_COL].dropna().tolist()
        if not year_values:
            continue
        six_year_labels.append(str(year))
        six_year_values.append(year_values)

    yearly_payload: dict[str, dict[str, list[object]]] = {}
    for year in years:
        year_df = df.loc[df["year"] == year].copy()
        labels: list[str] = []
        values: list[list[float]] = []
        for month in MONTH_ORDER:
            month_values = year_df.loc[
                year_df["ts"].dt.month == month, TARGET_COL
            ].dropna().tolist()
            if not month_values:
                continue
            labels.append(MONTH_LABELS[month])
            values.append(month_values)
        if labels:
            yearly_payload[str(year)] = {"labels": labels, "values": values}

    seasonal_payload: dict[str, dict[str, list[object]]] = {}
    for season in SEASON_ORDER:
        season_df = df.loc[df["season"] == season].copy()
        labels = []
        values = []
        for year in years:
            year_values = season_df.loc[
                season_df["year"] == year, TARGET_COL
            ].dropna().tolist()
            if not year_values:
                continue
            labels.append(str(year))
            values.append(year_values)
        if labels:
            seasonal_payload[season] = {"labels": labels, "values": values}

    return {
        "6year": {
            "labels": six_year_labels,
            "values": six_year_values,
            "title_suffix": "6-Year Distribution by Year",
        },
        "yearly": yearly_payload,
        "seasonal": seasonal_payload,
    }


def collect_dashboard_data() -> dict[str, dict[str, object]]:
    engine = build_engine()
    dashboard_data: dict[str, dict[str, object]] = {}

    for meter_urn in REPRESENTATIVE_METERS:
        df, _ = fetch_meter_raw_df(engine, meter_urn)
        if TARGET_COL not in df.columns:
            print(f"{meter_urn} skip: '{TARGET_COL}' column missing")
            continue

        working = df[["ts", TARGET_COL]].copy()
        working["ts"] = pd.to_datetime(working["ts"], utc=True, errors="coerce")
        working[TARGET_COL] = pd.to_numeric(working[TARGET_COL], errors="coerce")
        working = working.dropna(subset=["ts", TARGET_COL]).sort_values("ts")
        if working.empty:
            print(f"{meter_urn} skip: usable '{TARGET_COL}' data 없음")
            continue

        working = add_period_columns(working)
        dashboard_data[meter_urn] = build_period_payload(working)
        print(f"{meter_urn} electric plotly boxplot 데이터 준비 완료")

    return dashboard_data


def build_html(dashboard_data: dict[str, dict[str, object]]) -> str:
    payload = json.dumps(dashboard_data, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Electric Raw EDA Boxplot</title>
  <script src="https://cdn.plot.ly/plotly-3.1.0.min.js"></script>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #f4f7fb 0%, #e6edf7 100%);
      color: #162033;
    }}
    .wrap {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 24px;
    }}
    .panel {{
      background: rgba(255, 255, 255, 0.92);
      border: 1px solid #d4dcec;
      border-radius: 18px;
      box-shadow: 0 18px 50px rgba(28, 45, 94, 0.08);
      padding: 20px;
    }}
    h1 {{
      margin: 0 0 16px;
      font-size: 28px;
    }}
    p {{
      margin: 0 0 18px;
      color: #4f607d;
    }}
    .controls {{
      display: grid;
      grid-template-columns: repeat(3, minmax(180px, 240px));
      gap: 12px;
      margin-bottom: 18px;
    }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 6px;
      color: #36507e;
      letter-spacing: 0.02em;
    }}
    select {{
      width: 100%;
      padding: 10px 12px;
      border-radius: 10px;
      border: 1px solid #bfd0ea;
      background: #fff;
      color: #162033;
      font-size: 14px;
    }}
    .meta {{
      margin-bottom: 8px;
      font-size: 13px;
      color: #5d6f8b;
    }}
    #plot {{
      width: 100%;
      height: 760px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1>Electric Raw EDA Boxplot</h1>
      <p>대표 전기 계량기별 `P` 분포를 6년 / 연도별 / 계절별로 전환해서 확인합니다.</p>
      <div class="controls">
        <div>
          <label for="meter-select">Meter</label>
          <select id="meter-select"></select>
        </div>
        <div>
          <label for="view-select">View</label>
          <select id="view-select">
            <option value="6year">6-Year</option>
            <option value="yearly">Yearly</option>
            <option value="seasonal">Seasonal</option>
          </select>
        </div>
        <div>
          <label for="sub-select" id="sub-select-label">Detail</label>
          <select id="sub-select"></select>
        </div>
      </div>
      <div class="meta" id="meta-text"></div>
      <div id="plot"></div>
    </div>
  </div>
  <script>
    const dashboardData = {payload};
    const meterSelect = document.getElementById("meter-select");
    const viewSelect = document.getElementById("view-select");
    const subSelect = document.getElementById("sub-select");
    const subLabel = document.getElementById("sub-select-label");
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

    function initMeters() {{
      const meters = Object.keys(dashboardData);
      setOptions(meterSelect, meters);
    }}

    function updateSubSelect() {{
      const meter = meterSelect.value;
      const view = viewSelect.value;
      const meterData = dashboardData[meter];

      if (view === "6year") {{
        setOptions(subSelect, [{{ value: "all", label: "All Years" }}]);
        subSelect.disabled = true;
        subLabel.textContent = "Detail";
        return;
      }}

      subSelect.disabled = false;
      if (view === "yearly") {{
        subLabel.textContent = "Year";
        const years = Object.keys(meterData.yearly);
        setOptions(subSelect, years);
      }} else {{
        subLabel.textContent = "Season";
        const seasons = Object.keys(meterData.seasonal);
        setOptions(subSelect, seasons);
      }}
    }}

    function buildTraces(labels, values) {{
      return labels.map((label, idx) => ({{
        type: "box",
        name: label,
        y: values[idx],
        boxpoints: "suspectedoutliers",
        marker: {{ color: "#2f6fed", outliercolor: "#d64545" }},
        line: {{ color: "#2f6fed" }},
        fillcolor: "rgba(47, 111, 237, 0.35)",
        jitter: 0.25,
        pointpos: 0,
        hovertemplate: `${{label}}<br>value=%{{y:.2f}}<extra></extra>`,
      }}));
    }}

    function renderPlot() {{
      const meter = meterSelect.value;
      const view = viewSelect.value;
      const detail = subSelect.value;
      const meterData = dashboardData[meter];

      let payload;
      let title;
      if (view === "6year") {{
        payload = meterData["6year"];
        title = `${{meter}} P 6-Year Distribution by Year`;
      }} else if (view === "yearly") {{
        payload = meterData.yearly[detail];
        title = `${{meter}} P Monthly Distribution in ${{detail}}`;
      }} else {{
        payload = meterData.seasonal[detail];
        title = `${{meter}} P ${{detail}} Distribution by Year`;
      }}

      const traces = buildTraces(payload.labels, payload.values);
      const totalPoints = payload.values.reduce((acc, row) => acc + row.length, 0);
      metaText.textContent = `meter=${{meter}} | view=${{view}} | detail=${{detail}} | traces=${{payload.labels.length}} | rows=${{totalPoints}}`;

      Plotly.react("plot", traces, {{
        title: {{ text: title }},
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "#ffffff",
        font: {{ family: "Segoe UI, sans-serif", color: "#162033" }},
        xaxis: {{
          title: view === "yearly" ? "Month" : "Category",
          showgrid: false,
        }},
        yaxis: {{
          title: "P",
          zeroline: true,
          gridcolor: "#e4ebf5",
        }},
        margin: {{ l: 70, r: 30, t: 60, b: 60 }},
        showlegend: false,
      }}, {{
        responsive: true,
        displaylogo: false,
      }});
    }}

    meterSelect.addEventListener("change", () => {{
      updateSubSelect();
      renderPlot();
    }});

    viewSelect.addEventListener("change", () => {{
      updateSubSelect();
      renderPlot();
    }});

    subSelect.addEventListener("change", renderPlot);

    initMeters();
    updateSubSelect();
    renderPlot();
  </script>
</body>
</html>
"""


def main() -> None:
    dashboard_data = collect_dashboard_data()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(build_html(dashboard_data), encoding="utf-8")
    print(f"saved: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
