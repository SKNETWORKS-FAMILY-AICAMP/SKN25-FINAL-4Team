from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.tsa.seasonal import STL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.meter_metadata import get_metadata
from scripts.eda.stl.eda_stl_electric import FEATURE_UNITS, build_input_series
from scripts.pipeline.preprocess import fetch_joined_data

OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "raw_eda" / "stl" / "plotly"
DASHBOARD_ROOT = OUTPUT_ROOT / "homoscedasticity"
HTML_PATH = OUTPUT_ROOT / "stl_homoscedasticity_dashboard.html"
MANIFEST_PATH = OUTPUT_ROOT / "stl_homoscedasticity_manifest.json"
PERIOD = 24 * 7
SEASONAL = 13
SIGMA = 3
P_VALUE_THRESHOLD = 0.05
YEARS = [2018, 2019, 2020, 2021, 2022, 2023]

CONSUMPTION_REPS = [
    "H1.Z13", "H1.Z21", "H1.Z24", "H1.Z12", "H2.Z66", "H4.Z51", "H2.Z70", "H2.Z351",
    "H2.Z61", "H2.Z36", "H2.Z62", "H2.Z64", "H3.Z43", "H3.Z44", "H3.Z48", "H4.Z50",
    "H1.Z10", "H1.Z16", "H1.Z18", "H1.Z19", "H1.Z23", "H1.Z26", "H1.Z27", "H2.Z65",
    "H2.Z68", "H2.Z69", "H2.ZE65", "H2.ZE74", "H3.Z40", "H3.Z41", "H3.Z42", "H3.Z45",
    "H3.Z46", "H3.Z47", "H3.Z71", "H2.T.Z31", "H2.T.Z32", "H2.Z351", "H2.Z361",
    "H4.Z50", "H4.ZE50", "H4.Z51", "H4.ZE51",
]
PRODUCTION_REPS = ["V.Z84", "H1.Z20"]
THERMAL_METERS = ["V.K21", "H1.K11", "H1.K12", "H1.K14", "H1.K15", "H1.K16", "H2.K21", "H1.W11", "H1.W12"]


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def format_float(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def write_payload_js(output_path: Path, payload: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        f"window.HOMOSCEDASTICITY_PAYLOAD = {json.dumps(payload, ensure_ascii=False)};\n",
        encoding="utf-8",
    )


def relative_payload_path(base_dir: Path, target_path: Path) -> str:
    return target_path.relative_to(base_dir).as_posix()


def get_target_specs() -> list[tuple[str, str, list[str]]]:
    specs: list[tuple[str, str, list[str]]] = []
    for meter_urn in unique_preserve_order(CONSUMPTION_REPS):
        specs.append((meter_urn, "electric_consumption", ["P", "PF", "U1"]))
    for meter_urn in unique_preserve_order(PRODUCTION_REPS):
        specs.append((meter_urn, "electric_production", ["P", "PF", "U1"]))
    for meter_urn in unique_preserve_order(THERMAL_METERS):
        specs.append((meter_urn, "thermal", ["P", "qv", "Tdiff"]))
    return specs


def load_raw_meter_data(meter_urn: str) -> pd.DataFrame:
    df = fetch_joined_data(meter_urn).copy()
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    return df


def compute_payload(meter_urn: str, group_name: str, feature: str) -> dict[str, Any]:
    raw_df = load_raw_meter_data(meter_urn)
    if feature not in raw_df.columns:
        raise ValueError(f"{meter_urn}: feature '{feature}' column missing")

    series = build_input_series(raw_df, feature)
    series = pd.to_numeric(series, errors="coerce").copy()
    raw_null_ratio = float(series.isna().mean() * 100)
    series = series.interpolate(method="linear", limit=24)
    stl_input = series.dropna()
    if len(stl_input) < PERIOD:
        raise ValueError(f"{meter_urn}-{feature}: not enough points for STL ({len(stl_input)})")

    result = STL(stl_input, period=PERIOD, seasonal=SEASONAL).fit()
    residual = pd.to_numeric(result.resid, errors="coerce")
    mean = float(residual.mean())
    std = float(residual.std())
    upper = mean + SIGMA * std
    lower = mean - SIGMA * std
    anomaly_mask = (residual > upper) | (residual < lower)

    detail = pd.DataFrame(
        {
            "ts": pd.to_datetime(residual.index, utc=True, errors="coerce"),
            "observed": pd.to_numeric(result.observed, errors="coerce").values,
            "trend": pd.to_numeric(result.trend, errors="coerce").values,
            "seasonal": pd.to_numeric(result.seasonal, errors="coerce").values,
            "residual": residual.values,
            "is_anomaly": anomaly_mask.values,
        }
    ).dropna(subset=["ts", "residual"])
    detail = detail.sort_values("ts").reset_index(drop=True)
    if len(detail) < 10:
        raise ValueError(f"{meter_urn}-{feature}: too few residual points ({len(detail)})")

    exog = sm.add_constant(np.arange(len(detail), dtype=float))
    lm_stat, lm_pvalue, f_stat, f_pvalue = het_breuschpagan(detail["residual"].values, exog)

    annual_std = detail.groupby(detail["ts"].dt.year)["residual"].std().reindex(YEARS)
    valid_std = annual_std.dropna()
    positive_std = valid_std[valid_std > 0]
    variance_ratio = None
    if not positive_std.empty:
        variance_ratio = float(valid_std.max() / positive_std.min())

    focus_years: list[int] = []
    if not valid_std.empty:
        max_std = float(valid_std.max())
        focus_years = [
            int(year)
            for year, value in valid_std.items()
            if pd.notna(value) and float(value) >= max_std * 0.9
        ]

    metadata = get_metadata(meter_urn) or {}
    anomaly_points = detail.loc[detail["is_anomaly"]]

    return {
        "meter_urn": meter_urn,
        "group": group_name,
        "feature": feature,
        "description": metadata.get("description", ""),
        "unit": FEATURE_UNITS.get(feature),
        "ts": [ts.isoformat() for ts in detail["ts"]],
        "observed": [format_float(v) for v in detail["observed"].tolist()],
        "trend": [format_float(v) for v in detail["trend"].tolist()],
        "seasonal": [format_float(v) for v in detail["seasonal"].tolist()],
        "residual": [format_float(v) for v in detail["residual"].tolist()],
        "anomaly_ts": [ts.isoformat() for ts in anomaly_points["ts"]],
        "anomaly_values": [format_float(v) for v in anomaly_points["residual"].tolist()],
        "annual_std": {str(year): format_float(annual_std.get(year), 6) for year in YEARS},
        "focus_years": focus_years,
        "bp": {
            "p_value": format_float(lm_pvalue, 8),
            "lm_stat": format_float(lm_stat, 6),
            "f_stat": format_float(f_stat, 6),
            "f_pvalue": format_float(f_pvalue, 8),
            "status": "PASS" if lm_pvalue >= P_VALUE_THRESHOLD else "FAIL",
            "threshold": P_VALUE_THRESHOLD,
        },
        "summary": {
            "n_total": int(len(detail)),
            "n_anomaly": int(detail["is_anomaly"].sum()),
            "anomaly_ratio": format_float(float(detail["is_anomaly"].mean() * 100), 4),
            "raw_null_ratio": format_float(raw_null_ratio, 4),
            "mean": format_float(mean, 6),
            "std": format_float(std, 6),
            "upper": format_float(upper, 6),
            "lower": format_float(lower, 6),
            "variance_ratio": format_float(variance_ratio, 6),
            "start_ts": detail["ts"].min().isoformat() if not detail.empty else None,
            "end_ts": detail["ts"].max().isoformat() if not detail.empty else None,
        },
    }


def collect_manifest() -> dict[str, Any]:
    specs = get_target_specs()
    manifest_entries: list[dict[str, Any]] = []
    failure_rows: list[tuple[str, str, str]] = []
    success_count = 0

    for meter_urn, group_name, features in specs:
        metadata = get_metadata(meter_urn) or {}
        meter_dir = DASHBOARD_ROOT / meter_urn
        available_features: list[dict[str, Any]] = []

        for feature in features:
            try:
                payload = compute_payload(meter_urn, group_name, feature)
            except Exception as exc:
                failure_rows.append((meter_urn, feature, str(exc)))
                print(f"{meter_urn} - {feature} 실패: {exc}", flush=True)
                continue

            feature_path = meter_dir / f"{feature}.js"
            write_payload_js(feature_path, payload)
            available_features.append(
                {
                    "feature": feature,
                    "unit": FEATURE_UNITS.get(feature),
                    "path": relative_payload_path(OUTPUT_ROOT, feature_path),
                }
            )
            success_count += 1
            print(f"{meter_urn} - {feature} 저장: {feature_path}", flush=True)

        if available_features:
            manifest_entries.append(
                {
                    "meter_urn": meter_urn,
                    "group": group_name,
                    "description": metadata.get("description", ""),
                    "label": f"{meter_urn} - {metadata.get('description', meter_urn)}",
                    "features": available_features,
                }
            )

    print(f"payload success count: {success_count}", flush=True)
    print(f"meter entries count: {len(manifest_entries)}", flush=True)
    print(f"failure count: {len(failure_rows)}", flush=True)
    if failure_rows:
        print("sample failures:", flush=True)
        for meter_urn, feature, message in failure_rows[:20]:
            print(f"  - {meter_urn} / {feature}: {message}", flush=True)

    return {
        "title": "STL Residual Homoscedasticity Dashboard",
        "period": PERIOD,
        "seasonal": SEASONAL,
        "sigma": SIGMA,
        "years": YEARS,
        "entries": manifest_entries,
    }


def build_html(manifest: dict[str, Any]) -> str:
    manifest_json = json.dumps(manifest, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>STL Residual Homoscedasticity Dashboard</title>
  <script src="https://cdn.plot.ly/plotly-3.1.0.min.js"></script>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #f7f2ea 0%, #efe3d3 100%);
      color: #1f1a16;
    }}
    .wrap {{
      max-width: 1600px;
      margin: 0 auto;
      padding: 24px;
    }}
    .panel {{
      background: rgba(255, 253, 250, 0.96);
      border: 1px solid #e1d5c3;
      border-radius: 18px;
      box-shadow: 0 16px 44px rgba(72, 53, 26, 0.08);
      padding: 22px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .subtitle {{ margin: 0 0 18px; color: #6d5b43; line-height: 1.5; }}
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
    .meta {{ margin-bottom: 10px; color: #6e614e; font-size: 13px; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(180px, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }}
    .stat-card {{
      background: #fffdf9;
      border: 1px solid #eadcc8;
      border-radius: 14px;
      padding: 12px 14px;
    }}
    .stat-label {{
      font-size: 12px;
      color: #7e6b55;
      margin-bottom: 4px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .stat-value {{ font-size: 22px; font-weight: 700; color: #2d2114; }}
    #plot {{ width: 100%; height: 1280px; }}
    @media (max-width: 1100px) {{
      .controls, .stats {{ grid-template-columns: 1fr; }}
      #plot {{ height: 980px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1>STL Residual Homoscedasticity Dashboard</h1>
      <p class="subtitle">
        DB raw 기준으로 STL(period={manifest["period"]}, seasonal={manifest["seasonal"]})을 재실행한 뒤,
        잔차 시계열과 연도별 std, Breusch-Pagan 결과를 함께 확인합니다.
      </p>
      <div class="controls">
        <div><label for="meter-select">Meter</label><select id="meter-select"></select></div>
        <div><label for="feature-select">Column</label><select id="feature-select"></select></div>
        <div>
          <label for="scale-select">Residual Scale</label>
          <select id="scale-select">
            <option value="linear">Linear</option>
            <option value="logabs">Log(abs residual)</option>
          </select>
        </div>
      </div>
      <div class="meta" id="meta-text"></div>
      <div class="stats">
        <div class="stat-card"><div class="stat-label">BP p-value</div><div class="stat-value" id="bp-pvalue">-</div></div>
        <div class="stat-card"><div class="stat-label">판정</div><div class="stat-value" id="bp-status">-</div></div>
        <div class="stat-card"><div class="stat-label">분산비율</div><div class="stat-value" id="var-ratio">-</div></div>
        <div class="stat-card"><div class="stat-label">이분산 집중 연도</div><div class="stat-value" id="focus-years">-</div></div>
      </div>
      <div id="plot"></div>
    </div>
  </div>
  <script>
    const manifest = {manifest_json};
    const meterSelect = document.getElementById("meter-select");
    const featureSelect = document.getElementById("feature-select");
    const scaleSelect = document.getElementById("scale-select");
    const metaText = document.getElementById("meta-text");
    const bpPvalue = document.getElementById("bp-pvalue");
    const bpStatus = document.getElementById("bp-status");
    const varRatio = document.getElementById("var-ratio");
    const focusYears = document.getElementById("focus-years");
    let currentPayload = null;
    let activeScript = null;

    function setOptions(selectEl, items, valueKey = "value", labelKey = "label") {{
      selectEl.innerHTML = "";
      items.forEach((item) => {{
        const option = document.createElement("option");
        option.value = item[valueKey];
        option.textContent = item[labelKey];
        selectEl.appendChild(option);
      }});
    }}

    function updateMeterOptions() {{
      const items = manifest.entries.map((entry) => ({{ value: entry.meter_urn, label: entry.label }}));
      setOptions(meterSelect, items);
    }}

    function getSelectedEntry() {{
      return manifest.entries.find((entry) => entry.meter_urn === meterSelect.value);
    }}

    function updateFeatureOptions() {{
      const entry = getSelectedEntry();
      if (!entry) {{
        featureSelect.innerHTML = "";
        return;
      }}
      const items = entry.features.map((item) => ({{
        value: item.feature,
        label: item.unit ? `${{item.feature}} [${{item.unit}}]` : item.feature,
      }}));
      setOptions(featureSelect, items);
    }}

    function loadPayload(path) {{
      return new Promise((resolve, reject) => {{
        if (activeScript) activeScript.remove();
        delete window.HOMOSCEDASTICITY_PAYLOAD;
        const script = document.createElement("script");
        script.src = path;
        script.onload = () => {{ activeScript = script; resolve(window.HOMOSCEDASTICITY_PAYLOAD); }};
        script.onerror = () => reject(new Error(`Failed to load ${{path}}`));
        document.body.appendChild(script);
      }});
    }}

    function buildResidualSeries(payload) {{
      if (scaleSelect.value === "linear") return payload.residual;
      return payload.residual.map((value) => {{
        if (value === null || value === undefined) return null;
        const absValue = Math.abs(value);
        return absValue > 0 ? Math.log10(absValue) : null;
      }});
    }}

    function updateStatCards(payload) {{
      const bp = payload.bp || {{}};
      const summary = payload.summary || {{}};
      bpPvalue.textContent = bp.p_value ?? "-";
      bpStatus.textContent = bp.status ?? "-";
      bpStatus.style.color = bp.status === "FAIL" ? "#b23b2a" : "#2d7a46";
      varRatio.textContent = summary.variance_ratio ? `${{summary.variance_ratio.toFixed(2)}}배` : "-";
      focusYears.textContent = (payload.focus_years || []).length ? payload.focus_years.join(", ") : "-";
    }}

    function buildPlot(payload) {{
      const residualSeries = buildResidualSeries(payload);
      const summary = payload.summary || {{}};
      const annualStd = manifest.years.map((year) => payload.annual_std?.[String(year)] ?? null);
      const anomalyValues = scaleSelect.value === "linear"
        ? payload.anomaly_values
        : payload.anomaly_values.map((value) => {{
            if (value === null || value === undefined) return null;
            const absValue = Math.abs(value);
            return absValue > 0 ? Math.log10(absValue) : null;
          }});

      const traces = [
        {{ type: "scatter", mode: "lines", x: payload.ts, y: payload.observed, line: {{ color: "#3b82f6", width: 1 }}, xaxis: "x", yaxis: "y" }},
        {{ type: "scatter", mode: "lines", x: payload.ts, y: payload.trend, line: {{ color: "#f59e0b", width: 1 }}, xaxis: "x2", yaxis: "y2" }},
        {{ type: "scatter", mode: "lines", x: payload.ts, y: payload.seasonal, line: {{ color: "#16a34a", width: 1 }}, xaxis: "x3", yaxis: "y3" }},
        {{ type: "scatter", mode: "lines", x: payload.ts, y: residualSeries, line: {{ color: "#6b7280", width: 1 }}, xaxis: "x4", yaxis: "y4" }},
        {{ type: "scatter", mode: "markers", x: payload.anomaly_ts, y: anomalyValues, marker: {{ color: "#dc2626", size: 5 }}, xaxis: "x4", yaxis: "y4" }},
        {{ type: "bar", x: manifest.years.map(String), y: annualStd, marker: {{ color: annualStd.map((value, idx) => (payload.focus_years || []).includes(manifest.years[idx]) ? "#b45309" : "#c08457") }}, xaxis: "x5", yaxis: "y5" }},
      ];

      const layout = {{
        template: "plotly_white",
        width: 1500,
        height: 1280,
        margin: {{ l: 70, r: 30, t: 70, b: 40 }},
        title: `${{payload.meter_urn}} | ${{payload.feature}} | BP p-value=${{payload.bp?.p_value}} | status=${{payload.bp?.status}}`,
        showlegend: false,
        grid: {{ rows: 5, columns: 1, pattern: "independent", roworder: "top to bottom" }},
        xaxis: {{ anchor: "y" }},
        yaxis: {{ title: "Observed" }},
        xaxis2: {{ anchor: "y2", matches: "x" }},
        yaxis2: {{ title: "Trend" }},
        xaxis3: {{ anchor: "y3", matches: "x" }},
        yaxis3: {{ title: "Seasonal" }},
        xaxis4: {{ anchor: "y4", matches: "x" }},
        yaxis4: {{ title: scaleSelect.value === "linear" ? "Residual" : "log10(|Residual|)" }},
        xaxis5: {{ title: "Year", anchor: "y5" }},
        yaxis5: {{ title: "Residual std" }},
        shapes: scaleSelect.value === "linear" ? [
          {{ type: "line", xref: "x4", yref: "y4", x0: payload.ts[0], x1: payload.ts[payload.ts.length - 1], y0: summary.upper, y1: summary.upper, line: {{ color: "#dc2626", dash: "dash", width: 1 }} }},
          {{ type: "line", xref: "x4", yref: "y4", x0: payload.ts[0], x1: payload.ts[payload.ts.length - 1], y0: summary.lower, y1: summary.lower, line: {{ color: "#dc2626", dash: "dash", width: 1 }} }},
        ] : [],
      }};

      Plotly.react("plot", traces, layout, {{ responsive: true }});
      metaText.textContent =
        `group=${{payload.group}} | meter=${{payload.meter_urn}} | feature=${{payload.feature}} | unit=${{payload.unit || "-"}} | ` +
        `n_total=${{summary.n_total}} | anomaly=${{summary.n_anomaly}} (${{summary.anomaly_ratio}}%) | raw_null=${{summary.raw_null_ratio}}% | ` +
        `range=${{summary.start_ts}} ~ ${{summary.end_ts}}`;
      updateStatCards(payload);
    }}

    async function render() {{
      const entry = getSelectedEntry();
      if (!entry) return;
      const featureMeta = entry.features.find((item) => item.feature === featureSelect.value);
      if (!featureMeta) return;
      currentPayload = await loadPayload(featureMeta.path);
      buildPlot(currentPayload);
    }}

    meterSelect.addEventListener("change", async () => {{ updateFeatureOptions(); await render(); }});
    featureSelect.addEventListener("change", render);
    scaleSelect.addEventListener("change", () => {{ if (currentPayload) buildPlot(currentPayload); }});

    updateMeterOptions();
    updateFeatureOptions();
    render();
  </script>
</body>
</html>
"""


def main() -> None:
    manifest = collect_manifest()
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    print(f"saved manifest: {MANIFEST_PATH}", flush=True)
    html = build_html(manifest)
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"saved html: {HTML_PATH}", flush=True)


if __name__ == "__main__":
    main()
