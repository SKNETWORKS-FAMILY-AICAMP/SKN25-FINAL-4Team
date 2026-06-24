"""LSTM/GRU 모델 정의 + 학습/추론 유틸리티. test2 의존 없음."""
from __future__ import annotations

import random

import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import StandardScaler
from torch import nn

from cms.modeling.anomaly.config import (
    BATCH_SIZE,
    HIDDEN_SIZE,
    LEARNING_RATE,
    MODEL_DROPOUT,
    PATIENCE,
    SMOOTH_L1_BETA,
    TRAINING_LOSS,
)
from cms.modeling.anomaly.preprocessing import DatasetBundle, make_loader


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def device() -> torch.device:
    return torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


class RecurrentPredictor(nn.Module):
    def __init__(
        self,
        input_size: int,
        hidden_size: int,
        output_size: int,
        model_architecture: str = "lstm",
        model_dropout: float = MODEL_DROPOUT,
    ) -> None:
        super().__init__()
        self.output_size = output_size
        self.model_architecture = model_architecture
        arch_map = {"lstm": nn.LSTM, "gru": nn.GRU}
        if model_architecture not in arch_map:
            raise ValueError(f"Unsupported architecture: {model_architecture}")
        self.recurrent = arch_map[model_architecture](
            input_size=input_size, hidden_size=hidden_size, batch_first=True
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Dropout(p=model_dropout),
            nn.Linear(hidden_size, output_size),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if isinstance(self.recurrent, nn.LSTM):
            _, (hidden, _) = self.recurrent(x)
        else:
            _, hidden = self.recurrent(x)
        return self.head(hidden[-1])


def make_loss_fn(
    training_loss: str = TRAINING_LOSS,
    smooth_l1_beta: float = SMOOTH_L1_BETA,
    reduction: str = "mean",
) -> nn.Module:
    if training_loss == "mse":
        return nn.MSELoss(reduction=reduction)
    if training_loss == "smooth_l1":
        return nn.SmoothL1Loss(beta=smooth_l1_beta, reduction=reduction)
    raise ValueError(f"Unsupported loss: {training_loss}")


def evaluate_objective_scaled(
    model: nn.Module,
    x: np.ndarray,
    y: np.ndarray,
    batch_size: int,
    dev: torch.device,
    training_loss: str = TRAINING_LOSS,
    smooth_l1_beta: float = SMOOTH_L1_BETA,
) -> float:
    model.eval()
    loss_fn = make_loss_fn(training_loss=training_loss, smooth_l1_beta=smooth_l1_beta, reduction="sum")
    total = 0.0
    with torch.no_grad():
        for xb, yb in make_loader(x, y, batch_size=batch_size, shuffle=False):
            total += float(loss_fn(model(xb.to(dev)), yb.to(dev)).cpu())
    return total / y.size


def train_lstm(
    bundle: DatasetBundle,
    variant,                  # LSTMVariant
    horizon: int,
    batch_size: int = BATCH_SIZE,
    epochs: int = 12,
) -> tuple[RecurrentPredictor, float, list[float]]:
    """LSTMVariant 기준으로 학습. (model, best_val, val_loss_history) 반환."""
    dev = device()
    model = RecurrentPredictor(
        input_size=len(bundle.feature_columns),
        hidden_size=HIDDEN_SIZE,
        output_size=horizon,
        model_architecture=variant.model_architecture,
        model_dropout=variant.model_dropout,
    ).to(dev)
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    loss_fn = make_loss_fn(training_loss=variant.training_loss, smooth_l1_beta=SMOOTH_L1_BETA, reduction="mean")
    best_val, best_state, patience_left = float("inf"), None, PATIENCE
    loader = make_loader(bundle.x_train, bundle.y_train, batch_size=batch_size, shuffle=True)
    val_loss_history: list[float] = []

    for _ in range(1, epochs + 1):
        model.train()
        for xb, yb in loader:
            xb, yb = xb.to(dev), yb.to(dev)
            optimizer.zero_grad()
            loss_fn(model(xb), yb).backward()
            optimizer.step()
        vl = evaluate_objective_scaled(
            model, bundle.x_val, bundle.y_val, batch_size, dev,
            training_loss=variant.training_loss, smooth_l1_beta=SMOOTH_L1_BETA,
        )
        vl_f = float(vl)
        val_loss_history.append(round(vl_f, 6) if not (vl_f != vl_f) else float("nan"))
        if vl_f == vl_f and vl_f < best_val:  # NaN-safe: NaN != NaN is True
            best_val = vl
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            patience_left = PATIENCE
        else:
            patience_left -= 1
            if patience_left <= 0:
                break

    if best_state:
        model.load_state_dict(best_state)
    return model, best_val, val_loss_history


def predict_scaled(model: RecurrentPredictor, x: np.ndarray, batch_size: int) -> np.ndarray:
    dev = next(model.parameters()).device
    model.eval()
    preds = []
    dummy = np.zeros((len(x), model.output_size), dtype=np.float32)
    with torch.no_grad():
        for xb, _ in make_loader(x, dummy, batch_size=batch_size, shuffle=False):
            preds.append(model(xb.to(dev)).cpu().numpy())
    return np.vstack(preds)


def inverse_target(scaler: StandardScaler, values: np.ndarray) -> np.ndarray:
    return scaler.inverse_transform(values.reshape(-1, 1)).reshape(values.shape)


def build_prediction_frame(meta: pd.DataFrame, actual: np.ndarray, pred: np.ndarray) -> pd.DataFrame:
    rows = meta.copy()
    abs_errors = []
    for idx in range(actual.shape[1]):
        step = idx + 1
        rows[f"actual_t_plus_{step}"] = actual[:, idx]
        rows[f"pred_t_plus_{step}"]   = pred[:, idx]
        rows[f"residual_t_plus_{step}"] = actual[:, idx] - pred[:, idx]
        rows[f"abs_error_t_plus_{step}"] = np.abs(actual[:, idx] - pred[:, idx])
        abs_errors.append(rows[f"abs_error_t_plus_{step}"].to_numpy())
    rows["anomaly_score_mae"] = np.mean(np.vstack(abs_errors), axis=0)
    return rows


def thresholds_from_validation(val_df: pd.DataFrame, quantile: float = 0.995) -> dict[str, float]:
    return {"threshold": float(val_df["anomaly_score_mae"].quantile(quantile))}


def add_anomaly_flags(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    result = df.copy()
    result["is_anomaly"] = result["anomaly_score_mae"] > threshold
    return result
