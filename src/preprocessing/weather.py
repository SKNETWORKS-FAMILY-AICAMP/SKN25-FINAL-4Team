"""기상관측소 전처리 (1개: WeatherStation.Weather, measurement 10개)"""
import pandas as pd
from .base import BasePreprocessor

# 기상 항목은 음수 가능 (Ta=기온 등), IQR만 적용
IQR_COLS = ["Igm", "Igc", "rho"]


class WeatherPreprocessor(BasePreprocessor):

    def preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self.remove_iqr_outliers(df, IQR_COLS)
        df = self.interpolate_linear(df)
        return df
