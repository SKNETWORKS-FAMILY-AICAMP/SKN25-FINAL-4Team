from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.meter_metadata import get_metadata
from scripts.eda.sliding.eda_plotly_sliding_dashboard_electric import (
    build_daily_payload,
    build_drill_daily_payload,
    build_html,
    build_monthly_payload,
    build_weekly_payload,
    build_yearly_payload,
    prepare_feature_frame,
    relative_payload_path,
    write_payload_js,
)
from scripts.pipeline.fetch_h1z16_with_weather import get_measurement_columns
from scripts.pipeline.fetch_h1z16_with_weather import (
    build_engine,
    fetch_meter_data,
    fetch_weather_data,
    validate_columns,
)


OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "raw_eda" / "sliding_window" / "plotly"
THERMAL_DIR = OUTPUT_ROOT / "thermal"
HTML_PATH = OUTPUT_ROOT / "thermal_sliding_dashboard.html"
THERMAL_FEATURE_UNITS = {
    "P": "W",
    "W": "Wh",
    "Tvl": "degC",
    "Trl": "degC",
    "Tdiff": "degC",
    "qv": "m3/h",
    "V": "m3",
    "Ta": "degC",
    "Igm": "W/m2",
}


def get_thermal_meters() -> list[str]:
    from config.meter_metadata import load_metadata

    metadata = load_metadata()
    return sorted([meter for meter, info in metadata.items() if info.get("meter_type") == "thermal"])


def load_raw_joined_heat_data(engine, weather_df: pd.DataFrame, meter_urn: str) -> pd.DataFrame:
    df_main = fetch_meter_data(engine, meter_urn).copy()
    validate_columns(df_main, meter_urn)
    df_main["ts"] = pd.to_datetime(df_main["ts"], utc=True, errors="coerce")

    merged = df_main.merge(weather_df, on="ts", how="left").sort_values("ts").reset_index(drop=True)
    for column in merged.columns:
        if column not in {"ts", "meter_urn"}:
            merged[column] = pd.to_numeric(merged[column], errors="coerce")
    return merged


def resolve_feature_columns(df: pd.DataFrame, meter_urn: str) -> list[str]:
    configured_features = [*get_measurement_columns(meter_urn), "Ta", "Igm"]
    return [feature for feature in configured_features if feature in df.columns and not df[feature].isnull().all()]


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
        "unit": THERMAL_FEATURE_UNITS.get(feature),
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
        df = load_raw_joined_heat_data(engine, weather_df, meter_urn)
    except Exception as exc:
        print(f"{meter_urn} 데이터 로드 실패: {exc}")
        return None

    metadata = get_metadata(meter_urn) or {}
    available_features: list[dict[str, str | None]] = []

    for feature in resolve_feature_columns(df, meter_urn):
        payload = build_feature_payload(meter_urn, feature, df)
        if payload is None:
            continue

        feature_path = output_dir / meter_urn / f"{feature}.js"
        write_payload_js(feature_path, payload)
        available_features.append(
            {
                "feature": feature,
                "unit": THERMAL_FEATURE_UNITS.get(feature),
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


def build_thermal_html(manifest: dict[str, Any]) -> str:
    html = build_html(manifest)
    html = html.replace("Electric Sliding Window Dashboard", "Thermal Sliding Window Dashboard")
    html = html.replace("Electric Sliding Window Plotly Dashboard", "Thermal Sliding Window Plotly Dashboard")
    html = html.replace(
        "DB raw 기준 계량기/컬럼별 슬라이딩 윈도우 비교. 범례 클릭으로 trace on/off, 상단 버튼으로 전체 표시/숨김이 가능합니다.",
        "DB raw 기준 열계량기/컬럼별 슬라이딩 윈도우 비교. 범례 클릭으로 trace on/off, 상단 버튼으로 전체 표시/숨김이 가능합니다.",
        1,
    )
    return html


def generate_dashboard() -> None:
    engine = build_engine()
    weather_df = fetch_weather_data(engine).copy()
    weather_df["ts"] = pd.to_datetime(weather_df["ts"], utc=True, errors="coerce")
    for column in ["Ta", "Igm"]:
        if column in weather_df.columns:
            weather_df[column] = pd.to_numeric(weather_df[column], errors="coerce")

    manifest: dict[str, Any] = {}
    for meter_urn in get_thermal_meters():
        payload = collect_meter_payload(engine, weather_df, meter_urn, THERMAL_DIR)
        if payload is not None:
            manifest[meter_urn] = payload

    html = build_thermal_html(manifest)
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"saved: {HTML_PATH}")


def main() -> None:
    generate_dashboard()


if __name__ == "__main__":
    main()
