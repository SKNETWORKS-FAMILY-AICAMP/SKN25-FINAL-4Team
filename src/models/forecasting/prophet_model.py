"""
Prophet 단기 예측 모델 — 계통 전력 소비 (grid_P) 7일 예측.
날씨(Ta) 외부 회귀 변수 포함, MLflow 실험 추적.
"""

import warnings
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
from prophet import Prophet
from prophet.diagnostics import cross_validation, performance_metrics

warnings.filterwarnings("ignore")

SAVE_DIR  = Path(__file__).parent / "saved"
MODEL_DIR = SAVE_DIR / "prophet"
MLFLOW_URI = "sqlite:////Users/khl/workspace/final/mlruns.db"

# 예측 대상 및 하이퍼파라미터
TARGET      = "grid_P"   # W 단위 → 학습 시 kW 변환
HORIZON     = "7 days"
INITIAL     = "365 days"
PERIOD      = "30 days"


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """loader.py 출력을 Prophet 입력 형식으로 변환."""
    d = df[["ts", TARGET, "Ta"]].copy()
    d = d.rename(columns={"ts": "ds", TARGET: "y"})
    d["ds"] = pd.to_datetime(d["ds"]).dt.tz_localize(None)   # Prophet은 tz-naive 필요
    d["y"]  = d["y"] / 1000                                   # W → kW
    d["Ta"] = d["Ta"].ffill().bfill().fillna(10.0)   # 독일 연평균 기온 fallback
    d = d.dropna(subset=["y"])
    return d.sort_values("ds").reset_index(drop=True)


def train(df: pd.DataFrame, run_cv: bool = False) -> Prophet:
    """Prophet 학습 + MLflow 기록."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    d = _prepare(df)

    mlflow.set_tracking_uri(MLFLOW_URI)
    mlflow.set_experiment("forecasting_prophet")

    params = dict(
        changepoint_prior_scale=0.05,
        seasonality_prior_scale=10,
        holidays_prior_scale=5,
        seasonality_mode="multiplicative",
    )

    with mlflow.start_run(run_name="prophet_grid_P"):
        mlflow.log_params(params)
        mlflow.log_param("target", TARGET)
        mlflow.log_param("train_rows", len(d))
        mlflow.log_param("train_start", str(d["ds"].min()))
        mlflow.log_param("train_end",   str(d["ds"].max()))

        model = Prophet(**params)
        model.add_regressor("Ta")
        model.add_seasonality(name="weekly",  period=7,    fourier_order=5)
        model.add_seasonality(name="daily",   period=1,    fourier_order=8)
        model.add_seasonality(name="yearly",  period=365.25, fourier_order=10)
        model.fit(d)

        if run_cv:
            cv_df = cross_validation(model, initial=INITIAL, period=PERIOD, horizon=HORIZON, parallel="processes")
            pm    = performance_metrics(cv_df)
            mlflow.log_metric("cv_mae",  float(pm["mae"].mean()))
            mlflow.log_metric("cv_rmse", float(pm["rmse"].mean()))
            mlflow.log_metric("cv_mape", float(pm["mape"].mean()))
            print(f"  CV MAE={pm['mae'].mean():.1f} kW  RMSE={pm['rmse'].mean():.1f} kW  MAPE={pm['mape'].mean():.3f}")

        import pickle
        with open(MODEL_DIR / "model.pkl", "wb") as f:
            pickle.dump(model, f)
        mlflow.log_artifact(str(MODEL_DIR / "model.pkl"))
        print(f"Prophet 학습 완료 — {len(d)}행, {d['ds'].min().date()} ~ {d['ds'].max().date()}")

    return model


def predict(df: pd.DataFrame, hours: int = 168) -> pd.DataFrame:
    """
    저장된 모델로 예측. df는 미래 날씨 포함 또는 없으면 Ta=평균 사용.
    반환: ds, yhat, yhat_lower, yhat_upper (kW)
    """
    import pickle
    model_path = MODEL_DIR / "model.pkl"
    if not model_path.exists():
        raise FileNotFoundError("Prophet 모델 없음. train() 먼저 실행하세요.")
    with open(model_path, "rb") as f:
        model = pickle.load(f)

    last_ts = pd.to_datetime(df["ts"]).max()
    if last_ts.tzinfo is not None:
        last_ts = last_ts.tz_localize(None)

    future = model.make_future_dataframe(periods=hours, freq="h", include_history=False)
    future["ds"] = pd.to_datetime(future["ds"]).dt.tz_localize(None)

    # Ta: 시간대별 평균으로 채움 (hour 기준 groupby)
    hist = _prepare(df)
    ta_mean = float(hist["Ta"].mean())
    ta_by_hour = hist.groupby(hist["ds"].dt.hour)["Ta"].mean()
    future["Ta"] = future["ds"].dt.hour.map(ta_by_hour).fillna(ta_mean)

    fc = model.predict(future)
    return fc[["ds", "yhat", "yhat_lower", "yhat_upper"]].rename(columns={"ds": "ts"})


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from data.loader import load_range

    print("데이터 로드 중...")
    df = load_range("2018-01-01", "2024-01-01")
    print(f"  {len(df)}행 로드")

    print("Prophet 학습 중...")
    model = train(df, run_cv=False)

    print("7일 예측...")
    fc = predict(df, hours=168)
    print(fc.head(10).to_string(index=False))
