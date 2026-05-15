"""Baseline feature 생성 스크립트.
 
feature_contract.md 16절 초기 feature set을 생성하고
outputs/tables/feature_baseline/ 에 저장한다.
 
사용법:
    python scripts/features/build_baseline_features.py \
        --start 2018-01-01 \
        --end 2022-01-01 \
        --resolution 15min \
        --output outputs/tables/feature_baseline
 
live replay 구간(2022~2023)은 별도 실행으로 분리한다.
학습 구간(2018~2021)과 동일한 meter set, feature set을 사용한다.
"""
 
from __future__ import annotations
 
import argparse
import logging
import sys
from pathlib import Path
 
import pandas as pd
 
# 프로젝트 루트를 sys.path에 추가
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
 
from ems.db import load_env
from ems.features import (
    FeatureMetadata,
    ResolutionCode,
    build_calendar_features,
    build_group_aggregate,
    build_redundancy_diff,
    build_rolling_features,
    build_weather_features,
    save_feature_metadata,
    to_wide,
)
from ems.ontology import EMSOntology
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)
 
 
# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
 
# feature_contract.md 8절 measurement family 기준
ELECTRICITY_MEASUREMENT = "P"
WEATHER_MEASUREMENTS = ["Ta", "Igm", "Ah"]
 
# feature_contract.md 16.1 group aggregate 대상
AGGREGATE_GROUPS = [
    {"group": "central_cooling", "domain": "electricity", "role": "consumption"},
    {"group": "server_power",    "domain": "electricity", "role": "consumption"},
    {"group": "emission_lab",    "domain": "electricity", "role": "consumption"},
    {"group": "pv",              "domain": "electricity", "role": "production"},
    {"group": "chp",             "domain": "electricity", "role": "production"},
]
 
# feature_contract.md 16.3 redundancy comparison 대상
REDUNDANCY_GROUPS = ["server_power", "local_cooling", "pv", "chp"]
 
# feature_contract.md 9.2: 15min 기준 rolling window
ROLLING_WINDOWS = {"1h": 4, "6h": 24, "24h": 96}
 
 
# ---------------------------------------------------------------------------
# 메인 빌드 함수
# ---------------------------------------------------------------------------
 
def build_baseline(
    start_ts: str,
    end_ts: str,
    resolution: ResolutionCode,
    output_dir: Path,
    kg: EMSOntology,
) -> None:
    """baseline feature 전체 생성."""
 
    all_frames: list[pd.DataFrame] = []
    all_metas: list[FeatureMetadata] = []
 
    # ------------------------------------------------------------------
    # 1. Group aggregate feature (feature_contract.md 16.1)
    # ------------------------------------------------------------------
    logger.info("=== Group aggregate feature 생성 시작 ===")
    for cfg in AGGREGATE_GROUPS:
        group = cfg["group"]
        logger.info("group aggregate: %s", group)
        try:
            agg_df, meta = build_group_aggregate(
                kg=kg,
                group=group,
                measurement=ELECTRICITY_MEASUREMENT,
                start_ts=start_ts,
                end_ts=end_ts,
                resolution=resolution,
                exclude_redundant=True,
                domain=cfg["domain"],
                role=cfg["role"],
            )
            if agg_df.empty:
                logger.warning("aggregate 결과 없음: group=%s", group)
                continue
 
            # rolling feature 추가
            feature_col = [c for c in agg_df.columns if c != "ts"][0]
            rolling_df = build_rolling_features(
                series=agg_df.set_index("ts")[feature_col],
                feature_name_base=feature_col,
                resolution=resolution,
                windows=ROLLING_WINDOWS,
            )
            rolling_df = rolling_df.reset_index()
 
            all_frames.append(agg_df)
            all_frames.append(rolling_df)
            all_metas.append(meta)
 
        except Exception as exc:  # noqa: BLE001
            logger.error("group aggregate 실패: group=%s error=%s", group, exc)
 
    # ------------------------------------------------------------------
    # 2. Weather external feature (feature_contract.md 16.2)
    # ------------------------------------------------------------------
    logger.info("=== Weather external feature 생성 시작 ===")
    try:
        weather_df, weather_metas = build_weather_features(
            kg=kg,
            measurements=WEATHER_MEASUREMENTS,
            start_ts=start_ts,
            end_ts=end_ts,
            resolution=resolution,
            rolling_window_ticks=4,  # 1h rolling
        )
        if not weather_df.empty:
            all_frames.append(weather_df)
            all_metas.extend(weather_metas)
    except Exception as exc:  # noqa: BLE001
        logger.error("weather feature 실패: %s", exc)
 
    # ------------------------------------------------------------------
    # 3. Redundancy comparison feature (feature_contract.md 16.3)
    # ------------------------------------------------------------------
    logger.info("=== Redundancy comparison feature 생성 시작 ===")
    for group in REDUNDANCY_GROUPS:
        logger.info("redundancy diff: %s", group)
        try:
            diff_df, diff_metas = build_redundancy_diff(
                kg=kg,
                group=group,
                measurement=ELECTRICITY_MEASUREMENT,
                start_ts=start_ts,
                end_ts=end_ts,
                resolution=resolution,
            )
            if not diff_df.empty:
                all_frames.append(diff_df)
                all_metas.extend(diff_metas)
        except Exception as exc:  # noqa: BLE001
            logger.error("redundancy diff 실패: group=%s error=%s", group, exc)
 
    # ------------------------------------------------------------------
    # 4. Calendar feature
    # ------------------------------------------------------------------
    logger.info("=== Calendar feature 생성 시작 ===")
    if all_frames:
        # ts 합집합에서 calendar feature 생성
        ts_union = pd.concat(
            [f["ts"] for f in all_frames if "ts" in f.columns],
            ignore_index=True,
        ).drop_duplicates().sort_values()
        ts_index = pd.DatetimeIndex(ts_union)
        calendar_df = build_calendar_features(ts_index)
        all_frames.append(calendar_df)
 
    # ------------------------------------------------------------------
    # 5. Wide format 전환 및 저장
    # ------------------------------------------------------------------
    if not all_frames:
        logger.error("생성된 feature가 없습니다. 종료합니다.")
        return
 
    logger.info("=== Wide format 전환 및 저장 ===")
    wide_df = to_wide(all_frames)
 
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "feature_matrix.parquet"
    wide_df.to_parquet(output_path, index=False)
    logger.info("feature matrix 저장: %s (shape=%s)", output_path, wide_df.shape)
 
    # feature metadata 저장
    meta_path = save_feature_metadata(all_metas, output_dir)
    logger.info("feature metadata 저장: %s", meta_path)
 
    # feature quality summary
    _save_quality_summary(wide_df, output_dir)
 
    logger.info("=== Baseline feature 생성 완료 ===")
 
 
def _save_quality_summary(df: pd.DataFrame, output_dir: Path) -> None:
    """feature_contract.md 14절 feature quality summary."""
    summary_rows = []
    for col in df.columns:
        if col == "ts":
            continue
        total = len(df)
        missing = df[col].isna().sum()
        summary_rows.append({
            "feature_name": col,
            "total_rows": total,
            "missing_rows": int(missing),
            "missing_rate": round(missing / total, 6) if total > 0 else None,
            "min": df[col].min() if pd.api.types.is_numeric_dtype(df[col]) else None,
            "max": df[col].max() if pd.api.types.is_numeric_dtype(df[col]) else None,
            "mean": df[col].mean() if pd.api.types.is_numeric_dtype(df[col]) else None,
        })
    summary_df = pd.DataFrame(summary_rows)
    path = output_dir / "feature_quality_summary.csv"
    summary_df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("feature quality summary 저장: %s", path)
 
 
# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
 
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EMS baseline feature 생성 스크립트")
    parser.add_argument(
        "--start",
        default="2018-01-01",
        help="조회 시작 시각 (UTC, 기본값: 2018-01-01)",
    )
    parser.add_argument(
        "--end",
        default="2022-01-01",
        help="조회 종료 시각 UTC 반개구간 (기본값: 2022-01-01, live replay 구간 제외)",
    )
    parser.add_argument(
        "--resolution",
        default="15min",
        choices=["15min", "1h"],
        help="해상도 (기본값: 15min)",
    )
    parser.add_argument(
        "--output",
        default="outputs/tables/feature_baseline",
        help="출력 디렉터리 (기본값: outputs/tables/feature_baseline)",
    )
    parser.add_argument(
        "--ttl",
        default=None,
        help="ems.ttl 경로. 생략하면 EMSOntology.from_default() 사용",
    )
    return parser.parse_args()
 
 
def main() -> None:
    args = _parse_args()
    load_env()
 
    logger.info(
        "baseline feature 생성 시작: start=%s end=%s resolution=%s output=%s",
        args.start,
        args.end,
        args.resolution,
        args.output,
    )
 
    # feature_contract.md 5.1: ontology helper 로드
    if args.ttl:
        kg = EMSOntology.from_ttl(args.ttl)
        logger.info("ontology 로드: %s", args.ttl)
    else:
        kg = EMSOntology.from_default()
        logger.info("ontology 로드: from_default()")
 
    build_baseline(
        start_ts=args.start,
        end_ts=args.end,
        resolution=args.resolution,
        output_dir=Path(args.output),
        kg=kg,
    )
 
 
if __name__ == "__main__":
    main()
 