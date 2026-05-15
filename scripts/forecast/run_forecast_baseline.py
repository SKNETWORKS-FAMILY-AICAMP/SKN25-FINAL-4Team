"""EMS 예측 모델 실행 스크립트.
 
H1.W11 P값을 타겟으로 XGBoost, Prophet, SVR, RF, SARIMAX를 비교한다.
학습 구간: 2018~2021, replay 구간: 2022~2023.
 
사용법:
    python scripts/forecast/run_forecast.py \
        --meter H1.W11 \
        --measurement P \
        --train-end 2022-01-01 \
        --eval-end 2024-01-01 \
        --models xgboost prophet rf svr \
        --output outputs/forecast/baseline
 
SARIMAX는 계산량이 크므로 별도 실행 권장:
    python scripts/forecast/run_forecast.py \
        --models sarimax \
        --resolution 1h
"""
 
from __future__ import annotations
 
import argparse
import logging
import sys
from pathlib import Path
 
import pandas as pd
 
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
 
from ems.db import fetch_measurements, load_env
from ems.forecast import (
    ForecastResult,
    build_forecast_features,
    save_forecast_comparison,
    save_forecast_result,
    train_prophet,
    train_rf,
    train_sarimax,
    train_svr,
    train_xgboost,
    train_test_split_temporal,
)
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)
 
MODEL_REGISTRY = {
    "xgboost": train_xgboost,
    "prophet": train_prophet,
    "svr": train_svr,
    "rf": train_rf,
    "sarimax": train_sarimax,
}
 
 
def run(
    meter_urn: str,
    measurement: str,
    start_ts: str,
    train_end_ts: str,
    eval_end_ts: str,
    resolution: str,
    models: list[str],
    output_dir: Path,
) -> None:
    """예측 모델 전체 실행."""
    load_env()
 
    # DB 조회
    logger.info("DB 조회: meter=%s measurement=%s", meter_urn, measurement)
    df_raw = fetch_measurements(meter_urn, measurement, start_ts, eval_end_ts, resolution)
    if df_raw.empty:
        logger.error("데이터 없음. 종료합니다.")
        return
 
    df_raw["ts"] = pd.to_datetime(df_raw["ts"], utc=True)

    # 전처리 적용
    from ems.preprocessing import run_pipeline
    df_raw["resolution_code"] = "15min"
    df_preprocessed = run_pipeline(df_raw)
    df_preprocessed = df_preprocessed[df_preprocessed["quality_flag"].isna()]
    df_preprocessed = df_preprocessed[~df_preprocessed["is_iqr_outlier"]]

    series = df_preprocessed.set_index("ts")["value"].sort_index()

    logger.info("전체 데이터: %d rows (%s ~ %s)", len(series), series.index.min(), series.index.max())
 
    # 피처 생성
    logger.info("피처 생성 중...")
    feat_df = build_forecast_features(series)
    logger.info("피처 생성 완료: %d rows × %d cols", feat_df.shape[0], feat_df.shape[1])
 
    # train/test 분할
    train, test = train_test_split_temporal(feat_df, train_end_ts)
    if len(train) == 0 or len(test) == 0:
        logger.error("train 또는 test 데이터 없음. 분할 기준 확인 필요.")
        return
 
    # 모델 실행
    results: list[ForecastResult] = []
 
    for model_name in models:
        if model_name not in MODEL_REGISTRY:
            logger.warning("알 수 없는 모델: %s. 건너뜁니다.", model_name)
            continue
 
        logger.info("=== 모델 실행: %s ===", model_name)
        try:
            result = MODEL_REGISTRY[model_name](train, test)
            result.meter_urn = meter_urn
            result.measurement = measurement
            save_forecast_result(result, output_dir)
            results.append(result)
        except Exception as exc:  # noqa: BLE001
            logger.error("모델 실패: %s error=%s", model_name, exc)
 
    # 비교 요약
    if results:
        save_forecast_comparison(results, output_dir)
        logger.info("=== 예측 모델 완료: %d개 모델 ===", len(results))
    else:
        logger.warning("실행된 모델 없음.")
 
 
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EMS 예측 모델 실행 스크립트")
    parser.add_argument("--meter", default="H1.W11", help="타겟 계량기 URN")
    parser.add_argument("--measurement", default="P", help="타겟 측정 항목")
    parser.add_argument("--start", default="2018-01-01", help="데이터 시작 시각")
    parser.add_argument("--train-end", default="2022-01-01", help="학습 구간 종료 (replay 시작)")
    parser.add_argument("--eval-end", default="2024-01-01", help="평가 구간 종료")
    parser.add_argument("--resolution", default="15min", choices=["15min", "1h"])
    parser.add_argument(
        "--models",
        nargs="+",
        default=["xgboost", "prophet", "rf", "svr"],
        choices=list(MODEL_REGISTRY.keys()),
        help="실행할 모델 목록",
    )
    parser.add_argument("--output", default="outputs/forecast/baseline")
    return parser.parse_args()
 
 
def main() -> None:
    args = _parse_args()
    load_env()
 
    logger.info(
        "예측 모델 시작: meter=%s measurement=%s train_end=%s eval_end=%s models=%s",
        args.meter, args.measurement, args.train_end, args.eval_end, args.models,
    )
 
    run(
        meter_urn=args.meter,
        measurement=args.measurement,
        start_ts=args.start,
        train_end_ts=args.train_end,
        eval_end_ts=args.eval_end,
        resolution=args.resolution,
        models=args.models,
        output_dir=Path(args.output),
    )
 
 
if __name__ == "__main__":
    main()