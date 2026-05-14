"""
한국 지역난방 열량계 데이터 어댑터

한국 데이터 특징:
  - 기간: 2021~2025 (5년), 약 16,868,714행
  - 이상 라벨 없음 → 비지도 전처리
  - KST 처리 필요
  - 용도: 파이프라인 범용성 검증 (A안)

공통 스키마 (혼다 기준):
  ts | meter_urn | measurement | value
"""
import pandas as pd


class KoreaAdapter:

    def __init__(self, config: dict):
        self.config = config
        self.time_col = config.get("time_col", "timestamp")
        self.target_col = config.get("target_col", "value")
        self.timezone = config.get("timezone", "Asia/Seoul")

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """한국 데이터 → 공통 EAV 스키마 변환."""
        # TODO: 실제 컬럼명 확인 후 구현
        raise NotImplementedError("한국 데이터 컬럼 구조 확인 후 구현 필요")
