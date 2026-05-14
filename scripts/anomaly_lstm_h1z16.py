from __future__ import annotations

import random
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import StandardScaler
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
LOSS_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_lstm_loss.png"
ANOMALY_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_lstm_anomaly.png"
RESULT_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "h1z16_lstm_results.csv"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from preprocess_h1z16 import preprocess_h1z16


FEATURE_COLUMNS = [
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
WINDOW_SIZE = 24
BATCH_SIZE = 64
EPOCHS = 30
PATIENCE = 5
LEARNING_RATE = 1e-3
RANDOM_STATE = 42


class LSTMAutoencoder(nn.Module):
    def __init__(self, n_features: int, seq_len: int) -> None:
        super().__init__()
        self.seq_len = seq_len
        self.n_features = n_features

        self.encoder_lstm1 = nn.LSTM(
            input_size=n_features,
            hidden_size=64,
            batch_first=True,
        )
        self.encoder_lstm2 = nn.LSTM(
            input_size=64,
            hidden_size=32,
            batch_first=True,
        )

        self.decoder_lstm1 = nn.LSTM(
            input_size=32,
            hidden_size=32,
            batch_first=True,
        )
        self.decoder_lstm2 = nn.LSTM(
            input_size=32,
            hidden_size=64,
            batch_first=True,
        )
        self.output_layer = nn.Linear(64, n_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        encoded, _ = self.encoder_lstm1(x)
        _, (hidden, _) = self.encoder_lstm2(encoded)

        repeated = hidden[-1].unsqueeze(1).repeat(1, self.seq_len, 1)
        decoded, _ = self.decoder_lstm1(repeated)
        decoded, _ = self.decoder_lstm2(decoded)

        return self.output_layer(decoded)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def load_lstm_input() -> tuple[pd.DataFrame, list[str]]:
    df, _, _, _ = preprocess_h1z16(print_progress=False, print_issue_details=False)
    df = df.loc[df["is_valid"]].copy()
    df = df.dropna(subset=FEATURE_COLUMNS).copy()
    df = df.sort_values("ts").reset_index(drop=True)
    return df, FEATURE_COLUMNS


def scale_features(df: pd.DataFrame, feature_columns: list[str]) -> tuple[pd.DataFrame, StandardScaler]:
    scaler = StandardScaler()
    scaled = scaler.fit_transform(df[feature_columns])
    scaled_df = df.copy()
    scaled_df[feature_columns] = pd.DataFrame(
        scaled,
        index=scaled_df.index,
        columns=feature_columns,
    )
    return scaled_df, scaler


def create_sequences(
    df: pd.DataFrame,
    feature_columns: list[str],
    window_size: int,
    raw_p_values: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    values = df[feature_columns].to_numpy(dtype=np.float32)
    ts_values = df["ts"].to_numpy()
    p_values = raw_p_values if raw_p_values is not None else df["P"].to_numpy()

    sequences = []
    seq_ts = []
    seq_p = []

    for end_idx in range(window_size - 1, len(df)):
        start_idx = end_idx - window_size + 1
        sequences.append(values[start_idx : end_idx + 1])
        seq_ts.append(ts_values[end_idx])
        seq_p.append(p_values[end_idx])

    return (
        np.asarray(sequences, dtype=np.float32),
        np.asarray(seq_ts),
        np.asarray(seq_p),
    )


def split_sequences(
    sequences: np.ndarray,
    seq_ts: np.ndarray,
    seq_p: np.ndarray,
) -> dict[str, np.ndarray]:
    split_idx = int(len(sequences) * 0.8)

    return {
        "train_seq": sequences[:split_idx],
        "test_seq": sequences[split_idx:],
        "train_ts": seq_ts[:split_idx],
        "test_ts": seq_ts[split_idx:],
        "train_p": seq_p[:split_idx],
        "test_p": seq_p[split_idx:],
    }


def build_dataloaders(train_seq: np.ndarray) -> tuple[DataLoader, DataLoader]:
    val_start = int(len(train_seq) * 0.9)
    train_main = train_seq[:val_start]
    val_seq = train_seq[val_start:]

    train_dataset = TensorDataset(torch.from_numpy(train_main), torch.from_numpy(train_main))
    val_dataset = TensorDataset(torch.from_numpy(val_seq), torch.from_numpy(val_seq))

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
            reconstructed = model(batch_x)
            loss = criterion(reconstructed, batch_y)

            if is_train:
                loss.backward()
                optimizer.step()

        batch_size = batch_x.size(0)
        total_loss += loss.item() * batch_size
        total_count += batch_size

    return total_loss / max(total_count, 1)


def train_model(
    train_seq: np.ndarray,
    n_features: int,
    print_epoch_logs: bool = True,
) -> tuple[LSTMAutoencoder, dict[str, list[float]]]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = LSTMAutoencoder(n_features=n_features, seq_len=WINDOW_SIZE).to(device)
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)

    train_loader, val_loader = build_dataloaders(train_seq)
    history = {"train_loss": [], "val_loss": []}

    best_state = None
    best_val_loss = float("inf")
    patience_count = 0

    for epoch in range(1, EPOCHS + 1):
        train_loss = run_epoch(model, train_loader, criterion, optimizer, device)
        val_loss = run_epoch(model, val_loader, criterion, None, device)

        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)

        if print_epoch_logs and (epoch % 5 == 0 or epoch == 1):
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


def reconstruction_errors(model: nn.Module, sequences: np.ndarray) -> np.ndarray:
    device = next(model.parameters()).device
    model.eval()

    dataset = TensorDataset(torch.from_numpy(sequences), torch.from_numpy(sequences))
    dataloader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=False)
    errors = []

    with torch.no_grad():
        for batch_x, batch_y in dataloader:
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)
            reconstructed = model(batch_x)
            batch_error = torch.mean((reconstructed - batch_y) ** 2, dim=(1, 2))
            errors.extend(batch_error.detach().cpu().numpy())

    return np.asarray(errors)


def save_loss_plot(history: dict[str, list[float]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(10, 5))
    plt.plot(history["train_loss"], label="train_loss", color="steelblue")
    plt.plot(history["val_loss"], label="val_loss", color="orange")
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.title("H1.Z16 LSTM Autoencoder Loss")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def save_anomaly_plot(df: pd.DataFrame, threshold: float, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    anomaly_df = df.loc[df["anomaly_lstm"]]

    fig, axes = plt.subplots(2, 1, figsize=(16, 8), sharex=True)

    axes[0].plot(df["ts"], df["P"], color="steelblue", linewidth=1, label="P")
    axes[0].scatter(
        anomaly_df["ts"],
        anomaly_df["P"],
        color="red",
        s=10,
        label="Anomaly",
        zorder=3,
    )
    axes[0].set_title("H1.Z16 P with LSTM Autoencoder Anomalies")
    axes[0].set_ylabel("P")
    axes[0].legend()

    axes[1].plot(df["ts"], df["reconstruction_error"], color="gray", linewidth=1, label="Reconstruction Error")
    axes[1].axhline(threshold, color="red", linestyle="--", linewidth=1, label="Threshold")
    axes[1].set_title("Reconstruction Error")
    axes[1].set_xlabel("ts")
    axes[1].set_ylabel("Error")
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


def save_results(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)


def run_lstm(
    df: pd.DataFrame | None = None,
    feature_cols: list[str] | None = None,
    save_plot_files: bool = True,
    print_epoch_logs: bool = True,
) -> tuple[pd.DataFrame, list[str], dict[str, list[float]], float]:
    set_seed(RANDOM_STATE)

    if df is None:
        df, feature_columns = load_lstm_input()
    else:
        feature_columns = feature_cols if feature_cols is not None else FEATURE_COLUMNS
        df = df.loc[df["is_valid"]].copy() if "is_valid" in df.columns else df.copy()
        df = df.dropna(subset=feature_columns).copy()
        df = df.sort_values("ts").reset_index(drop=True)
    scaled_df, _ = scale_features(df, feature_columns)

    sequences, seq_ts, seq_p = create_sequences(
        scaled_df,
        feature_columns,
        WINDOW_SIZE,
        raw_p_values=df["P"].to_numpy(),
    )
    split = split_sequences(sequences, seq_ts, seq_p)

    train_seq = split["train_seq"]
    test_seq = split["test_seq"]

    model, history = train_model(
        train_seq,
        n_features=len(feature_columns),
        print_epoch_logs=print_epoch_logs,
    )
    final_train_loss = history["train_loss"][-1]
    final_val_loss = history["val_loss"][-1]

    test_errors = reconstruction_errors(model, test_seq)
    threshold = test_errors.mean() + 2 * test_errors.std()
    anomaly_mask = test_errors > threshold

    result_df = pd.DataFrame(
        {
            "ts": pd.to_datetime(split["test_ts"]),
            "P": split["test_p"],
            "reconstruction_error": test_errors,
            "anomaly_lstm": anomaly_mask,
        }
    )

    full_result_df = df[["ts", "P"]].copy()
    full_result_df["reconstruction_error"] = np.nan
    full_result_df["anomaly_lstm"] = False
    full_result_df = full_result_df.merge(
        result_df,
        on=["ts", "P"],
        how="left",
        suffixes=("", "_test"),
    )
    full_result_df["reconstruction_error"] = full_result_df["reconstruction_error_test"].combine_first(
        full_result_df["reconstruction_error"]
    )
    full_result_df["anomaly_lstm"] = full_result_df["anomaly_lstm_test"].fillna(
        full_result_df["anomaly_lstm"]
    )
    full_result_df = full_result_df.drop(columns=["reconstruction_error_test", "anomaly_lstm_test"])

    if save_plot_files:
        save_loss_plot(history, LOSS_OUTPUT_PATH)
        save_anomaly_plot(result_df, threshold, ANOMALY_OUTPUT_PATH)

    return full_result_df, feature_columns, history, threshold


def main() -> None:
    full_result_df, feature_columns, history, threshold = run_lstm(
        df=None,
        feature_cols=None,
        save_plot_files=True,
        print_epoch_logs=True,
    )
    save_results(
        full_result_df[["ts", "P", "reconstruction_error", "anomaly_lstm"]],
        RESULT_OUTPUT_PATH,
    )
    result_df = full_result_df.loc[full_result_df["reconstruction_error"].notna()].copy()
    train_rows = len(full_result_df) - len(result_df)
    test_rows = len(result_df)

    print(f"사용된 feature 수: {len(feature_columns)}")
    print(f"train/test 행 수: {train_rows} / {test_rows}")

    final_train_loss = history["train_loss"][-1]
    final_val_loss = history["val_loss"][-1]

    anomaly_df = result_df.loc[result_df["anomaly_lstm"]].copy()
    anomaly_df = anomaly_df.sort_values("reconstruction_error", ascending=False)
    anomaly_ratio = len(anomaly_df) / len(result_df) if len(result_df) else 0.0

    print(f"최종 epoch loss: train={final_train_loss:.6f}, val={final_val_loss:.6f}")
    print(f"threshold 값: {threshold:.6f}")
    print(f"이상 탐지 개수: {len(anomaly_df)} ({anomaly_ratio:.2%})")
    print("이상 구간 상위 10개:")
    print(anomaly_df[["ts", "P", "reconstruction_error"]].head(10).to_string(index=False))

    print(f"플롯 저장: {LOSS_OUTPUT_PATH}")
    print(f"플롯 저장: {ANOMALY_OUTPUT_PATH}")
    print(f"결과 저장: {RESULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
