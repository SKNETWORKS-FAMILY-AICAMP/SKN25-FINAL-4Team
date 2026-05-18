"""EMS 예측 모델 모듈.

analysis_requirements.md 4절 기준 예측 모델을 구현한다.

모델 후보:
    - XGBoost
    - Prophet
    - SVR
    - RandomForest
    - SARIMAX

평가 지표: MAE, RMSE, MAPE
학습/평가 분할: 시간 순서 보존 (live_simulation.md 기준)
학습 구간: 2018~2021 (4년)
Replay 구간: 2022~2023 (2년)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

ModelName = Literal["xgboost", "prophet", "svr", "rf", "sarimax"]


@dataclass
class ForecastResult:
    """예측 모델 평가 결과."""

    model_name: ModelName
    meter_urn: str
    measurement: str
    train_period: str
    eval_period: str
    mae: float
    rmse: float
    mape: float
    y_true: pd.Series
    y_pred: pd.Series
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def build_forecast_features(
    series: pd.Series,
    lag_ticks: list[int] | None = None,
    rolling_windows: dict[str, int] | None = None,
) -> pd.DataFrame:
    """예측 모델 입력 피처 생성."""
    _lags = lag_ticks or [1, 4, 96, 672]
    _windows = rolling_windows or {"1h": 4, "6h": 24, "24h": 96, "7d": 672}

    df = pd.DataFrame({"target": series})

    for tick in _lags:
        df[f"lag_{tick}"] = series.shift(tick)

    for label, ticks in _windows.items():
        df[f"rolling_mean_{label}"] = series.shift(1).rolling(window=ticks, min_periods=1).mean()
        df[f"rolling_std_{label}"] = series.shift(1).rolling(window=ticks, min_periods=1).std()

    df["diff_1"] = series.diff(1)
    df["diff_4"] = series.diff(4)

    idx = series.index
    seconds_in_day = 24 * 3600
    day_seconds = idx.hour * 3600 + idx.minute * 60
    df["sin_hour"] = np.sin(2 * np.pi * day_seconds / seconds_in_day)
    df["cos_hour"] = np.cos(2 * np.pi * day_seconds / seconds_in_day)
    df["sin_dow"] = np.sin(2 * np.pi * idx.dayofweek / 7)
    df["cos_dow"] = np.cos(2 * np.pi * idx.dayofweek / 7)
    df["month"] = idx.month
    df["is_weekend"] = (idx.dayofweek >= 5).astype(int)

    df = df.dropna()
    return df


def train_val_test_split_temporal(
    df: pd.DataFrame,
    train_end_ts: str,
    validation_end_ts: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """시간 순서 보존 train/validation/test 3분할."""
    train_end = pd.Timestamp(train_end_ts, tz="UTC")
    val_end = pd.Timestamp(validation_end_ts, tz="UTC")

    train = df[df.index < train_end]
    validation = df[(df.index >= train_end) & (df.index < val_end)]
    test = df[df.index >= val_end]

    logger.info("train: %d rows (%s ~ %s)", len(train), train.index.min(), train.index.max())
    logger.info("validation: %d rows (%s ~ %s)", len(validation), validation.index.min(), validation.index.max())
    logger.info("test: %d rows (%s ~ %s)", len(test), test.index.min(), test.index.max())
    return train, validation, test


def evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """MAE, RMSE, MAPE 계산."""
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mask = y_true != 0
    mape = float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100) if mask.any() else float("nan")
    return {"mae": mae, "rmse": rmse, "mape": mape}


def train_xgboost(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    test: pd.DataFrame,
    target_col: str = "target",
    **xgb_params,
) -> ForecastResult:
    """XGBoost 예측 모델."""
    try:
        from xgboost import XGBRegressor
    except ImportError:
        raise ImportError("xgboost가 설치되지 않았습니다. uv pip install xgboost")

    feature_cols = [c for c in train.columns if c != target_col]
    X_train = train[feature_cols].values
    y_train = train[target_col].values
    X_val = validation[feature_cols].values
    y_val = validation[target_col].values
    X_test = test[feature_cols].values
    y_test = test[target_col].values

    params = {
        "n_estimators": 500,
        "max_depth": 6,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "random_state": 42,
        "n_jobs": -1,
    }
    params.update(xgb_params)

    model = XGBRegressor(**params)
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    y_pred = model.predict(X_test)

    metrics = evaluate(y_test, y_pred)
    logger.info("XGBoost: MAE=%.2f RMSE=%.2f MAPE=%.2f%%", metrics["mae"], metrics["rmse"], metrics["mape"])

    return ForecastResult(
        model_name="xgboost",
        meter_urn="",
        measurement=target_col,
        train_period=f"{train.index.min()} ~ {train.index.max()}",
        eval_period=f"{test.index.min()} ~ {test.index.max()}",
        y_true=pd.Series(y_test, index=test.index),
        y_pred=pd.Series(y_pred, index=test.index),
        **metrics,
    )

def train_prophet(
    train: pd.DataFrame,
    validation: pd.DataFrame,  # 추가
    test: pd.DataFrame,
    target_col: str = "target",
) -> ForecastResult:
    # validation 인자 추가만 하고 내부 로직은 동일
    """Prophet 예측 모델."""
    try:
        from prophet import Prophet
    except ImportError:
        raise ImportError("prophet이 설치되지 않았습니다. uv pip install prophet")

    train_prophet_df = pd.DataFrame({
        "ds": train.index.tz_localize(None),
        "y": train[target_col].values,
    })

    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=True,
        changepoint_prior_scale=0.05,
    )
    model.fit(train_prophet_df)

    future = pd.DataFrame({"ds": test.index.tz_localize(None)})
    forecast = model.predict(future)
    y_pred = forecast["yhat"].values
    y_test = test[target_col].values

    metrics = evaluate(y_test, y_pred)
    logger.info("Prophet: MAE=%.2f RMSE=%.2f MAPE=%.2f%%", metrics["mae"], metrics["rmse"], metrics["mape"])

    return ForecastResult(
        model_name="prophet",
        meter_urn="",
        measurement=target_col,
        train_period=f"{train.index.min()} ~ {train.index.max()}",
        eval_period=f"{test.index.min()} ~ {test.index.max()}",
        y_true=pd.Series(y_test, index=test.index),
        y_pred=pd.Series(y_pred, index=test.index),
        **metrics,
    )


def train_svr(
    train: pd.DataFrame,
    validation: pd.DataFrame,  # 추가
    test: pd.DataFrame,
    target_col: str = "target",
) -> ForecastResult:
    # validation 인자 추가만 하고 내부 로직은 동일
    """SVR 예측 모델."""
    from sklearn.svm import SVR

    feature_cols = [c for c in train.columns if c != target_col]
    X_train = train[feature_cols].values
    y_train = train[target_col].values
    X_test = test[feature_cols].values
    y_test = test[target_col].values

    scaler_X = StandardScaler()
    scaler_y = StandardScaler()
    X_train_s = scaler_X.fit_transform(X_train)
    y_train_s = scaler_y.fit_transform(y_train.reshape(-1, 1)).ravel()
    X_test_s = scaler_X.transform(X_test)

    model = SVR(kernel="rbf", C=1.0, epsilon=0.1)
    model.fit(X_train_s, y_train_s)
    y_pred_s = model.predict(X_test_s)
    y_pred = scaler_y.inverse_transform(y_pred_s.reshape(-1, 1)).ravel()

    metrics = evaluate(y_test, y_pred)
    logger.info("SVR: MAE=%.2f RMSE=%.2f MAPE=%.2f%%", metrics["mae"], metrics["rmse"], metrics["mape"])

    return ForecastResult(
        model_name="svr",
        meter_urn="",
        measurement=target_col,
        train_period=f"{train.index.min()} ~ {train.index.max()}",
        eval_period=f"{test.index.min()} ~ {test.index.max()}",
        y_true=pd.Series(y_test, index=test.index),
        y_pred=pd.Series(y_pred, index=test.index),
        **metrics,
    )


def train_rf(
    train: pd.DataFrame,
    validation: pd.DataFrame,  # 추가
    test: pd.DataFrame,
    target_col: str = "target",
) -> ForecastResult:
    # validation 인자 추가만 하고 내부 로직은 동일
    """RandomForest 예측 모델."""
    from sklearn.ensemble import RandomForestRegressor

    feature_cols = [c for c in train.columns if c != target_col]
    X_train = train[feature_cols].values
    y_train = train[target_col].values
    X_test = test[feature_cols].values
    y_test = test[target_col].values

    model = RandomForestRegressor(
        n_estimators=300,
        max_depth=10,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)

    metrics = evaluate(y_test, y_pred)
    logger.info("RF: MAE=%.2f RMSE=%.2f MAPE=%.2f%%", metrics["mae"], metrics["rmse"], metrics["mape"])

    return ForecastResult(
        model_name="rf",
        meter_urn="",
        measurement=target_col,
        train_period=f"{train.index.min()} ~ {train.index.max()}",
        eval_period=f"{test.index.min()} ~ {test.index.max()}",
        y_true=pd.Series(y_test, index=test.index),
        y_pred=pd.Series(y_pred, index=test.index),
        **metrics,
    )


def train_sarimax(
    train: pd.DataFrame,
    validation: pd.DataFrame,  # 추가
    test: pd.DataFrame,
    target_col: str = "target",
    order: tuple = (1, 1, 1),
    seasonal_order: tuple = (1, 1, 1, 96),
) -> ForecastResult:
    # validation 인자 추가만 하고 내부 로직은 동일
    """SARIMAX 예측 모델. 계산량이 크므로 1h 해상도 권장."""
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    y_train = train[target_col]
    y_test = test[target_col]

    model = SARIMAX(
        y_train,
        order=order,
        seasonal_order=seasonal_order,
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    fitted = model.fit(disp=False)
    forecast = fitted.forecast(steps=len(y_test))
    y_pred = forecast.values

    metrics = evaluate(y_test.values, y_pred)
    logger.info("SARIMAX: MAE=%.2f RMSE=%.2f MAPE=%.2f%%", metrics["mae"], metrics["rmse"], metrics["mape"])

    return ForecastResult(
        model_name="sarimax",
        meter_urn="",
        measurement=target_col,
        train_period=f"{train.index.min()} ~ {train.index.max()}",
        eval_period=f"{test.index.min()} ~ {test.index.max()}",
        y_true=y_test,
        y_pred=pd.Series(y_pred, index=test.index),
        **metrics,
    )


def save_forecast_result(
    result: ForecastResult,
    output_dir: Path,
) -> Path:
    """예측 결과를 CSV로 저장한다."""
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame({
        "ts": result.y_true.index,
        "y_true": result.y_true.values,
        "y_pred": result.y_pred.values,
        "error": (result.y_true.values - result.y_pred.values),
    })

    filename = f"forecast_{result.model_name}_{result.meter_urn.replace('.', '_')}_{result.measurement}.csv"
    path = output_dir / filename
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("예측 결과 저장: %s", path)
    return path


def save_forecast_comparison(
    results: list[ForecastResult],
    output_dir: Path,
) -> Path:
    """모델별 성능 비교 요약을 저장한다."""
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for r in results:
        rows.append({
            "model": r.model_name,
            "meter_urn": r.meter_urn,
            "measurement": r.measurement,
            "train_period": r.train_period,
            "eval_period": r.eval_period,
            "mae": round(r.mae, 4),
            "rmse": round(r.rmse, 4),
            "mape": round(r.mape, 4),
            "created_at": r.created_at,
        })

    df = pd.DataFrame(rows).sort_values("rmse")
    path = output_dir / "forecast_comparison.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("모델 비교 저장: %s", path)
    logger.info("\n%s", df[["model", "mae", "rmse", "mape"]].to_string(index=False))
    return path