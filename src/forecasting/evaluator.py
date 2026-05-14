"""
예측 모델 평가 (MAE / RMSE / MAPE)
모델: Prophet / LSTM / XGBoost
분할: train 80% / test 20%
실험 추적: MLflow
"""
import numpy as np
import mlflow


def mean_absolute_error(y_true, y_pred) -> float:
    return float(np.mean(np.abs(np.array(y_true) - np.array(y_pred))))


def root_mean_squared_error(y_true, y_pred) -> float:
    return float(np.sqrt(np.mean((np.array(y_true) - np.array(y_pred)) ** 2)))


def mean_absolute_percentage_error(y_true, y_pred) -> float:
    y_true = np.array(y_true)
    y_pred = np.array(y_pred)
    mask = y_true != 0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def log_metrics(run_name: str, mae: float, rmse: float, mape: float):
    with mlflow.start_run(run_name=run_name):
        mlflow.log_metric("MAE", mae)
        mlflow.log_metric("RMSE", rmse)
        mlflow.log_metric("MAPE", mape)
