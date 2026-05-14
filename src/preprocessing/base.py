"""
공통 전처리 베이스 클래스

전처리 3단계:
  1단계(SQL): EAV → 피벗 (queries.py에서 처리)
  2단계(Python): 음수 처리, IQR 이상치 제거, 선형 보간
  3단계(Python): 모델 입력 형태 구성

처리 방식: 계량기별 순차 처리 (A방식)
  for meter_urn in meter_list:
      df = query → preprocess → predict → save
  (전체 피벗 B방식 미채택: 컬럼 1,562개, 메모리 2~3GB 문제)
"""
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod


class BasePreprocessor(ABC):

    def __init__(self, config: dict):
        self.config = config

    def remove_negatives(self, df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        """물리적으로 음수 불가 컬럼의 음수값 NaN 처리."""
        for col in cols:
            if col in df.columns:
                df[col] = df[col].where(df[col] >= 0, other=np.nan)
        return df

    def remove_iqr_outliers(self, df: pd.DataFrame, cols: list[str], factor: float = 1.5) -> pd.DataFrame:
        """IQR 기반 이상치 NaN 처리."""
        for col in cols:
            if col in df.columns:
                Q1 = df[col].quantile(0.25)
                Q3 = df[col].quantile(0.75)
                IQR = Q3 - Q1
                lower = Q1 - factor * IQR
                upper = Q3 + factor * IQR
                df[col] = df[col].where(df[col].between(lower, upper), other=np.nan)
        return df

    def interpolate_linear(self, df: pd.DataFrame, limit: int = 3) -> pd.DataFrame:
        """선형 보간. limit: 최대 연속 결측 허용 개수."""
        return df.interpolate(method="linear", limit=limit, limit_direction="forward")

    @abstractmethod
    def preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        """종류별 전처리 구현 (electric/thermal/weather에서 override)."""
        pass
