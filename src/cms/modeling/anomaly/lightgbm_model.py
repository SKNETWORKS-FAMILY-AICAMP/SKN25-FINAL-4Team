"""v61 LightGBM flattened-window 모델. horizon step별 독립 모델."""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore", message="X does not have valid feature names")

N_ESTIMATORS          = 500
LEARNING_RATE_LGB     = 0.05
NUM_LEAVES            = 31
MAX_DEPTH             = -1
SUBSAMPLE             = 0.9
COLSAMPLE_BYTREE      = 0.9
REG_LAMBDA            = 3.0
EARLY_STOPPING_ROUNDS = 50


def _flatten(x: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(x.reshape(x.shape[0], x.shape[1] * x.shape[2]))


def fit_lightgbm(bundle, horizon: int, seed: int = 42) -> list:
    """horizon step별 독립 LGBMRegressor 학습."""
    from lightgbm import LGBMRegressor, early_stopping, log_evaluation

    x_tr = _flatten(bundle.x_train)
    x_va = _flatten(bundle.x_val)
    models = []
    for step in range(horizon):
        m = LGBMRegressor(
            objective="regression_l2", metric="rmse",
            n_estimators=N_ESTIMATORS, learning_rate=LEARNING_RATE_LGB,
            num_leaves=NUM_LEAVES, max_depth=MAX_DEPTH,
            subsample=SUBSAMPLE, colsample_bytree=COLSAMPLE_BYTREE,
            reg_lambda=REG_LAMBDA, random_state=seed,
            n_jobs=-1, verbosity=-1,
        )
        m.fit(
            x_tr, np.ascontiguousarray(bundle.y_train[:, step]),
            eval_set=[(x_va, np.ascontiguousarray(bundle.y_val[:, step]))],
            eval_metric="rmse",
            callbacks=[
                early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                log_evaluation(0),
            ],
        )
        models.append(m)
    return models


def predict_lightgbm_scaled(models: list, x: np.ndarray) -> np.ndarray:
    flat = _flatten(x)
    preds = []
    for m in models:
        best_iter = getattr(m, "best_iteration_", None) or None
        preds.append(m.predict(flat, num_iteration=best_iter))
    return np.column_stack(preds).astype(np.float32)


def save_lightgbm(models: list, meter_urn: str, horizon: int, artifacts_dir: Path) -> None:
    d = artifacts_dir / f"{horizon}h" / meter_urn
    d.mkdir(parents=True, exist_ok=True)
    for i, m in enumerate(models, start=1):
        m.booster_.save_model(str(d / f"lightgbm_t_plus_{i}.txt"))
