from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from statsmodels.tsa.seasonal import STL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.meter_metadata import get_metadata
from scripts.eda_stl_electric import FEATURE_UNITS, build_input_series, load_raw_meter_data
from scripts.preprocess_h1z16 import get_numeric_columns

OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "raw_eda" / "stl" / "plotly"
ELECTRIC_DIR = OUTPUT_ROOT / "electric"
HTML_PATH = OUTPUT_ROOT / "electric_stl_dashboard.html"
PERIOD = 24 * 7
SEASONAL = 13
SIGMA = 3

ELECTRIC_METERS = [
    "H1.Z10",
    "H1.Z13",
    "H1.Z16",
    "H1.Z20",
    "H2.T.Z33",
    "H2.Z35",
    "H2.Z64",
    "H2.Z68",
    "H2.ZE64",
    "H4.Z50",
    "V.Z84",
]


def format_float(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), 6)


def stl_payload_for_series(meter_urn: str, feature: str, series: pd.Series) -> dict[str, Any] | None:
    numeric = pd.to_numeric(series, errors="coerce").copy()
    null_ratio = float(numeric.isnull().mean() * 100)
    numeric = numeric.interpolate(method="linear", limit=24)
    stl_input = numeric.dropna()

    if stl_input.empty or len(stl_input) < PERIOD:
        return None

    result = STL(stl_input, period=PERIOD, seasonal=SEASONAL).fit()
    residual = result.resid
    if residual.empty:
        return None

    mean = float(residual.mean())
    std = float(residual.std())
    upper = mean + SIGMA * std
    lower = mean - SIGMA * std
    anomaly_mask = (residual > upper) | (residual < lower)

    return {
        "meter_urn": meter_urn,
        "feature": feature,
        "unit": FEATURE_UNITS.get(feature),
        "ts": [ts.isoformat() for ts in residual.index],
        "observed": [format_float(v) for v in result.observed.tolist()],
        "trend": [format_float(v) for v in result.trend.tolist()],
        "seasonal": [format_float(v) for v in result.seasonal.tolist()],
        "residual": [format_float(v) for v in residual.tolist()],
        "anomaly_ts": [ts.isoformat() for ts in residual[anomaly_mask].index],
        "anomaly_values": [format_float(v) for v in residual[anomaly_mask].tolist()],
        "mean": format_float(mean),
        "upper": format_float(upper),
        "lower": format_float(lower),
        "n_total": int(len(residual)),
        "n_anomaly": int(anomaly_mask.sum()),
        "ratio": round(float(anomaly_mask.mean() * 100), 4),
        "null_ratio": round(null_ratio, 4),
    }


def write_payload_js(output_path: Path, payload: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script = f"window.STL_PAYLOAD = {json.dumps(payload, ensure_ascii=False)};\n"
    output_path.write_text(script, encoding="utf-8")


def relative_payload_path(base_dir: Path, target_path: Path) -> str:
    return target_path.relative_to(base_dir).as_posix()


def collect_meter_payload(meter_urn: str, output_dir: Path) -> dict[str, Any]:
    df = load_raw_meter_data(meter_urn)
    metadata = get_metadata(meter_urn) or {}
    available_features: list[dict[str, str | None]] = []

    for feature in get_numeric_columns(meter_urn):
        if feature not in df.columns:
            continue
        series = build_input_series(df, feature)
        if series.dropna().empty:
            continue
        try:
            payload = stl_payload_for_series(meter_urn, feature, series)
        except Exception as exc:
            print(f"{meter_urn} - {feature} STL 실패: {exc}")
            continue
        if payload is None:
            continue

        feature_path = output_dir / meter_urn / f"{feature}.js"
        write_payload_js(feature_path, payload)
        available_features.append(
            {
                "feature": feature,
                "unit": FEATURE_UNITS.get(feature),
                "path": relative_payload_path(OUTPUT_ROOT, feature_path),
            }
        )
        print(f"{meter_urn} - {feature} 저장: {feature_path}")

    return {
        "label": f"{meter_urn} - {metadata.get('description', meter_urn)}",
        "description": metadata.get("description", ""),
        "group": metadata.get("group_name", ""),
        "energy_type": metadata.get("energy_type", ""),
        "features": available_features,
    }


def build_html(manifest: dict[str, Any], title: str, heading: str, accent: str, body_bg: str, panel_bg: str) -> str:
    manifest_json = json.dumps(manifest, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <script src="https://cdn.plot.ly/plotly-3.1.0.min.js"></script>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", sans-serif;
      background: {body_bg};
      color: #182235;
    }}
    .wrap {{
      max-width: 1480px;
      margin: 0 auto;
      padding: 24px;
    }}
    .panel {{
      background: {panel_bg};
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
      grid-template-columns: repeat(2, minmax(220px, 280px));
      gap: 12px;
      margin-bottom: 14px;
    }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 6px;
      color: {accent};
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
      height: 1120px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1>{heading}</h1>
      <p>계량기와 컬럼을 선택하면 4행 STL 분해 그래프가 같은 x축 범위로 함께 확대/이동됩니다.</p>
      <div class="controls">
        <div>
          <label for="meter-select">Meter</label>
          <select id="meter-select"></select>
        </div>
        <div>
          <label for="feature-select">Column</label>
          <select id="feature-select"></select>
        </div>
      </div>
      <div class="meta" id="meta-text"></div>
      <div id="plot"></div>
    </div>
  </div>
  <script>
    const manifest = {manifest_json};
    const meterSelect = document.getElementById("meter-select");
    const featureSelect = document.getElementById("feature-select");
    const metaText = document.getElementById("meta-text");
    let activeScript = null;

    function setOptions(selectEl, options) {{
      selectEl.innerHTML = "";
      options.forEach((option) => {{
        const el = document.createElement("option");
        el.value = option.value;
        el.textContent = option.label;
        selectEl.appendChild(el);
      }});
    }}

    function initMeterOptions() {{
      const options = Object.entries(manifest).map(([meter, info]) => {{
        return {{ value: meter, label: info.label }};
      }});
      setOptions(meterSelect, options);
    }}

    function updateFeatureOptions() {{
      const meterInfo = manifest[meterSelect.value];
      const options = meterInfo.features.map((item) => {{
        const label = item.unit ? `${{item.feature}} [${{item.unit}}]` : item.feature;
        return {{ value: item.feature, label }};
      }});
      setOptions(featureSelect, options);
    }}

    function loadFeaturePayload(path) {{
      return new Promise((resolve, reject) => {{
        if (activeScript) {{
          activeScript.remove();
          activeScript = null;
        }}
        delete window.STL_PAYLOAD;
        const script = document.createElement("script");
        script.src = path + "?t=" + Date.now();
        script.onload = () => resolve(window.STL_PAYLOAD);
        script.onerror = () => reject(new Error("payload load failed"));
        activeScript = script;
        document.body.appendChild(script);
      }});
    }}

    function buildTitle(payload) {{
      const unit = payload.unit ? ` [${{payload.unit}}]` : "";
      return `${{payload.meter_urn}} - ${{payload.feature}} STL 분해 (±3σ)${{unit}}`;
    }}

    async function renderPlot() {{
      const meterInfo = manifest[meterSelect.value];
      const featureInfo = meterInfo.features.find((item) => item.feature === featureSelect.value);
      if (!featureInfo) {{
        return;
      }}

      const payload = await loadFeaturePayload(featureInfo.path);
      metaText.textContent =
        `description=${{meterInfo.description || "-"}} | group=${{meterInfo.group || "-"}} | ` +
        `energy=${{meterInfo.energy_type || "-"}} | total=${{payload.n_total}} | ` +
        `anomaly=${{payload.n_anomaly}} (${{payload.ratio.toFixed(2)}}%) | null_ratio=${{payload.null_ratio.toFixed(2)}}%`;

      const commonLine = {{
        mode: "lines",
        type: "scattergl",
        line: {{ width: 1 }},
        hovertemplate: "%{{x}}<br>%{{y}}<extra></extra>",
      }};

      const traces = [
        {{
          ...commonLine,
          x: payload.ts,
          y: payload.observed,
          name: "Observed",
          line: {{ color: "steelblue", width: 1 }},
          xaxis: "x",
          yaxis: "y",
        }},
        {{
          ...commonLine,
          x: payload.ts,
          y: payload.trend,
          name: "Trend",
          line: {{ color: "orange", width: 1 }},
          xaxis: "x2",
          yaxis: "y2",
        }},
        {{
          ...commonLine,
          x: payload.ts,
          y: payload.seasonal,
          name: "Seasonal",
          line: {{ color: "green", width: 1 }},
          xaxis: "x3",
          yaxis: "y3",
        }},
        {{
          ...commonLine,
          x: payload.ts,
          y: payload.residual,
          name: "Residual",
          line: {{ color: "gray", width: 1 }},
          xaxis: "x4",
          yaxis: "y4",
        }},
        {{
          type: "scattergl",
          mode: "markers",
          x: payload.anomaly_ts,
          y: payload.anomaly_values,
          name: "Residual anomaly",
          marker: {{ color: "red", size: 5 }},
          xaxis: "x4",
          yaxis: "y4",
          hovertemplate: "%{{x}}<br>anomaly=%{{y}}<extra></extra>",
        }},
      ];

      const annotations = [
        {{ text: "Observed", x: 0, xref: "paper", y: 1.0, yref: "paper", xanchor: "left", yanchor: "bottom", showarrow: false, font: {{ size: 12, color: "#182235" }} }},
        {{ text: "Trend", x: 0, xref: "paper", y: 0.74, yref: "paper", xanchor: "left", yanchor: "bottom", showarrow: false, font: {{ size: 12, color: "#182235" }} }},
        {{ text: "Seasonal", x: 0, xref: "paper", y: 0.48, yref: "paper", xanchor: "left", yanchor: "bottom", showarrow: false, font: {{ size: 12, color: "#182235" }} }},
        {{ text: "Residual", x: 0, xref: "paper", y: 0.22, yref: "paper", xanchor: "left", yanchor: "bottom", showarrow: false, font: {{ size: 12, color: "#182235" }} }},
      ];

      const upperShapes = [0, 1, 2, 3].map((idx) => null).filter(Boolean);
      upperShapes.push(
        {{
          type: "line",
          xref: "x4",
          yref: "y4",
          x0: payload.ts[0],
          x1: payload.ts[payload.ts.length - 1],
          y0: payload.upper,
          y1: payload.upper,
          line: {{ color: "red", width: 1, dash: "dash" }},
        }},
        {{
          type: "line",
          xref: "x4",
          yref: "y4",
          x0: payload.ts[0],
          x1: payload.ts[payload.ts.length - 1],
          y0: payload.lower,
          y1: payload.lower,
          line: {{ color: "red", width: 1, dash: "dash" }},
        }},
        {{
          type: "line",
          xref: "x4",
          yref: "y4",
          x0: payload.ts[0],
          x1: payload.ts[payload.ts.length - 1],
          y0: payload.mean,
          y1: payload.mean,
          line: {{ color: "black", width: 1, dash: "dot" }},
        }}
      );

      const layout = {{
        title: {{ text: buildTitle(payload) }},
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "#ffffff",
        font: {{ family: "Segoe UI, sans-serif", color: "#182235" }},
        hovermode: "x unified",
        showlegend: false,
        margin: {{ l: 80, r: 30, t: 80, b: 60 }},
        height: 1120,
        annotations,
        shapes: upperShapes,
        xaxis: {{ domain: [0, 1], anchor: "y", matches: "x4", showticklabels: false }},
        xaxis2: {{ domain: [0, 1], anchor: "y2", matches: "x4", showticklabels: false }},
        xaxis3: {{ domain: [0, 1], anchor: "y3", matches: "x4", showticklabels: false }},
        xaxis4: {{ domain: [0, 1], anchor: "y4", rangeslider: {{ visible: true }}, title: {{ text: "Time" }} }},
        yaxis: {{ domain: [0.78, 1.0], title: {{ text: "Observed" }} }},
        yaxis2: {{ domain: [0.52, 0.74], title: {{ text: "Trend" }} }},
        yaxis3: {{ domain: [0.26, 0.48], title: {{ text: "Seasonal" }} }},
        yaxis4: {{ domain: [0.0, 0.22], title: {{ text: "Residual" }} }},
      }};

      Plotly.react("plot", traces, layout, {{ responsive: true, displaylogo: false }});
    }}

    meterSelect.addEventListener("change", async () => {{
      updateFeatureOptions();
      await renderPlot();
    }});

    featureSelect.addEventListener("change", renderPlot);

    async function init() {{
      initMeterOptions();
      updateFeatureOptions();
      await renderPlot();
    }}

    init();
  </script>
</body>
</html>
"""


def generate_dashboard(
    meters: list[str],
    title: str,
    heading: str,
    accent: str,
    body_bg: str,
    panel_bg: str,
    output_dir: Path,
    html_path: Path,
    energy_key: str = "energy_type",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    for meter_urn in meters:
        meter_manifest = collect_meter_payload(meter_urn, output_dir)
        if meter_manifest["features"]:
            manifest[meter_urn] = meter_manifest

    html = build_html(manifest, title, heading, accent, body_bg, panel_bg)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")
    print(f"saved: {html_path}")


def main() -> None:
    generate_dashboard(
        meters=ELECTRIC_METERS,
        title="Electric STL Dashboard",
        heading="Electric STL Plotly Dashboard",
        accent="#35507e",
        body_bg="linear-gradient(180deg, #f3f7fc 0%, #e8eef8 100%)",
        panel_bg="rgba(255, 255, 255, 0.94)",
        output_dir=ELECTRIC_DIR,
        html_path=HTML_PATH,
    )


if __name__ == "__main__":
    main()
