#!/usr/bin/env python3
"""Train an LSTM forecast model for EMS grid_P with optional MLflow logging."""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "outputs" / "team_1h_dataset"
OUT_BASE = PROJECT_DIR / "outputs" / "forecast_lstm"
FEATURE_COLS = [
    "grid_P", "pv_P", "chp_P", "Ta", "Igm",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos",
]
TARGET_COL = "grid_P"


@dataclass
class SequencePack:
    x: np.ndarray
    y: np.ndarray
    ts: np.ndarray
    split: np.ndarray


class LSTMRegressor(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, num_layers: int, dropout: float) -> None:
        super().__init__()
        effective_dropout = dropout if num_layers > 1 else 0.0
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=effective_dropout)
        mid = max(1, hidden_size // 2)
        self.head = nn.Sequential(nn.Linear(hidden_size, mid), nn.ReLU(), nn.Linear(mid, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :]).squeeze(-1)


def make_sequences(df: pd.DataFrame, seq_len: int, horizon: int) -> tuple[SequencePack, np.ndarray]:
    values = df[FEATURE_COLS].to_numpy(dtype=np.float32)
    target = df[TARGET_COL].to_numpy(dtype=np.float32)
    ts = df["ts"].to_numpy()
    split = df["split"].to_numpy()
    outage = df["is_gateway_outage"].to_numpy(dtype=bool)
    target_observed = df[f"{TARGET_COL}_observed"].to_numpy(dtype=bool)

    xs, ys, out_ts, out_split, valid_train = [], [], [], [], []
    for y_idx in range(seq_len + horizon - 1, len(df)):
        x_start = y_idx - horizon - seq_len + 1
        x_end = y_idx - horizon + 1
        input_splits = split[x_start:x_end]
        if len(set(input_splits.tolist() + [split[y_idx]])) != 1:
            continue
        if not target_observed[y_idx]:
            continue
        is_valid_train = bool(split[y_idx] == "train" and not outage[x_start : y_idx + 1].any())
        xs.append(values[x_start:x_end])
        ys.append(target[y_idx])
        out_ts.append(ts[y_idx])
        out_split.append(split[y_idx])
        valid_train.append(is_valid_train)
    return SequencePack(np.stack(xs).astype(np.float32), np.array(ys, dtype=np.float32), np.array(out_ts), np.array(out_split)), np.array(valid_train, dtype=bool)


def inverse_grid(scaled: np.ndarray, scaler) -> np.ndarray:
    idx = FEATURE_COLS.index(TARGET_COL)
    data_min = float(scaler.data_min_[idx])
    data_max = float(scaler.data_max_[idx])
    return scaled * (data_max - data_min) + data_min


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    err = y_pred - y_true
    return {
        "MAE": float(np.mean(np.abs(err))),
        "RMSE": float(np.sqrt(np.mean(err ** 2))),
        "MAPE": float(np.mean(np.abs(err) / np.maximum(np.abs(y_true), 1.0)) * 100),
    }


def log_mlflow(args, out_dir: Path, result_metrics: dict) -> str | None:
    if not args.mlflow_uri:
        return None
    import mlflow

    mlflow.set_tracking_uri(args.mlflow_uri)
    mlflow.set_experiment(args.mlflow_experiment)
    with mlflow.start_run(run_name=args.run_name):
        run_id = mlflow.active_run().info.run_id
        mlflow.log_params({
            "target": "grid_P_next_hour_consumption_import",
            "target_definition": "grid_P=max(raw signed grid transformer aggregate P,0); horizon=t+1",
            "resolution": "1h",
            "source_relation": "ems.reduced_measurement_1h derived from ems.cr_measurement_1h",
            "train_period": "2018-2021 excluding gateway outage sequences",
            "validation_period": "2022",
            "test_period": "2023",
            "features": ",".join(FEATURE_COLS),
            "scaler": "MinMaxScaler(feature_range=(0,1), clip=True)",
            "missing": "fill_0",
            "model_family": "LSTM",
            "seq_len": args.seq_len,
            "horizon": args.horizon,
            "hidden_size": args.hidden_size,
            "num_layers": args.num_layers,
            "dropout": args.dropout,
            "learning_rate": args.lr,
            "batch_size": args.batch_size,
            "epochs": args.epochs,
        })
        for split in ["val", "test"]:
            for k, v in result_metrics[split].items():
                mlflow.log_metric(f"{split}_{k}", float(v))
        mlflow.log_metric("train_time_sec", float(result_metrics["train_time_sec"]))
        mlflow.log_metric("train_sequence_count", int(result_metrics["train_sequence_count"]))
        mlflow.log_metric("validation_sequence_count", int(result_metrics["validation_sequence_count"]))
        mlflow.log_metric("test_sequence_count", int(result_metrics["test_sequence_count"]))
        for path in ["metrics.json", "loss_history.csv", "predictions_validation.parquet", "predictions_test.parquet", "model.pt"]:
            mlflow.log_artifact(str(out_dir / path), artifact_path="grid_forecast")
        mlflow.log_artifact(str(DATA_DIR / "dataset_metadata.json"), artifact_path="grid_forecast")
    return run_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seq-len", type=int, default=24)
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--hidden-size", type=int, default=64)
    parser.add_argument("--num-layers", type=int, default=2)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--run-name", default="grid_lstm_seq24")
    parser.add_argument("--mlflow-uri", default="")
    parser.add_argument("--mlflow-experiment", default="SSA-IPSO-LSTM")
    args = parser.parse_args()

    out_dir = OUT_BASE / args.run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(DATA_DIR / "team_1h_features_scaled.parquet")
    scaler = joblib.load(DATA_DIR / "minmax_scaler.pkl")
    pack, valid_train = make_sequences(df, args.seq_len, args.horizon)
    train_mask = (pack.split == "train") & valid_train
    val_mask = pack.split == "validation"
    test_mask = pack.split == "test"

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = LSTMRegressor(len(FEATURE_COLS), args.hidden_size, args.num_layers, args.dropout).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    criterion = nn.MSELoss()
    train_ds = TensorDataset(torch.from_numpy(pack.x[train_mask]), torch.from_numpy(pack.y[train_mask]))
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)
    val_x = torch.from_numpy(pack.x[val_mask]).to(device)
    val_y = torch.from_numpy(pack.y[val_mask]).to(device)

    best_val, best_state, patience_left = float("inf"), None, args.patience
    history = []
    start_time = time.time()
    for epoch in range(1, args.epochs + 1):
        model.train(); losses = []
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(xb), yb)
            loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0); optimizer.step()
            losses.append(float(loss.detach().cpu()))
        model.eval()
        with torch.no_grad():
            val_loss = float(criterion(model(val_x), val_y).detach().cpu())
        train_loss = float(np.mean(losses))
        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss})
        print(f"epoch={epoch} train_loss={train_loss:.6f} val_loss={val_loss:.6f}", flush=True)
        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            patience_left = args.patience
        else:
            patience_left -= 1
            if patience_left <= 0:
                print("early_stop", flush=True); break
    train_time = time.time() - start_time
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()

    def predict(mask: np.ndarray) -> np.ndarray:
        preds = []
        loader = DataLoader(torch.from_numpy(pack.x[mask]), batch_size=args.batch_size, shuffle=False)
        with torch.no_grad():
            for xb in loader:
                preds.append(model(xb.to(device)).detach().cpu().numpy())
        return np.concatenate(preds)

    val_pred_s, test_pred_s = predict(val_mask), predict(test_mask)
    val_true, val_pred = inverse_grid(pack.y[val_mask], scaler), inverse_grid(val_pred_s, scaler)
    test_true, test_pred = inverse_grid(pack.y[test_mask], scaler), inverse_grid(test_pred_s, scaler)

    result_metrics = {
        "val": metrics(val_true, val_pred),
        "test": metrics(test_true, test_pred),
        "train_sequence_count": int(train_mask.sum()),
        "validation_sequence_count": int(val_mask.sum()),
        "test_sequence_count": int(test_mask.sum()),
        "train_time_sec": train_time,
        "device": str(device),
        "params": vars(args),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "metrics.json").write_text(json.dumps(result_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(history).to_csv(out_dir / "loss_history.csv", index=False)
    torch.save({"model_state_dict": model.state_dict(), "params": vars(args), "feature_cols": FEATURE_COLS}, out_dir / "model.pt")
    pd.DataFrame({"ts": pack.ts[val_mask], "y_true": val_true, "y_pred": val_pred}).to_parquet(out_dir / "predictions_validation.parquet", index=False)
    pd.DataFrame({"ts": pack.ts[test_mask], "y_true": test_true, "y_pred": test_pred}).to_parquet(out_dir / "predictions_test.parquet", index=False)
    run_id = log_mlflow(args, out_dir, result_metrics)
    result_metrics["mlflow_run_id"] = run_id
    (out_dir / "metrics.json").write_text(json.dumps(result_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print("forecast_done")
    print(json.dumps(result_metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
