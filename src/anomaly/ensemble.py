"""
비지도 3단계 앙상블 이상탐지

1단계: 통계 (평균 ±2σ, STL 분해 후 잔차 σ)
2단계: Isolation Forest (P+PF+T_flow 다변량)
3단계: LSTM Autoencoder (복원 오차 기반)

판정 기준:
  2개 이상 일치 → 주의 (노란불, WARNING)
  3개 모두 일치 → 위험 (빨간불, CRITICAL)

issues 라벨 활용:
  - 학습 제외 마스킹
  - 결과 교차 검증
  - 리포팅 원인 구분
"""
import pandas as pd
from enum import Enum


class AnomalyLevel(Enum):
    NORMAL   = "normal"
    WARNING  = "warning"   # 2/3 일치
    CRITICAL = "critical"  # 3/3 일치


def ensemble_judge(
    stat_flag: bool,
    iforest_flag: bool,
    lstm_flag: bool,
) -> AnomalyLevel:
    count = sum([stat_flag, iforest_flag, lstm_flag])
    if count == 3:
        return AnomalyLevel.CRITICAL
    elif count >= 2:
        return AnomalyLevel.WARNING
    return AnomalyLevel.NORMAL
