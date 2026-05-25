from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.meter_metadata import get_metadata, load_metadata
from scripts.pipeline.fetch_h1z16_with_weather import (
    build_engine,
    fetch_meter_data,
    fetch_weather_data,
    validate_columns,
)
from scripts.pipeline.preprocess import get_numeric_columns

OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "raw_eda" / "sliding_window" / "plotly"
ELECTRIC_DIR = OUTPUT_ROOT / "electric"
HTML_PATH = OUTPUT_ROOT / "electric_sliding_dashboard.html"
YEAR_OPTIONS = [2018, 2019, 2020, 2021, 2022, 2023]
MONTH_OPTIONS = list(range(1, 13))
MONTH_LABELS = [f"{month:02d}" for month in MONTH_OPTIONS]
WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HOUR_LABELS = [f"{hour:02d}" for hour in range(24)]
HOUR_TICK_LABELS = [f"{hour:02d}" for hour in range(0, 24, 2)]
YEAR_COLORS = {
    2018: "#1f77b4",
    2019: "#ff7f0e",
    2020: "#2ca02c",
    2021: "#d62728",
    2022: "#9467bd",
    2023: "#8c564b",
}
WEEK_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
WEEKDAY_COLORS = {
    "Mon": "#1f77b4",
    "Tue": "#ff7f0e",
    "Wed": "#2ca02c",
    "Thu": "#d62728",
    "Fri": "#9467bd",
    "Sat": "#8c564b",
    "Sun": "#e377c2",
}
FEATURE_UNITS = {
    "P": "W",
    "W": "Wh",
    "PF": "ratio",
    "PF1": "ratio",
    "PF2": "ratio",
    "PF3": "ratio",
    "P1": "W",
    "P2": "W",
    "P3": "W",
    "I1": "A",
    "I2": "A",
    "I3": "A",
    "U1": "V",
    "U2": "V",
    "U3": "V",
    "Q": "var",
    "f": "Hz",
    "W_in": "Wh",
    "W_out": "Wh",
    "Ta": "degC",
    "Igm": "W/m2",
}


def format_float(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), 6)


def write_payload_js(output_path: Path, payload: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script = f"window.SLIDING_PAYLOAD = {json.dumps(payload, ensure_ascii=False)};\n"
    output_path.write_text(script, encoding="utf-8")


def relative_payload_path(base_dir: Path, target_path: Path) -> str:
    return target_path.relative_to(base_dir).as_posix()


def get_electric_meters() -> list[str]:
    metadata = load_metadata()
    return sorted([meter for meter, info in metadata.items() if info.get("meter_type") == "electric"])


def load_raw_joined_meter_data(engine, weather_df: pd.DataFrame, meter_urn: str) -> pd.DataFrame:
    df_main = fetch_meter_data(engine, meter_urn).copy()
    validate_columns(df_main, meter_urn)
    df_main["ts"] = pd.to_datetime(df_main["ts"], utc=True, errors="coerce")

    merged = df_main.merge(weather_df, on="ts", how="left").sort_values("ts").reset_index(drop=True)
    for column in get_numeric_columns(meter_urn):
        if column in merged.columns:
            merged[column] = pd.to_numeric(merged[column], errors="coerce")
    return merged


def prepare_feature_frame(df: pd.DataFrame, feature: str) -> pd.DataFrame:
    working = df[["ts", feature]].copy()
    working["ts"] = pd.to_datetime(working["ts"], utc=True, errors="coerce")
    working[feature] = pd.to_numeric(working[feature], errors="coerce")
    working = working.dropna(subset=["ts"]).sort_values("ts").drop_duplicates(subset=["ts"])
    working["year"] = working["ts"].dt.year
    working = working.loc[working["year"].isin(YEAR_OPTIONS)].copy()
    return working


def build_yearly_payload(working: pd.DataFrame, feature: str) -> dict[str, Any]:
    monthly = (
        working.assign(month=working["ts"].dt.month)
        .groupby(["year", "month"])[feature]
        .agg(mean="mean", median="median")
        .reset_index()
    )
    lookup = {
        (int(row.year), int(row.month)): {
            "mean": format_float(row.mean),
            "median": format_float(row.median),
        }
        for row in monthly.itertuples(index=False)
    }

    years_payload: dict[str, Any] = {}
    for year in YEAR_OPTIONS:
        years_payload[str(year)] = {
            "x": MONTH_LABELS,
            "mean": [lookup.get((year, month), {}).get("mean") for month in MONTH_OPTIONS],
            "median": [lookup.get((year, month), {}).get("median") for month in MONTH_OPTIONS],
        }

    return {"years": years_payload}


def build_weekly_payload(working: pd.DataFrame, feature: str) -> dict[str, Any]:
    daily = (
        working.assign(date=working["ts"].dt.floor("D"))
        .groupby(["year", "date"])[feature]
        .agg(mean="mean", median="median")
        .reset_index()
    )
    if daily.empty:
        return {"years": {}, "months_by_year": {}}

    daily["month"] = pd.to_datetime(daily["date"]).dt.month
    daily["day"] = pd.to_datetime(daily["date"]).dt.day
    daily["weekday_idx"] = pd.to_datetime(daily["date"]).dt.weekday
    daily["week_in_month"] = ((daily["day"] - 1) // 7) + 1

    result: dict[str, dict[str, Any]] = {}
    months_by_year: dict[str, list[int]] = {}

    for (year, month), month_df in daily.groupby(["year", "month"], sort=True):
        year_key = str(int(year))
        month_key = f"{int(month):02d}"
        months_by_year.setdefault(year_key, [])
        if int(month) not in months_by_year[year_key]:
            months_by_year[year_key].append(int(month))

        weeks_payload: dict[str, Any] = {}
        for week_no, week_df in month_df.groupby("week_in_month", sort=True):
            mean_vals = [None] * 7
            median_vals = [None] * 7
            for row in week_df.itertuples(index=False):
                idx = int(row.weekday_idx)
                mean_vals[idx] = format_float(row.mean)
                median_vals[idx] = format_float(row.median)
            weeks_payload[f"W{int(week_no)}"] = {
                "x": WEEKDAY_LABELS,
                "mean": mean_vals,
                "median": median_vals,
            }

        result.setdefault(year_key, {})[month_key] = {
            "weeks": weeks_payload,
            "week_labels": list(weeks_payload.keys()),
        }

    for year_key in months_by_year:
        months_by_year[year_key] = sorted(months_by_year[year_key])

    return {"years": result, "months_by_year": months_by_year}


def build_monthly_payload(working: pd.DataFrame, feature: str) -> dict[str, Any]:
    daily = (
        working.assign(date=working["ts"].dt.floor("D"))
        .groupby(["year", "date"])[feature]
        .agg(mean="mean", median="median")
        .reset_index()
    )
    if daily.empty:
        return {"months": {}, "available_months": []}

    daily["month"] = pd.to_datetime(daily["date"]).dt.month
    daily["weekday_idx"] = pd.to_datetime(daily["date"]).dt.weekday

    grouped = (
        daily.groupby(["month", "year", "weekday_idx"])[["mean", "median"]]
        .agg({"mean": "mean", "median": "median"})
        .reset_index()
    )

    result: dict[str, Any] = {}
    available_months: set[int] = set()

    for (month, year), month_df in grouped.groupby(["month", "year"], sort=True):
        month_key = f"{int(month):02d}"
        year_key = str(int(year))
        available_months.add(int(month))

        mean_vals = [None] * 7
        median_vals = [None] * 7
        for row in month_df.itertuples(index=False):
            idx = int(row.weekday_idx)
            mean_vals[idx] = format_float(row.mean)
            median_vals[idx] = format_float(row.median)

        result.setdefault(month_key, {})[year_key] = {
            "x": WEEKDAY_LABELS,
            "mean": mean_vals,
            "median": median_vals,
        }

    return {"months": result, "available_months": sorted(available_months)}


def build_daily_payload(working: pd.DataFrame, feature: str) -> dict[str, Any]:
    hourly = (
        working.assign(month=working["ts"].dt.month, weekday_idx=working["ts"].dt.weekday, hour=working["ts"].dt.hour)
        .groupby(["year", "month", "weekday_idx", "hour"])[feature]
        .agg(mean="mean", median="median")
        .reset_index()
    )
    if hourly.empty:
        return {"years": {}, "months_by_year": {}}

    result: dict[str, dict[str, Any]] = {}
    months_by_year: dict[str, list[int]] = {}

    for (year, month), month_df in hourly.groupby(["year", "month"], sort=True):
        year_key = str(int(year))
        month_key = f"{int(month):02d}"
        months_by_year.setdefault(year_key, [])
        if int(month) not in months_by_year[year_key]:
            months_by_year[year_key].append(int(month))

        weekday_payload: dict[str, Any] = {}
        for weekday_idx, weekday_df in month_df.groupby("weekday_idx", sort=True):
            mean_vals = [None] * 24
            median_vals = [None] * 24
            for row in weekday_df.itertuples(index=False):
                idx = int(row.hour)
                mean_vals[idx] = format_float(row.mean)
                median_vals[idx] = format_float(row.median)
            weekday_label = WEEKDAY_LABELS[int(weekday_idx)]
            weekday_payload[weekday_label] = {
                "x": HOUR_LABELS,
                "mean": mean_vals,
                "median": median_vals,
            }

        result.setdefault(year_key, {})[month_key] = {
            "weekdays": weekday_payload,
            "weekday_labels": [label for label in WEEKDAY_LABELS if label in weekday_payload],
        }

    for year_key in months_by_year:
        months_by_year[year_key] = sorted(months_by_year[year_key])

    return {"years": result, "months_by_year": months_by_year}


def build_drill_daily_payload(working: pd.DataFrame, feature: str) -> dict[str, Any]:
    hourly = (
        working.assign(
            month=working["ts"].dt.month,
            day=working["ts"].dt.day,
            weekday_idx=working["ts"].dt.weekday,
            hour=working["ts"].dt.hour,
        )
        .groupby(["year", "month", "day", "weekday_idx", "hour"])[feature]
        .agg(mean="mean", median="median")
        .reset_index()
    )
    if hourly.empty:
        return {"years": {}, "months_by_year": {}}

    hourly["week_in_month"] = ((hourly["day"] - 1) // 7) + 1

    result: dict[str, dict[str, Any]] = {}
    months_by_year: dict[str, list[int]] = {}

    for (year, month, weekday_idx), part_df in hourly.groupby(["year", "month", "weekday_idx"], sort=True):
        year_key = str(int(year))
        month_key = f"{int(month):02d}"
        weekday_label = WEEKDAY_LABELS[int(weekday_idx)]
        months_by_year.setdefault(year_key, [])
        if int(month) not in months_by_year[year_key]:
            months_by_year[year_key].append(int(month))

        weeks_payload: dict[str, Any] = {}
        for week_no, week_df in part_df.groupby("week_in_month", sort=True):
            mean_vals = [None] * 24
            median_vals = [None] * 24
            for row in week_df.itertuples(index=False):
                mean_vals[int(row.hour)] = format_float(row.mean)
                median_vals[int(row.hour)] = format_float(row.median)
            weeks_payload[f"W{int(week_no)}"] = {
                "x": HOUR_LABELS,
                "mean": mean_vals,
                "median": median_vals,
            }

        result.setdefault(year_key, {}).setdefault(month_key, {})[weekday_label] = {
            "weeks": weeks_payload,
            "week_labels": list(weeks_payload.keys()),
        }

    for year_key in months_by_year:
        months_by_year[year_key] = sorted(months_by_year[year_key])

    return {"years": result, "months_by_year": months_by_year}


def build_feature_payload(meter_urn: str, feature: str, df: pd.DataFrame) -> dict[str, Any] | None:
    if feature not in df.columns:
        return None

    working = prepare_feature_frame(df, feature)
    if working.empty or working[feature].dropna().empty:
        return None

    null_ratio = float(working[feature].isnull().mean() * 100)
    available_years = sorted(set(int(year) for year in working["year"].dropna().astype(int).tolist()))

    yearly = build_yearly_payload(working, feature)
    monthly = build_monthly_payload(working, feature)
    weekly = build_weekly_payload(working, feature)
    daily = build_daily_payload(working, feature)
    drill_daily = build_drill_daily_payload(working, feature)

    return {
        "meter_urn": meter_urn,
        "feature": feature,
        "unit": FEATURE_UNITS.get(feature),
        "available_years": available_years,
        "null_ratio": round(null_ratio, 4),
        "yearly": yearly,
        "monthly": monthly,
        "weekly": weekly,
        "daily": daily,
        "drill_daily": drill_daily,
    }


def collect_meter_payload(engine, weather_df: pd.DataFrame, meter_urn: str, output_dir: Path) -> dict[str, Any] | None:
    try:
        df = load_raw_joined_meter_data(engine, weather_df, meter_urn)
    except Exception as exc:
        print(f"{meter_urn} 데이터 로드 실패: {exc}")
        return None

    metadata = get_metadata(meter_urn) or {}
    available_features: list[dict[str, str]] = []

    for feature in get_numeric_columns(meter_urn):
        if feature not in df.columns or df[feature].isnull().all():
            continue

        payload = build_feature_payload(meter_urn, feature, df)
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

    if not available_features:
        return None

    return {
        "label": f"{meter_urn} - {metadata.get('description', meter_urn)}",
        "description": metadata.get("description", ""),
        "group": metadata.get("group_name", ""),
        "energy_type": metadata.get("energy_type", ""),
        "features": available_features,
    }


def build_html(manifest: dict[str, Any]) -> str:
    manifest_json = json.dumps(manifest, ensure_ascii=False)
    year_color_json = json.dumps(YEAR_COLORS, ensure_ascii=False)
    week_color_json = json.dumps(WEEK_COLORS, ensure_ascii=False)
    weekday_color_json = json.dumps(WEEKDAY_COLORS, ensure_ascii=False)
    year_options_json = json.dumps(YEAR_OPTIONS, ensure_ascii=False)
    month_options_json = json.dumps(MONTH_LABELS, ensure_ascii=False)
    weekday_labels_json = json.dumps(WEEKDAY_LABELS, ensure_ascii=False)
    hour_labels_json = json.dumps(HOUR_LABELS, ensure_ascii=False)
    hour_tick_labels_json = json.dumps(HOUR_TICK_LABELS, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Electric Sliding Window Dashboard</title>
  <script src="https://cdn.plot.ly/plotly-3.1.0.min.js"></script>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #f3f7fc 0%, #e8eef8 100%);
      color: #182235;
    }}
    .wrap {{
      max-width: 1540px;
      margin: 0 auto;
      padding: 24px;
    }}
    .panel {{
      background: rgba(255, 255, 255, 0.96);
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
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 14px;
      align-items: end;
    }}
    .aux-controls {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: end;
      margin-bottom: 10px;
    }}
    .aux-block {{
      min-width: 180px;
    }}
    .toggle-wrap {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 42px;
      padding: 0 4px;
      font-size: 14px;
      color: #27446f;
      font-weight: 600;
    }}
    .toggle-wrap input {{
      width: 16px;
      height: 16px;
    }}
    .stat-buttons {{
      display: flex;
      gap: 8px;
    }}
    button.stat-btn, button.trace-btn {{
      border: 1px solid #bfd0ea;
      background: #fff;
      color: #27446f;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
    }}
    button.stat-btn.active {{
      background: #27446f;
      color: #fff;
      border-color: #27446f;
    }}
    .trace-actions {{
      display: flex;
      gap: 8px;
      margin-bottom: 10px;
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
      height: 940px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1>Electric Sliding Window Plotly Dashboard</h1>
      <p>DB raw 기준 계량기/컬럼별 슬라이딩 윈도우 비교. 범례 클릭으로 trace on/off, 상단 버튼으로 전체 표시/숨김이 가능합니다.</p>
      <div class="controls">
        <div>
          <label for="meter-select">Meter</label>
          <select id="meter-select"></select>
        </div>
        <div>
          <label for="feature-select">Column</label>
          <select id="feature-select"></select>
        </div>
        <div>
          <label for="mode-select">Mode</label>
          <select id="mode-select">
            <option value="cross-year">Cross-year</option>
            <option value="drill-down">Drill-down</option>
          </select>
        </div>
        <div>
          <label for="view-select">View</label>
          <select id="view-select">
          </select>
        </div>
        <div>
          <label for="year-select">Year</label>
          <select id="year-select"></select>
        </div>
        <div>
          <label for="month-select">Month</label>
          <select id="month-select"></select>
        </div>
        <div>
          <label for="compare-select">Compare</label>
          <select id="compare-select">
            <option value="weekday-fixed">Weekday Fixed</option>
            <option value="week-fixed">Week Fixed</option>
            <option value="month-average">Month Average</option>
          </select>
        </div>
        <div>
          <label for="weekday-select">Weekday</label>
          <select id="weekday-select"></select>
        </div>
        <div>
          <label for="week-select">Week</label>
          <select id="week-select"></select>
        </div>
      </div>
      <div class="aux-controls">
        <div class="aux-block">
          <label>Statistic</label>
          <div class="stat-buttons">
            <button class="stat-btn active" id="median-btn" data-stat="median">Median</button>
            <button class="stat-btn" id="mean-btn" data-stat="mean">Mean</button>
          </div>
        </div>
        <div class="aux-block">
          <label>Scale</label>
          <label class="toggle-wrap" for="auto-scale-toggle">
            <input type="checkbox" id="auto-scale-toggle">
            <span>Auto Scale</span>
          </label>
        </div>
        <div class="trace-actions">
          <button class="trace-btn" id="show-all-btn">Show All</button>
          <button class="trace-btn" id="hide-all-btn">Hide All</button>
        </div>
      </div>
      <div class="meta" id="meta-text"></div>
      <div id="plot"></div>
    </div>
  </div>
  <script>
    const manifest = {manifest_json};
    const yearColors = {year_color_json};
    const weekColors = {week_color_json};
    const weekdayColors = {weekday_color_json};
    const fixedYears = {year_options_json};
    const fixedMonths = {month_options_json};
    const weekdayLabels = {weekday_labels_json};
    const hourLabels = {hour_labels_json};
    const hourTickLabels = {hour_tick_labels_json};

    const meterSelect = document.getElementById("meter-select");
    const featureSelect = document.getElementById("feature-select");
    const modeSelect = document.getElementById("mode-select");
    const viewSelect = document.getElementById("view-select");
    const yearSelect = document.getElementById("year-select");
    const monthSelect = document.getElementById("month-select");
    const compareSelect = document.getElementById("compare-select");
    const weekdaySelect = document.getElementById("weekday-select");
    const weekSelect = document.getElementById("week-select");
    const medianBtn = document.getElementById("median-btn");
    const meanBtn = document.getElementById("mean-btn");
    const autoScaleToggle = document.getElementById("auto-scale-toggle");
    const showAllBtn = document.getElementById("show-all-btn");
    const hideAllBtn = document.getElementById("hide-all-btn");
    const metaText = document.getElementById("meta-text");

    let activeScript = null;
    let activePayload = null;
    let activeStat = "median";
    let autoScaleEnabled = false;

    const viewOptionsByMode = {{
      "cross-year": [
        {{ value: "yearly", label: "연도별" }},
        {{ value: "monthly", label: "월별 요일 패턴" }},
        {{ value: "daily", label: "일별" }},
      ],
      "drill-down": [
        {{ value: "yearly", label: "연도별" }},
        {{ value: "weekly", label: "주간" }},
        {{ value: "daily", label: "일별" }},
      ],
    }};

    function setOptions(selectEl, options, preferredValue = null) {{
      const prev = preferredValue ?? selectEl.value;
      selectEl.innerHTML = "";
      options.forEach((option) => {{
        const el = document.createElement("option");
        el.value = option.value;
        el.textContent = option.label;
        selectEl.appendChild(el);
      }});
      if (options.some((option) => option.value === prev)) {{
        selectEl.value = prev;
      }}
    }}

    function initMeterOptions() {{
      const options = Object.entries(manifest).map(([meter, info]) => {{
        return {{ value: meter, label: info.label }};
      }});
      setOptions(meterSelect, options);
    }}

    function initViewOptions() {{
      const options = viewOptionsByMode[modeSelect.value] || [];
      const prev = viewSelect.value;
      setOptions(viewSelect, options, prev);
    }}

    function updateFeatureOptions() {{
      const meterInfo = manifest[meterSelect.value];
      const options = meterInfo.features.map((item) => {{
        return {{ value: item.feature, label: item.feature }};
      }});
      setOptions(featureSelect, options);
    }}

    function getTitleWithUnit(baseTitle) {{
      const unit = activePayload?.unit ? ` [${{activePayload.unit}}]` : "";
      return `${{baseTitle}}${{unit}}`;
    }}

    function loadFeaturePayload(path) {{
      return new Promise((resolve, reject) => {{
        if (activeScript) {{
          activeScript.remove();
          activeScript = null;
        }}
        delete window.SLIDING_PAYLOAD;
        const script = document.createElement("script");
        script.src = path + "?t=" + Date.now();
        script.onload = () => resolve(window.SLIDING_PAYLOAD);
        script.onerror = () => reject(new Error("payload load failed"));
        activeScript = script;
        document.body.appendChild(script);
      }});
    }}

    function getSelectedFeatureInfo() {{
      const meterInfo = manifest[meterSelect.value];
      return meterInfo.features.find((item) => item.feature === featureSelect.value);
    }}

    function getMonthOptionsFromPayload(viewKey, yearValue) {{
      if (viewKey === "monthly") {{
        const months = activePayload?.monthly?.available_months || [];
        return months.map((month) => {{
          const label = String(month).padStart(2, "0");
          return {{ value: label, label }};
        }});
      }}
      const monthsByYear = activePayload?.[viewKey]?.months_by_year || {{}};
      const months = monthsByYear[String(yearValue)] || [];
      return months.map((month) => {{
        const label = String(month).padStart(2, "0");
        return {{ value: label, label }};
      }});
    }}

    function getWeekdayOptions() {{
      if (!activePayload) {{
        return weekdayLabels.map((weekday) => ({{ value: weekday, label: weekday }}));
      }}

      const mode = modeSelect.value;
      const view = viewSelect.value;

      if (mode === "cross-year" && view === "daily") {{
        const month = monthSelect.value;
        const labels = new Set();
        fixedYears.forEach((year) => {{
          const block = activePayload.daily.years?.[String(year)]?.[month];
          (block?.weekday_labels || []).forEach((weekday) => labels.add(weekday));
        }});
        return Array.from(labels).map((weekday) => ({{ value: weekday, label: weekday }}));
      }}

      if (mode === "drill-down" && view === "daily") {{
        const year = yearSelect.value;
        const month = monthSelect.value;
        const block = activePayload.drill_daily.years?.[year]?.[month] || {{}};
        return Object.keys(block).map((weekday) => ({{ value: weekday, label: weekday }}));
      }}

      return weekdayLabels.map((weekday) => ({{ value: weekday, label: weekday }}));
    }}

    function updateWeekdayOptions() {{
      const options = getWeekdayOptions();
      setOptions(weekdaySelect, options.length ? options : weekdayLabels.map((weekday) => ({{ value: weekday, label: weekday }})));
    }}

    function getDaysInMonth(yearValue, monthValue) {{
      const year = Number(yearValue);
      const month = Number(monthValue);
      return new Date(Date.UTC(year, month, 0)).getUTCDate();
    }}

    function getWeekRangeInfo(yearValue, monthValue, weekLabel) {{
      const weekNo = Number(String(weekLabel || "").replace("W", ""));
      if (!yearValue || !monthValue || !Number.isFinite(weekNo) || weekNo < 1) {{
        return null;
      }}

      const year = Number(yearValue);
      const month = Number(monthValue);
      const startDay = ((weekNo - 1) * 7) + 1;
      const daysInMonth = getDaysInMonth(year, month);
      if (startDay > daysInMonth) {{
        return null;
      }}

      const endDay = Math.min(startDay + 6, daysInMonth);
      const startDate = new Date(Date.UTC(year, month - 1, startDay));
      const endDate = new Date(Date.UTC(year, month - 1, endDay));
      const shortLabel = `${{weekLabel}} | ${{String(month).padStart(2, "0")}}/${{String(startDay).padStart(2, "0")}}-${{String(month).padStart(2, "0")}}/${{String(endDay).padStart(2, "0")}}`;
      const weekdayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
      const detailLabel =
        `${{String(month).padStart(2, "0")}}/${{String(startDay).padStart(2, "0")}}(${{weekdayNames[startDate.getUTCDay()]}})` +
        `~${{String(month).padStart(2, "0")}}/${{String(endDay).padStart(2, "0")}}(${{weekdayNames[endDate.getUTCDay()]}})`;

      return {{
        weekLabel,
        shortLabel,
        detailLabel,
        startDate,
        endDate,
      }};
    }}

    function formatMonthDay(dateObj) {{
      return `${{String(dateObj.getUTCMonth() + 1).padStart(2, "0")}}/${{String(dateObj.getUTCDate()).padStart(2, "0")}}`;
    }}

    function getWeekFixedLabel(yearValue, monthValue, weekLabel) {{
      const rangeInfo = getWeekRangeInfo(yearValue, monthValue, weekLabel);
      if (!rangeInfo) {{
        return weekLabel;
      }}
      return `${{weekLabel}} | ${{formatMonthDay(rangeInfo.startDate)}}(${{weekdayLabels[(rangeInfo.startDate.getUTCDay() + 6) % 7]}})-${{formatMonthDay(rangeInfo.endDate)}}(${{weekdayLabels[(rangeInfo.endDate.getUTCDay() + 6) % 7]}})`;
    }}

    function getWeekdayFixedLabel(yearValue, monthValue, weekLabel, weekday) {{
      const rangeInfo = getWeekRangeInfo(yearValue, monthValue, weekLabel);
      if (!rangeInfo || !weekday) {{
        return weekLabel;
      }}

      const weekdayIndex = weekdayLabels.indexOf(weekday);
      if (weekdayIndex < 0) {{
        return weekLabel;
      }}

      const candidate = new Date(rangeInfo.startDate);
      for (let offset = 0; offset < 7; offset += 1) {{
        candidate.setUTCDate(rangeInfo.startDate.getUTCDate() + offset);
        if (candidate.getUTCMonth() !== rangeInfo.startDate.getUTCMonth()) {{
          break;
        }}
        const candidateWeekday = (candidate.getUTCDay() + 6) % 7;
        if (candidateWeekday === weekdayIndex) {{
          return `${{weekLabel}} | ${{formatMonthDay(candidate)}}(${{weekday}})`;
        }}
      }}

      return weekLabel;
    }}

    function getWeekOptions() {{
      if (!activePayload || modeSelect.value !== "drill-down" || viewSelect.value !== "daily") {{
        return [];
      }}

      const year = yearSelect.value;
      const month = monthSelect.value;
      const monthBlock = activePayload.drill_daily.years?.[year]?.[month] || {{}};
      const labels = new Set();
      Object.values(monthBlock).forEach((weekdayBlock) => {{
        (weekdayBlock.week_labels || []).forEach((weekLabel) => labels.add(weekLabel));
      }});

      return Array.from(labels)
        .sort((left, right) => Number(left.slice(1)) - Number(right.slice(1)))
        .map((weekLabel) => {{
          return {{
            value: weekLabel,
            label: weekLabel,
          }};
        }});
    }}

    function updateWeekOptions() {{
      const options = getWeekOptions();
      setOptions(weekSelect, options);
    }}

    function updateTemporalControls() {{
      const mode = modeSelect.value;
      const view = viewSelect.value;
      const isDrillDaily = mode === "drill-down" && view === "daily";
      const isWeekFixed = compareSelect.value === "week-fixed";
      const isMonthAverage = compareSelect.value === "month-average";
      const needsYear =
        (mode === "drill-down" && (view === "yearly" || view === "weekly" || view === "daily"));
      const needsMonth =
        (mode === "cross-year" && (view === "monthly" || view === "daily")) ||
        (mode === "drill-down" && (view === "weekly" || view === "daily"));
      const needsCompare = isDrillDaily;
      const needsWeekday = view === "daily" && (!isDrillDaily || (!isWeekFixed && !isMonthAverage));
      const needsWeek = isDrillDaily && isWeekFixed;
      yearSelect.parentElement.style.display = needsYear ? "block" : "none";
      monthSelect.parentElement.style.display = needsMonth ? "block" : "none";
      compareSelect.parentElement.style.display = needsCompare ? "block" : "none";
      weekdaySelect.parentElement.style.display = needsWeekday ? "block" : "none";
      weekSelect.parentElement.style.display = needsWeek ? "block" : "none";

      if (!activePayload) {{
        return;
      }}

      if (needsYear) {{
        const sourceKey = view === "daily" && mode === "drill-down" ? "drill_daily" : view;
        const years = Object.keys(activePayload[sourceKey].years || {{}}).sort();
        setOptions(yearSelect, years.map((year) => ({{ value: year, label: year }})));
      }}
      updateMonthOptions();
      updateWeekdayOptions();
      updateWeekOptions();
    }}

    function updateMonthOptions() {{
      const mode = modeSelect.value;
      const view = viewSelect.value;
      if (!activePayload) {{
        return;
      }}
      if (mode === "drill-down" && view === "yearly") {{
        monthSelect.innerHTML = "";
        return;
      }}
      if (mode === "cross-year" && view === "yearly") {{
        monthSelect.innerHTML = "";
        return;
      }}
      const payloadViewKey = view === "daily" && mode === "drill-down" ? "drill_daily" : view;
      const options = getMonthOptionsFromPayload(payloadViewKey, yearSelect.value);
      setOptions(monthSelect, options.length ? options : fixedMonths.map((month) => ({{ value: month, label: month }})));
    }}

    function buildYearlyTraces() {{
      if (modeSelect.value === "drill-down") {{
        const year = yearSelect.value;
        const traces = [];
        fixedYears.forEach((traceYear) => {{
          const block = activePayload.yearly.years[String(traceYear)] || {{ x: fixedMonths, mean: new Array(12).fill(null), median: new Array(12).fill(null) }};
          const isSelected = String(traceYear) === String(year);
          traces.push({{
            type: "scatter",
            mode: "lines+markers",
            x: block.x,
            y: block[activeStat],
            name: String(traceYear),
            line: {{ color: yearColors[String(traceYear)] || "#1f77b4", width: isSelected ? 3 : 2 }},
            marker: {{ size: isSelected ? 8 : 7 }},
            hovertemplate: `year=${{traceYear}}<br>month=%{{x}}<br>${{activeStat}}=%{{y:.4f}}<extra></extra>`,
          }});
        }});
        return {{
          traces,
          title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | Drill-down 연도별 | ${{year}}`),
          meta: `years=2018~2023 compared | null_ratio=${{activePayload.null_ratio}}%`,
          xaxisTitle: "Month",
          yaxisTitle: `monthly ${{activeStat}}`,
        }};
      }}

      const traces = [];
      fixedYears.forEach((year) => {{
        const block = activePayload.yearly.years[String(year)] || {{ x: fixedMonths, mean: new Array(12).fill(null), median: new Array(12).fill(null) }};
        traces.push({{
          type: "scatter",
          mode: "lines+markers",
          x: block.x,
          y: block[activeStat],
          name: String(year),
          line: {{ color: yearColors[String(year)] || "#1f77b4", width: 2 }},
          marker: {{ size: 7 }},
          hovertemplate: `year=${{year}}<br>month=%{{x}}<br>${{activeStat}}=%{{y:.4f}}<extra></extra>`,
        }});
      }});
      return {{
        traces,
        title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | 연도별 월별 ${{activeStat}}`),
        meta: `years=2018~2023 fixed | null_ratio=${{activePayload.null_ratio}}%`,
        xaxisTitle: "Month",
        yaxisTitle: `monthly ${{activeStat}}`,
      }};
    }}

    function buildWeeklyTraces() {{
      const year = yearSelect.value;
      const month = monthSelect.value;
      const block = activePayload.weekly.years?.[year]?.[month];
      if (!block) {{
        return {{
          traces: [],
          title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | 주간 비교 (no data)`),
          meta: `no data`,
          xaxisTitle: "Weekday",
          yaxisTitle: `daily ${{activeStat}}`,
        }};
      }}

      const traces = block.week_labels.map((weekLabel, idx) => {{
        const weekBlock = block.weeks[weekLabel];
        return {{
          type: "scatter",
          mode: "lines+markers",
          x: weekBlock.x,
          y: weekBlock[activeStat],
          name: weekLabel,
          line: {{ color: weekColors[idx % weekColors.length], width: 2 }},
          marker: {{ size: 7 }},
          hovertemplate: `week=${{weekLabel}}<br>weekday=%{{x}}<br>${{activeStat}}=%{{y:.4f}}<extra></extra>`,
        }};
      }});

      return {{
        traces,
        title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | 주간 비교 | ${{year}}-${{month}}`),
        meta: `traces=${{block.week_labels.length}}`,
        xaxisTitle: "Weekday",
        yaxisTitle: `daily ${{activeStat}}`,
      }};
    }}

    function buildMonthlyTraces() {{
      const month = monthSelect.value;
      const block = activePayload.monthly.months?.[month];
      if (!block) {{
        return {{
          traces: [],
          title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | 월별 비교 (no data)`),
          meta: `no data`,
          xaxisTitle: "Weekday",
          yaxisTitle: `daily ${{activeStat}}`,
        }};
      }}

      const traces = fixedYears.map((year) => {{
        const yearKey = String(year);
        const yearBlock = block[yearKey] || {{ x: weekdayLabels, mean: new Array(7).fill(null), median: new Array(7).fill(null) }};
        return {{
          type: "scatter",
          mode: "lines+markers",
          x: yearBlock.x,
          y: yearBlock[activeStat],
          name: yearKey,
          line: {{ color: yearColors[yearKey] || "#1f77b4", width: 2 }},
          marker: {{ size: 7 }},
          hovertemplate: `year=${{yearKey}}<br>weekday=%{{x}}<br>${{activeStat}}=%{{y:.4f}}<extra></extra>`,
        }};
      }});

      return {{
        traces,
        title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | 월별 비교 | month=${{month}}`),
        meta: `years=2018~2023 fixed`,
        xaxisTitle: "Weekday",
        yaxisTitle: `daily ${{activeStat}}`,
      }};
    }}

    function buildDailyTraces() {{
      if (modeSelect.value === "cross-year") {{
        const month = monthSelect.value;
        const weekday = weekdaySelect.value;
        const traces = fixedYears.map((year) => {{
          const block = activePayload.daily.years?.[String(year)]?.[month];
          const weekdayBlock = block?.weekdays?.[weekday] || {{ x: hourLabels, mean: new Array(24).fill(null), median: new Array(24).fill(null) }};
          return {{
            type: "scatter",
            mode: "lines+markers",
            x: weekdayBlock.x,
            y: weekdayBlock[activeStat],
            name: String(year),
            line: {{ color: yearColors[String(year)] || "#1f77b4", width: 2 }},
            marker: {{ size: 6 }},
            hovertemplate: `year=${{year}}<br>weekday=${{weekday}}<br>hour=%{{x}}<br>${{activeStat}}=%{{y:.4f}}<extra></extra>`,
          }};
        }});

        return {{
          traces,
          title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | Cross-year 일별 | month=${{month}} | weekday=${{weekday}}`),
          meta: `years=2018~2023 fixed`,
          xaxisTitle: "Hour",
          yaxisTitle: `${{activeStat}} by hour`,
        }};
      }}

      const year = yearSelect.value;
      const month = monthSelect.value;
      const compareMode = compareSelect.value;

      if (compareMode === "week-fixed") {{
        const weekLabel = weekSelect.value;
        const rangeInfo = getWeekRangeInfo(year, month, weekLabel);
        const displayWeekLabel = getWeekFixedLabel(year, month, weekLabel);
        const monthBlock = activePayload.drill_daily.years?.[year]?.[month] || {{}};
        const traces = weekdayLabels.flatMap((weekday) => {{
          const weekdayBlock = monthBlock[weekday];
          const weekBlock = weekdayBlock?.weeks?.[weekLabel];
          if (!weekBlock) {{
            return [];
          }}
          return [{{
            type: "scatter",
            mode: "lines+markers",
            x: weekBlock.x,
            y: weekBlock[activeStat],
            name: weekday,
            line: {{ color: weekdayColors[weekday] || "#1f77b4", width: 2 }},
            marker: {{ size: 6 }},
            hovertemplate: `week=${{weekLabel}}<br>weekday=${{weekday}}<br>hour=%{{x}}<br>${{activeStat}}=%{{y:.4f}}<extra></extra>`,
          }}];
        }});

        if (!traces.length) {{
          return {{
            traces: [],
            title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | Drill-down 일별 비교 (no data)`),
            meta: `compare=week-fixed | range=${{rangeInfo?.detailLabel || "n/a"}} | no data`,
            xaxisTitle: "Hour",
            yaxisTitle: `${{activeStat}} by hour`,
          }};
        }}

        return {{
          traces,
          title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | Drill-down 일별 | ${{year}}-${{month}} | ${{weekLabel}}`),
          meta: `compare=week-fixed | range=${{rangeInfo?.detailLabel || "n/a"}} | traces=${{traces.length}}`,
          xaxisTitle: "Hour",
          yaxisTitle: `${{activeStat}} by hour`,
        }};
      }}

      if (compareMode === "month-average") {{
        const monthBlock = activePayload.daily.years?.[year]?.[month];
        if (!monthBlock) {{
          return {{
            traces: [],
            title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | Drill-down 일별 비교 (no data)`),
            meta: `compare=month-average | no data`,
            xaxisTitle: "Hour",
            yaxisTitle: `${{activeStat}} by hour`,
          }};
        }}

        const traces = weekdayLabels.flatMap((weekday) => {{
          const weekdayBlock = monthBlock.weekdays?.[weekday];
          if (!weekdayBlock) {{
            return [];
          }}
          return [{{
            type: "scatter",
            mode: "lines+markers",
            x: weekdayBlock.x,
            y: weekdayBlock[activeStat],
            name: weekday,
            line: {{ color: weekdayColors[weekday] || "#1f77b4", width: 2 }},
            marker: {{ size: 6 }},
            hovertemplate: `weekday=${{weekday}}<br>hour=%{{x}}<br>${{activeStat}}=%{{y:.4f}}<extra></extra>`,
          }}];
        }});

        return {{
          traces,
          title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | Drill-down 월평균 일별 | ${{year}}-${{month}}`),
          meta: `compare=month-average | traces=${{traces.length}}`,
          xaxisTitle: "Hour",
          yaxisTitle: `${{activeStat}} by hour`,
        }};
      }}

      const weekday = weekdaySelect.value;
      const block = activePayload.drill_daily.years?.[year]?.[month]?.[weekday];
      if (!block) {{
        return {{
          traces: [],
          title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | Drill-down 일별 비교 (no data)`),
          meta: `compare=weekday-fixed | no data`,
          xaxisTitle: "Hour",
          yaxisTitle: `${{activeStat}} by hour`,
        }};
      }}

      const traces = block.week_labels.map((weekLabel, idx) => {{
        const weekBlock = block.weeks[weekLabel];
        return {{
          type: "scatter",
          mode: "lines+markers",
          x: weekBlock.x,
          y: weekBlock[activeStat],
          name: getWeekdayFixedLabel(year, month, weekLabel, weekday),
          line: {{ color: weekColors[idx % weekColors.length], width: 2 }},
          marker: {{ size: 6 }},
          hovertemplate: `week=${{weekLabel}}<br>weekday=${{weekday}}<br>hour=%{{x}}<br>${{activeStat}}=%{{y:.4f}}<extra></extra>`,
        }};
      }});

      return {{
        traces,
        title: getTitleWithUnit(`${{activePayload.meter_urn}} - ${{activePayload.feature}} | Drill-down 일별 | ${{year}}-${{month}} | ${{weekday}}`),
        meta: `compare=weekday-fixed | traces=${{block.week_labels.length}}`,
        xaxisTitle: "Hour",
        yaxisTitle: `${{activeStat}} by hour`,
      }};
    }}

    function buildViewModel() {{
      const mode = modeSelect.value;
      const view = viewSelect.value;
      if (view === "yearly") {{
        return buildYearlyTraces();
      }}
      if (mode === "cross-year" && view === "monthly") {{
        return buildMonthlyTraces();
      }}
      if (mode === "drill-down" && view === "weekly") {{
        return buildWeeklyTraces();
      }}
      if (view === "daily") {{
        return buildDailyTraces();
      }}

      return {{
        traces: [],
        title: `${{activePayload.meter_urn}} - ${{activePayload.feature}} | unsupported view`,
        meta: `unsupported`,
        xaxisTitle: "",
        yaxisTitle: "",
      }};
    }}

    function renderPlot() {{
      if (!activePayload) {{
        return;
      }}

      const viewModel = buildViewModel();
      const meterInfo = manifest[meterSelect.value];
      const metaPrefix = `description=${{meterInfo.description}} | group=${{meterInfo.group}} | energy=${{meterInfo.energy_type}} | stat=${{activeStat}}`;
      metaText.textContent = `${{metaPrefix}} | ${{viewModel.meta}}`;

      const yRange = getFixedYRange(viewModel.traces);

      const layout = {{
        title: {{ text: viewModel.title }},
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "#ffffff",
        font: {{ family: "Segoe UI, sans-serif", color: "#182235" }},
        xaxis: {{
          title: {{ text: viewModel.xaxisTitle }},
          showgrid: true,
          gridcolor: "#e8eef8",
          tickmode: viewModel.xaxisTitle === "Hour" ? "array" : "auto",
          tickvals: viewModel.xaxisTitle === "Hour" ? hourTickLabels : undefined,
          ticktext: viewModel.xaxisTitle === "Hour" ? hourTickLabels : undefined,
        }},
        yaxis: {{
          title: {{ text: viewModel.yaxisTitle }},
          showgrid: true,
          gridcolor: "#edf2fa",
          autorange: autoScaleEnabled || !yRange,
          range: autoScaleEnabled ? undefined : yRange,
        }},
        legend: {{
          orientation: "h",
          yanchor: "bottom",
          y: 1.02,
          xanchor: "left",
          x: 0,
        }},
        margin: {{ l: 80, r: 30, t: 90, b: 70 }},
        height: 900,
        uirevision: `${{meterSelect.value}}-${{featureSelect.value}}-${{modeSelect.value}}-${{viewSelect.value}}`,
        annotations: viewModel.traces.length ? [] : [{{
          text: "선택한 조건에 해당하는 데이터가 없습니다.",
          showarrow: false,
          xref: "paper",
          yref: "paper",
          x: 0.5,
          y: 0.5,
          font: {{ size: 16, color: "#61728e" }},
        }}],
      }};

      Plotly.react("plot", viewModel.traces, layout, {{
        responsive: true,
        displaylogo: false,
      }});
    }}

    function getFixedYRange(traces) {{
      const values = traces
        .flatMap((trace) => Array.isArray(trace.y) ? trace.y : [])
        .filter((value) => typeof value === "number" && Number.isFinite(value));

      if (!values.length) {{
        return null;
      }}

      let minVal = Math.min(...values);
      let maxVal = Math.max(...values);

      if (minVal === maxVal) {{
        const pad = minVal === 0 ? 1 : Math.abs(minVal) * 0.1;
        return [minVal - pad, maxVal + pad];
      }}

      const span = maxVal - minVal;
      const pad = span * 0.05;
      minVal -= pad;
      maxVal += pad;

      if (minVal >= 0) {{
        minVal = Math.max(0, minVal);
      }}

      return [minVal, maxVal];
    }}

    async function loadAndRenderFeature() {{
      const featureInfo = getSelectedFeatureInfo();
      if (!featureInfo) {{
        return;
      }}
      activePayload = await loadFeaturePayload(featureInfo.path);
      updateTemporalControls();
      renderPlot();
    }}

    function setStat(stat) {{
      activeStat = stat;
      medianBtn.classList.toggle("active", stat === "median");
      meanBtn.classList.toggle("active", stat === "mean");
      renderPlot();
    }}

    meterSelect.addEventListener("change", async () => {{
      updateFeatureOptions();
      await loadAndRenderFeature();
    }});

    featureSelect.addEventListener("change", loadAndRenderFeature);
    modeSelect.addEventListener("change", () => {{
      initViewOptions();
      updateTemporalControls();
      renderPlot();
    }});
    viewSelect.addEventListener("change", () => {{
      updateTemporalControls();
      renderPlot();
    }});
    compareSelect.addEventListener("change", () => {{
      updateTemporalControls();
      renderPlot();
    }});
    yearSelect.addEventListener("change", () => {{
      updateMonthOptions();
      updateWeekdayOptions();
      updateWeekOptions();
      renderPlot();
    }});
    monthSelect.addEventListener("change", () => {{
      updateWeekdayOptions();
      updateWeekOptions();
      renderPlot();
    }});
    weekdaySelect.addEventListener("change", renderPlot);
    weekSelect.addEventListener("change", renderPlot);
    medianBtn.addEventListener("click", () => setStat("median"));
    meanBtn.addEventListener("click", () => setStat("mean"));
    autoScaleToggle.addEventListener("change", () => {{
      autoScaleEnabled = autoScaleToggle.checked;
      renderPlot();
    }});
    showAllBtn.addEventListener("click", () => Plotly.restyle("plot", {{ visible: true }}));
    hideAllBtn.addEventListener("click", () => Plotly.restyle("plot", {{ visible: "legendonly" }}));

    initMeterOptions();
    initViewOptions();
    updateFeatureOptions();
    loadAndRenderFeature();
  </script>
</body>
</html>
"""


def generate_dashboard() -> None:
    engine = build_engine()
    weather_df = fetch_weather_data(engine).copy()
    weather_df["ts"] = pd.to_datetime(weather_df["ts"], utc=True, errors="coerce")
    for column in ["Ta", "Igm"]:
        if column in weather_df.columns:
            weather_df[column] = pd.to_numeric(weather_df[column], errors="coerce")

    manifest: dict[str, Any] = {}
    for meter_urn in get_electric_meters():
        payload = collect_meter_payload(engine, weather_df, meter_urn, ELECTRIC_DIR)
        if payload is not None:
            manifest[meter_urn] = payload

    html = build_html(manifest)
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"saved: {HTML_PATH}")


def main() -> None:
    generate_dashboard()


if __name__ == "__main__":
    main()
