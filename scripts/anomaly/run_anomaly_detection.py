"""EMS 이상탐지 실행 스크립트.
 
analysis_requirements.md 3절 기준 단계적 앙상블 이상탐지를 실행한다.
 
우선순위:
    1등급: central_cooling, chp, grid_transformer
    2등급: pv, local_cooling, server_power
    3등급: 나머지
 
사용법:
    python scripts/anomaly/run_anomaly_detection.py \
        --start 2018-01-01 \
        --end 2022-01-01 \
        --resolution 15min \
        --priority 1 \
        --output outputs/anomaly/baseline
 
LSTM Autoencoder 포함 실행:
    python scripts/anomaly/run_anomaly_detection.py \
        --start 2018-01-01 \
        --end 2022-01-01 \
        --use-lstm \
        --priority 1
"""

from __future__ import annotations

from tqdm import tqdm

import argparse
import logging
import sys
from pathlib import Path
 
import pandas as pd
 
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
 
from ems.anomaly import (
    run_anomaly_pipeline,
    save_anomaly_result,
    save_anomaly_summary,
)
from ems.db import fetch_measurements, load_env
from ems.ontology import EMSOntology
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)
 
 
# ---------------------------------------------------------------------------
# 우선순위별 group 및 measurement 설정
# ---------------------------------------------------------------------------
 
PRIORITY_GROUPS = {
    1: ["central_cooling", "chp", "grid_transformer"],
    2: ["pv", "local_cooling", "server_power"],
    3: ["emission_lab", "weather_station"],
}
 
# 그룹별 주요 measurement
GROUP_MEASUREMENTS = {
    "central_cooling": ["P"],
    "chp":             ["P"],
    "grid_transformer":["P"],
    "pv":              ["P"],
    "local_cooling":   ["P"],
    "server_power":    ["P"],
    "emission_lab":    ["P"],
    "weather_station": ["Ta", "Igm"],
}
 
 
# ---------------------------------------------------------------------------
# DB 조회 헬퍼
# ---------------------------------------------------------------------------
 
def _fetch_series(
    meter_urn: str,
    measurement: str,
    start_ts: str,
    end_ts: str,
    resolution: str,
) -> pd.Series | None:
    """DB에서 단변량 시계열을 읽어 반환한다."""
    try:
        df = fetch_measurements(meter_urn, measurement, start_ts, end_ts, resolution)
        if df.empty:
            logger.warning("데이터 없음: meter=%s measurement=%s", meter_urn, measurement)
            return None
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        series = df.set_index("ts")["value"].sort_index()
        return series
    except Exception as exc:  # noqa: BLE001
        logger.error("DB 조회 실패: meter=%s measurement=%s error=%s", meter_urn, measurement, exc)
        return None
 
 
# ---------------------------------------------------------------------------
# 메인 실행 함수
# ---------------------------------------------------------------------------
 
def run(
    start_ts: str,
    end_ts: str,
    resolution: str,
    priority: int,
    output_dir: Path,
    kg: EMSOntology,
    use_lstm: bool = False,
) -> None:
    """우선순위 등급 기준 이상탐지 전체 실행."""
 
    groups = PRIORITY_GROUPS.get(priority, [])
    if not groups:
        logger.error("유효하지 않은 우선순위: %d", priority)
        return
 
    all_results = []
 
    for group in groups:
        measurements = GROUP_MEASUREMENTS.get(group, ["P"])
        domain = "weather" if group == "weather_station" else "electricity"
        role = "weather" if group == "weather_station" else None
 
        try:
            meter_urns = kg.get_feature_meter_set(
                group=group,
                domain=domain,
                role=role,
                exclude_redundant=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("ontology 조회 실패: group=%s error=%s", group, exc)
            continue
 
        if not meter_urns:
            logger.warning("meter 없음: group=%s", group)
            continue
 
        logger.info("=== 그룹: %s | meters=%d ===", group, len(meter_urns))
 
        for meter_urn in tqdm(meter_urns, desc=f"{group}", unit="meter"):
            for measurement in measurements:
                logger.info("이상탐지: %s / %s", meter_urn, measurement)
 
                series = _fetch_series(meter_urn, measurement, start_ts, end_ts, resolution)
                if series is None or len(series) < 100:
                    logger.warning("데이터 부족: meter=%s measurement=%s rows=%s",
                                   meter_urn, measurement, len(series) if series is not None else 0)
                    continue
 
                result = run_anomaly_pipeline(
                    series=series,
                    meter_urn=meter_urn,
                    measurement=measurement,
                    resolution_code=resolution,
                    use_lstm=use_lstm,
                )
 
                save_anomaly_result(result, output_dir)
                all_results.append(result)
 
    if all_results:
        save_anomaly_summary(all_results, output_dir)
        logger.info("=== 이상탐지 완료: 총 %d개 계량기/측정 항목 ===", len(all_results))
    else:
        logger.warning("이상탐지 결과 없음.")
 
 
# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
 
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EMS 이상탐지 실행 스크립트")
    parser.add_argument("--start", default="2018-01-01", help="조회 시작 (UTC)")
    parser.add_argument("--end", default="2022-01-01", help="조회 종료 UTC 반개구간")
    parser.add_argument("--resolution", default="15min", choices=["15min", "1h"])
    parser.add_argument("--priority", type=int, default=1, choices=[1, 2, 3],
                        help="우선순위 등급 (1=central_cooling/chp/grid_transformer)")
    parser.add_argument("--output", default="outputs/anomaly/baseline")
    parser.add_argument("--use-lstm", action="store_true", help="LSTM Autoencoder 포함")
    parser.add_argument("--ttl", default=None, help="ems.ttl 경로 (기본: from_default)")
    return parser.parse_args()
 
 
def main() -> None:
    args = _parse_args()
    load_env()
 
    logger.info(
        "이상탐지 시작: start=%s end=%s resolution=%s priority=%d output=%s lstm=%s",
        args.start, args.end, args.resolution, args.priority, args.output, args.use_lstm,
    )
 
    if args.ttl:
        kg = EMSOntology.from_ttl(args.ttl)
    else:
        kg = EMSOntology.from_default()
 
    run(
        start_ts=args.start,
        end_ts=args.end,
        resolution=args.resolution,
        priority=args.priority,
        output_dir=Path(args.output),
        kg=kg,
        use_lstm=args.use_lstm,
    )
 
 
if __name__ == "__main__":
    main()