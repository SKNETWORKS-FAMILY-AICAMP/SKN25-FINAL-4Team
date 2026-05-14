"""전기 계량기 전처리 (71개, measurement 22개)"""
import pandas as pd
from .base import BasePreprocessor

NON_NEGATIVE_COLS = ["P", "W", "I1", "I2", "I3"]


class ElectricPreprocessor(BasePreprocessor):

    def preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self.remove_negatives(df, NON_NEGATIVE_COLS)
        df = self.remove_iqr_outliers(df, NON_NEGATIVE_COLS)
        df = self.interpolate_linear(df)
        return df
