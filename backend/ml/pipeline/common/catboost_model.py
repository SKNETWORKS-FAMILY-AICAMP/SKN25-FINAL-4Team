"""v53 CatBoost flattened-window 모델."""
from __future__ import annotations

import numpy as np
from catboost import CatBoostRegressor

ITERATIONS = 600
LEARNING_RATE = 0.05
DEPTH = 6
L2_LEAF_REG = 3.0
EARLY_STOPPING_ROUNDS = 50


def flatten_windows(x: np.ndarray) -> np.ndarray:
    return x.reshape(x.shape[0], x.shape[1] * x.shape[2])


def fit_catboost(bundle, horizon: int, seed: int = 42) -> CatBoostRegressor:
    x_train = flatten_windows(bundle.x_train)
    x_val = flatten_windows(bundle.x_val)
    y_train = bundle.y_train.ravel() if horizon == 1 else bundle.y_train
    y_val = bundle.y_val.ravel() if horizon == 1 else bundle.y_val
    loss = "RMSE" if horizon == 1 else "MultiRMSE"

    model = CatBoostRegressor(
        loss_function=loss,
        eval_metric=loss,
        iterations=ITERATIONS,
        learning_rate=LEARNING_RATE,
        depth=DEPTH,
        l2_leaf_reg=L2_LEAF_REG,
        random_seed=seed,
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        allow_writing_files=False,
        verbose=False,
        thread_count=-1,
    )
    model.fit(x_train, y_train, eval_set=(x_val, y_val), use_best_model=True)
    return model


def predict_catboost_scaled(model: CatBoostRegressor, x: np.ndarray, horizon: int) -> np.ndarray:
    pred = np.asarray(model.predict(flatten_windows(x)), dtype=np.float32)
    if horizon == 1:
        pred = pred.reshape(-1, 1)
    return pred
