from __future__ import annotations

import random
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import torch
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import MinMaxScaler, StandardScaler
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
LOSS_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_pred_delta_loss.png"
RESULT_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_pred_delta_result.png"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from preprocess_h1z16 import preprocess_h1z16


FEATURE_CANDIDATES = [
    "P",
    "PF",
    "I1",
    "I2",
    "I3",
    "Ta",
    "Igm",
    "hour_sin",
    "hour_cos",
    "month_sin",
    "month_cos",
    "is_weekend",
]
TARGET_COLUMN = "delta_W"
WINDOW_SIZE = 24
BATCH_SIZE = 64
EPOCHS = 30
PATIENCE = 5
LEARNING_RATE = 1e-3
RANDOM_STATE = 42


class LSTMPredictor(nn.Module):
    def __init__(self, n_features: int) -> None:
        super().__init__()
        self.lstm1 = nn.LSTM(
            input_size=n_features,
            hidden_size=64,
            batch_first=True,
        )
        self.dropout1 = nn.Dropout(0.2)
        self.lstm2 = nn.LSTM(
            input_size=64,
            hidden_size=32,
            batch_first=True,
        )
        self.dropout2 = nn.Dropout(0.2)
        self.fc1 = nn.Linear(32, 16)
        self.relu = nn.ReLU()
        self.fc2 = nn.Linear(16, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm1(x)
        out = self.dropout1(out)
        out, (hidden, _) = self.lstm2(out)
        hidden = self.dropout2(hidden[-1])
        out = self.fc1(hidden)
        out = self.relu(out)
        return self.fc2(out)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def load_prediction_input() -> tuple[pd.DataFrame, list[str]]:
    df, _, _, _ = preprocess_h1z16(
        print_progress=False,
        print_issue_details=False,
    )
    df = df.loc[df["is_valid"]].copy()
    df = df.sort_values("ts").reset_index(drop=True)
    df[TARGET_COLUMN] = df["W"].diff()
    df.loc[df[TARGET_COLUMN] < 0, TARGET_COLUMN] = np.nan

    selected_features = []
    for column in FEATURE_CANDIDATES:
        if column not in df.columns:
            continue
        if df[column].isna().mean() <= 0.5:
            selected_features.append(column)

    required_columns = selected_features + ["W", TARGET_COLUMN]
    df = df.dropna(subset=required_columns).copy()
    df = df.reset_index(drop=True)
    return df, selected_features


def scale_data(
    df: pd.DataFrame,
    feature_columns: list[str],
) -> tuple[np.ndarray, np.ndarray, StandardScaler, MinMaxScaler]:
    x_scaler = StandardScaler()
    y_scaler = MinMaxScaler()

    x_scaled = x_scaler.fit_transform(df[feature_columns])
    y_scaled = y_scaler.fit_transform(df[[TARGET_COLUMN]])
    return x_scaled, y_scaled, x_scaler, y_scaler


def create_sequences(
    df: pd.DataFrame,
    x_scaled: np.ndarray,
    y_scaled: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    sequences = []
    targets = []
    target_ts = []
    actual_targets = []

    for start_idx in range(0, len(df) - WINDOW_SIZE):
        end_idx = start_idx + WINDOW_SIZE
        sequences.append(x_scaled[start_idx:end_idx])
        targets.append(y_scaled[end_idx, 0])
        target_ts.append(df["ts"].iloc[end_idx])
        actual_targets.append(df[TARGET_COLUMN].iloc[end_idx])

    return (
        np.asarray(sequences, dtype=np.float32),
        np.asarray(targets, dtype=np.float32).reshape(-1, 1),
        np.asarray(target_ts),
        np.asarray(actual_targets, dtype=np.float32),
    )


def split_sequences(
    X: np.ndarray,
    y: np.ndarray,
    target_ts: np.ndarray,
    actual_targets: np.ndarray,
) -> dict[str, np.ndarray]:
    split_idx = int(len(X) * 0.8)
    return {
        "X_train": X[:split_idx],
        "X_test": X[split_idx:],
        "y_train": y[:split_idx],
        "y_test": y[split_idx:],
        "ts_train": target_ts[:split_idx],
        "ts_test": target_ts[split_idx:],
        "actual_train": actual_targets[:split_idx],
        "actual_test": actual_targets[split_idx:],
    }


def build_dataloaders(X_train: np.ndarray, y_train: np.ndarray) -> tuple[DataLoader, DataLoader]:
    val_start = int(len(X_train) * 0.9)
    X_train_main = X_train[:val_start]
    y_train_main = y_train[:val_start]
    X_val = X_train[val_start:]
    y_val = y_train[val_start:]

    train_dataset = TensorDataset(torch.from_numpy(X_train_main), torch.from_numpy(y_train_main))
    val_dataset = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))

    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=False)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False)
    return train_loader, val_loader


def run_epoch(
    model: nn.Module,
    dataloader: DataLoader,
    criterion: nn.Module,
    optimizer: torch.optim.Optimizer | None,
    device: torch.device,
) -> float:
    is_train = optimizer is not None
    model.train(is_train)

    total_loss = 0.0
    total_count = 0

    for batch_x, batch_y in dataloader:
        batch_x = batch_x.to(device)
        batch_y = batch_y.to(device)

        if is_train:
            optimizer.zero_grad()

        with torch.set_grad_enabled(is_train):
            preds = model(batch_x)
            loss = criterion(preds, batch_y)
            if is_train:
                loss.backward()
                optimizer.step()

        batch_size = batch_x.size(0)
        total_loss += loss.item() * batch_size
        total_count += batch_size

    return total_loss / max(total_count, 1)


def train_model(X_train: np.ndarray, y_train: np.ndarray, n_features: int) -> tuple[nn.Module, dict[str, list[float]]]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = LSTMPredictor(n_features=n_features).to(device)
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)

    train_loader, val_loader = build_dataloaders(X_train, y_train)
    history = {"train_loss": [], "val_loss": []}

    best_state = None
    best_val_loss = float("inf")
    patience_count = 0

    for epoch in range(1, EPOCHS + 1):
        train_loss = run_epoch(model, train_loader, criterion, optimizer, device)
        val_loss = run_epoch(model, val_loader, criterion, None, device)

        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)

        if epoch % 5 == 0 or epoch == 1:
            print(f"epoch {epoch}: train_loss={train_loss:.6f}, val_loss={val_loss:.6f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            patience_count = 0
        else:
            patience_count += 1

        if patience_count >= PATIENCE:
            break

    if best_state is not None:
        model.load_state_dict(best_state)

    return model, history


def predict(model: nn.Module, X: np.ndarray) -> np.ndarray:
    device = next(model.parameters()).device
    model.eval()

    dataset = TensorDataset(torch.from_numpy(X))
    dataloader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=False)
    preds = []

    with torch.no_grad():
        for (batch_x,) in dataloader:
            batch_x = batch_x.to(device)
            batch_preds = model(batch_x)
            preds.extend(batch_preds.detach().cpu().numpy().reshape(-1))

    return np.asarray(preds, dtype=np.float32).reshape(-1, 1)


def calculate_metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    mae = mean_absolute_error(actual, pred)
    rmse = float(np.sqrt(mean_squared_error(actual, pred)))
    nonzero_mask = actual != 0
    mape = float(np.mean(np.abs((actual[nonzero_mask] - pred[nonzero_mask]) / actual[nonzero_mask])) * 100)
    return {"mae": float(mae), "rmse": rmse, "mape": mape}


def save_loss_plot(history: dict[str, list[float]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(10, 5))
    plt.plot(history["train_loss"], label="train_loss", color="steelblue")
    plt.plot(history["val_loss"], label="val_loss", color="orange")
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.title("H1.Z16 Prediction Loss")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def save_result_plot(result_df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 1, figsize=(16, 8), sharex=True)

    axes[0].plot(result_df["ts"], result_df["actual_delta_W"], label="Actual", color="steelblue", linewidth=1)
    axes[0].plot(result_df["ts"], result_df["pred_delta_W"], label="Predicted", color="orange", linewidth=1)
    axes[0].set_title("H1.Z16 delta_W Actual vs Predicted")
    axes[0].set_ylabel("delta_W")
    axes[0].legend()

    axes[1].plot(result_df["ts"], result_df["actual_delta_W"] - result_df["pred_delta_W"], color="gray", linewidth=1)
    axes[1].axhline(0, color="red", linestyle="--", linewidth=1)
    axes[1].set_title("Prediction Error (Actual - Predicted)")
    axes[1].set_xlabel("ts")
    axes[1].set_ylabel("Error")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


def run_prediction() -> tuple[pd.DataFrame, list[str], dict[str, list[float]], dict[str, float], int, int]:
    set_seed(RANDOM_STATE)

    df, feature_columns = load_prediction_input()
    x_scaled, y_scaled, _, y_scaler = scale_data(df, feature_columns)
    X, y, target_ts, actual_targets = create_sequences(df, x_scaled, y_scaled)
    split = split_sequences(X, y, target_ts, actual_targets)

    model, history = train_model(split["X_train"], split["y_train"], len(feature_columns))
    pred_scaled = predict(model, split["X_test"])

    pred_actual = y_scaler.inverse_transform(pred_scaled).reshape(-1)
    y_test_actual = y_scaler.inverse_transform(split["y_test"]).reshape(-1)
    metrics = calculate_metrics(y_test_actual, pred_actual)

    result_df = pd.DataFrame(
        {
            "ts": pd.to_datetime(split["ts_test"]),
            "actual_delta_W": y_test_actual,
            "pred_delta_W": pred_actual,
        }
    )

    save_loss_plot(history, LOSS_OUTPUT_PATH)
    save_result_plot(result_df, RESULT_OUTPUT_PATH)

    return result_df, feature_columns, history, metrics, len(split["X_train"]), len(split["X_test"])


def main() -> None:
    result_df, feature_columns, history, metrics, train_rows, test_rows = run_prediction()

    print(f"사용된 feature 수: {len(feature_columns)}")
    print(f"train/test 행 수: {train_rows} / {test_rows}")
    print(
        f"최종 epoch loss: train={history['train_loss'][-1]:.6f}, "
        f"val={history['val_loss'][-1]:.6f}"
    )
    print(
        f"MAE={metrics['mae']:.4f}, RMSE={metrics['rmse']:.4f}, "
        f"MAPE={metrics['mape']:.2f}%"
    )
    print("실제값 vs 예측값 상위 10개:")
    print(result_df.head(10).to_string(index=False))
    print(f"플롯 저장: {LOSS_OUTPUT_PATH}")
    print(f"플롯 저장: {RESULT_OUTPUT_PATH}")


def predict_meter(meter_urn: str, steps: int = 24) -> dict:
    import logging

    from preprocess_h1z16 import preprocess_meter

    logger = logging.getLogger(__name__)
    logger.info("%s predict_meter 시작", meter_urn)

    set_seed(RANDOM_STATE)

    df, _, _, _ = preprocess_meter(
        meter_urn,
        print_progress=False,
        print_issue_details=False,
    )
    df = df.loc[df["is_valid"]].copy()
    df = df.sort_values("ts").reset_index(drop=True)
    df[TARGET_COLUMN] = df["W"].diff()
    df.loc[df[TARGET_COLUMN] < 0, TARGET_COLUMN] = np.nan

    feature_columns = []
    for column in FEATURE_CANDIDATES:
        if column not in df.columns:
            continue
        if df[column].isna().mean() <= 0.5:
            feature_columns.append(column)

    required_columns = feature_columns + ["W", TARGET_COLUMN]
    df = df.dropna(subset=required_columns).copy().reset_index(drop=True)

    if len(feature_columns) == 0:
        raise ValueError(f"{meter_urn} has no usable prediction features")
    if len(df) <= WINDOW_SIZE:
        raise ValueError(f"{meter_urn} does not have enough rows for prediction")

    x_scaled, y_scaled, _, y_scaler = scale_data(df, feature_columns)
    X, y, target_ts, actual_targets = create_sequences(df, x_scaled, y_scaled)
    if len(X) == 0:
        raise ValueError(f"{meter_urn} does not have enough sequences for prediction")

    split = split_sequences(X, y, target_ts, actual_targets)
    model, history = train_model(split["X_train"], split["y_train"], len(feature_columns))
    pred_scaled = predict(model, split["X_test"])

    pred_actual = y_scaler.inverse_transform(pred_scaled).reshape(-1)
    y_test_actual = y_scaler.inverse_transform(split["y_test"]).reshape(-1)
    metrics = calculate_metrics(y_test_actual, pred_actual)

    future_window = x_scaled[-WINDOW_SIZE:].astype(np.float32).copy()
    future_predictions: list[dict[str, float]] = []

    for step_idx in range(1, steps + 1):
        next_scaled = predict(model, future_window[np.newaxis, :, :])[0, 0]
        next_actual = float(y_scaler.inverse_transform(np.array([[next_scaled]], dtype=np.float32))[0, 0])
        future_predictions.append(
            {
                "step": step_idx,
                "delta_w_pred": next_actual,
            }
        )

        next_feature_row = future_window[-1].copy()
        future_window = np.vstack([future_window[1:], next_feature_row])

    logger.info("%s predict_meter 완료 - steps: %s", meter_urn, steps)
    return {
        "meter_urn": meter_urn,
        "steps": steps,
        "unit": TARGET_COLUMN,
        "predictions": future_predictions,
        "model_metrics": metrics,
    }


if __name__ == "__main__":
    main()
