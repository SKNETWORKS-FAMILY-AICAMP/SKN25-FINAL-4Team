"""열량계 전처리 (9개, measurement 7개: P/W/Tvl/Trl/Tdiff/qv/V)"""
import pandas as pd
from .base import BasePreprocessor

NON_NEGATIVE_COLS = ["P", "W", "qv", "V"]


class ThermalPreprocessor(BasePreprocessor):

    def preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self.remove_negatives(df, NON_NEGATIVE_COLS)
        df = self.remove_iqr_outliers(df, NON_NEGATIVE_COLS)
        df = self.interpolate_linear(df)
        return df
