"""v10/v12/v15 앙상블 헬퍼. test2 의존 없음. train.py에서 인라인으로 대체됐으나 참조용 유지."""
from __future__ import annotations

import numpy as np

from energy_v84.common.config import LSTM_VARIANTS, SMOOTH_L1_BETA
from energy_v84.common.preprocessing import DatasetBundle
from energy_v84.common.model import (
    RecurrentPredictor,
    evaluate_objective_scaled,
    make_loss_fn,
    predict_scaled,
    train_lstm,
    HIDDEN_SIZE,
    LEARNING_RATE,
    PATIENCE,
)


def v10_ensemble(lstm_results: dict, bundle: DatasetBundle, top_k: int = 2) -> dict:
    y_val, y_test = bundle.y_val, bundle.y_test
    scores = {v: float(np.mean(np.abs(r["val_pred"] - y_val))) for v, r in lstm_results.items()}
    top = sorted(scores.items(), key=lambda x: (x[1], x[0]))[:top_k]
    inv = np.array([1.0 / max(m, 1e-9) for _, m in top])
    w = inv / inv.sum()
    vp = sum(w[i] * lstm_results[v]["val_pred"]  for i, (v, _) in enumerate(top))
    tp = sum(w[i] * lstm_results[v]["test_pred"] for i, (v, _) in enumerate(top))
    return {"val_pred": vp, "test_pred": tp}


def _stepwise_topk(lstm_results, y_val, y_test, top_k):
    horizon = y_val.shape[1]
    vp = np.zeros_like(y_val)
    tp = np.zeros_like(y_test)
    for step in range(horizon):
        scores = {v: float(np.mean(np.abs(r["val_pred"][:, step] - y_val[:, step])))
                  for v, r in lstm_results.items()}
        top = sorted(scores.items(), key=lambda x: (x[1], x[0]))[:top_k]
        versions, maes = zip(*top)
        inv = np.array([1.0 / max(m, 1e-9) for m in maes])
        w = inv / inv.sum()
        for i, v in enumerate(versions):
            vp[:, step] += w[i] * lstm_results[v]["val_pred"][:, step]
            tp[:, step] += w[i] * lstm_results[v]["test_pred"][:, step]
    return vp, tp


def v12_ensemble(lstm_results, bundle):
    return {"val_pred": _stepwise_topk(lstm_results, bundle.y_val, bundle.y_test, 2)[0],
            "test_pred": _stepwise_topk(lstm_results, bundle.y_val, bundle.y_test, 2)[1]}


def v15_ensemble(lstm_results, bundle):
    return {"val_pred": _stepwise_topk(lstm_results, bundle.y_val, bundle.y_test, 3)[0],
            "test_pred": _stepwise_topk(lstm_results, bundle.y_val, bundle.y_test, 3)[1]}
