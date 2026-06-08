"""v67 Ridge flattened-window 모델."""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import Ridge

RIDGE_ALPHAS = (0.1, 1.0, 10.0, 100.0, 1000.0)


def flatten_windows(x: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(x.reshape(x.shape[0], x.shape[1] * x.shape[2]))


def fit_ridge(bundle, horizon: int, alphas=RIDGE_ALPHAS) -> tuple[Ridge, float]:
    """validation MAE 기준 최적 alpha Ridge 학습. 선택된 alpha 반환."""
    x_train = flatten_windows(bundle.x_train)
    x_val = flatten_windows(bundle.x_val)

    best_alpha = None
    best_mae = float("inf")
    for alpha in alphas:
        model = Ridge(alpha=alpha, fit_intercept=True, solver="lsqr")
        model.fit(x_train, bundle.y_train)
        pred = np.asarray(model.predict(x_val), dtype=np.float32)
        if pred.ndim == 1:
            pred = pred.reshape(-1, 1)
        mae = float(np.mean(np.abs(pred - bundle.y_val)))
        if mae < best_mae:
            best_mae = mae
            best_alpha = alpha

    model = Ridge(alpha=best_alpha, fit_intercept=True, solver="lsqr")
    model.fit(x_train, bundle.y_train)
    return model, best_alpha


def predict_ridge_scaled(model: Ridge, x: np.ndarray) -> np.ndarray:
    pred = np.asarray(model.predict(flatten_windows(x)), dtype=np.float32)
    if pred.ndim == 1:
        pred = pred.reshape(-1, 1)
    return pred
