"""v71 daily seasonal naive — 입력창의 처음 horizon개 P값을 그대로 사용."""
from __future__ import annotations

import numpy as np

from ml.pipeline.common.config import USE_RESIDUAL_TARGET


def predict_seasonal_naive(bundle, split: str, horizon: int) -> np.ndarray:
    """
    입력창 x의 첫 번째 ~ horizon 번째 timestep의 P 값 (target feature index)을 예측으로 사용.
    pred_t_plus_k = input_window_P_at_index_{k-1}  (previous-day same-hour P)

    USE_RESIDUAL_TARGET=True 이면 절대값이 아닌 잔차로 반환:
      naive_residual = P(t-24+k) - P(t-1)  (anchor 기준 편차)
    복원은 build_prediction_df 에서 anchor를 더해 처리함.
    """
    target_idx = bundle.feature_columns.index("P")
    x = {"train": bundle.x_train, "val": bundle.x_val, "test": bundle.x_test}[split]
    naive_absolute = x[:, :horizon, target_idx].astype(np.float32)  # (N, horizon)

    if USE_RESIDUAL_TARGET:
        anchor = {
            "train": bundle.anchor_train,
            "val":   bundle.anchor_val,
            "test":  bundle.anchor_test,
        }[split]
        # anchor shape: (N,) → (N, 1), 모든 horizon step에 브로드캐스트
        return naive_absolute - anchor.reshape(-1, 1)

    return naive_absolute
