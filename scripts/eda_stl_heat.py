from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "raw_eda" / "stl"
PNG_ROOT = OUTPUT_ROOT / "png" / "thermal"
CSV_ROOT = OUTPUT_ROOT / "csv" / "thermal"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eda_stl_electric import (
    build_input_series,
    configure_matplotlib,
    FEATURE_UNITS,
    load_raw_meter_data,
    run_stl_eda,
    save_stl_plot,
    summarize_stl_result,
)


heat_meter_config: dict[str, list[str]] = {
    "V.K21":  ["Tdiff", "qv", "Tvl", "Trl", "P"],
    "H1.K11": ["Tdiff", "qv", "Tvl", "Trl", "P"],
    "H1.K12": ["Tdiff", "Tvl", "Trl", "P"],
    "H1.K14": ["Tdiff", "qv", "Tvl", "Trl", "P"],
    "H1.K15": ["Tdiff", "Tvl", "Trl", "P"],
    "H1.K16": ["Tdiff", "qv", "Tvl", "Trl", "P"],
    "H2.K21": ["Tdiff", "qv", "Tvl", "Trl", "P"],
    "H1.W11": ["Tdiff", "qv", "Tvl", "Trl", "P"],
    "H1.W12": ["Tdiff", "qv", "Tvl", "Trl", "P"],
}

COOLING_METERS: set[str] = {"V.K21", "H1.K11", "H1.K12", "H1.K14", "H1.K15", "H1.K16", "H2.K21"}
HEATING_METERS: set[str] = {"H1.W11", "H1.W12"}

METER_NOTES: dict[str, str] = {
    "V.K21":  "유량 센서(qv) 고장 이력 2회 존재 (논문 확정). qv 잔차에서 특정 구간 급락 주의.",
    "H1.K11": "2022년 초 qv/Tdiff 결측 구간 있음. 결측률 출력 후 진행.",
    "H1.K12": "2022년 이후 거의 미가동 (P=0 고착). 결측률 출력 후 진행. 신뢰도 낮음 주의.",
    "H1.K15": "거의 미가동 상태. 결측률 출력 후 진행. 신뢰도 낮음 주의.",
    "H2.K21": "2021.11.15~12.10 게이트웨이 장애 구간 (논문 확정). 해당 구간 잔차 집중 예상.",
    "H1.W11": "2023년 운전 조건 변화 (논문 확정, 난방 현대화). 2023년 잔차 패턴 변화 예상.",
    "H1.W12": "여름 qv 거의 0 (CHP 여름 미가동 정상). 여름 구간 잔차 해석 주의.",
}

DETAIL_COLS = ["feature", "ts", "observed", "trend", "seasonal", "residual", "upper", "lower", "is_anomaly"]
SUMMARY_COLS = ["feature", "n_total", "n_anomaly", "ratio", "residual_max", "residual_min", "upper", "lower", "null_ratio"]


def print_yearly_distribution(anomaly_mask: pd.Series, meter_urn: str, col: str) -> None:
    if anomaly_mask.empty:
        return
    yearly = anomaly_mask.groupby(anomaly_mask.index.year).sum().astype(int)
    print(f"  연도별 이상 분포 [{meter_urn} - {col}]:")
    for year, count in yearly.items():
        print(f"    {year}: {count}건")


def main() -> None:
    configure_matplotlib()

    for meter_urn, columns in heat_meter_config.items():
        print(f"\n{'=' * 50}")
        print(f"=== {meter_urn} ===")

        if meter_urn in COOLING_METERS:
            print("  [냉각 계량기] Tdiff 음수가 정상. 잔차 양수 방향 튐이 핵심 이상 신호.")
        elif meter_urn in HEATING_METERS:
            print("  [난방 계량기] Tdiff 양수가 정상. 잔차 음수 방향 튐이 핵심 이상 신호.")
        if meter_urn in METER_NOTES:
            print(f"  [주의] {METER_NOTES[meter_urn]}")

        try:
            df = load_raw_meter_data(meter_urn)
        except Exception as exc:
            print(f"  데이터 로드 실패: {exc}\n")
            continue

        meter_png_dir = PNG_ROOT / meter_urn
        meter_csv_dir = CSV_ROOT / meter_urn
        summary_rows: list[dict] = []
        detail_frames: list[pd.DataFrame] = []

        for col in columns:
            print(f"\n  --- {col} ---")
            if col not in df.columns:
                print(f"  {col} 컬럼 없음, skip")
                continue

            missing_ratio = float(df[col].isnull().mean() * 100)
            print(f"  결측률: {missing_ratio:.1f}%")
            if missing_ratio > 50:
                print("  → 결측률 높음, STL 결과 해석 주의")

            try:
                series = build_input_series(df, col)
                result, anomaly_mask = run_stl_eda(series, f"{meter_urn} - {col}", sigma=3)
                print_yearly_distribution(anomaly_mask, meter_urn, col)

                summary_row, detail_df = summarize_stl_result(
                    meter_urn, col, result, anomaly_mask, missing_ratio, sigma=3
                )
                summary_rows.append(summary_row)
                detail_frames.append(detail_df)

                output_path = meter_png_dir / f"{meter_urn}_{col}_stl.png"
                save_stl_plot(
                    result, anomaly_mask,
                    f"{meter_urn} - {col} STL 분해 (±3σ)",
                    output_path, sigma=3,
                    unit=FEATURE_UNITS.get(col),
                )
                print(f"  저장: {output_path}")

            except Exception as exc:
                print(f"  STL 실패: {exc}")

        if summary_rows:
            meter_csv_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(summary_rows)[SUMMARY_COLS].to_csv(
                meter_csv_dir / f"{meter_urn}_summary.csv", index=False
            )
            print(f"\n  summary → {meter_csv_dir / f'{meter_urn}_summary.csv'}")

        if detail_frames:
            meter_csv_dir.mkdir(parents=True, exist_ok=True)
            pd.concat(detail_frames, ignore_index=True)[DETAIL_COLS].to_csv(
                meter_csv_dir / f"{meter_urn}_detail.csv", index=False
            )
            print(f"  detail  → {meter_csv_dir / f'{meter_urn}_detail.csv'}")


if __name__ == "__main__":
    main()
