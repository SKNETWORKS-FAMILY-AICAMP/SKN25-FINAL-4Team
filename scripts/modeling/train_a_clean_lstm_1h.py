#!/usr/bin/env python3
"""Independent LSTM baselines for EMS A-clean 1h targets.

The script trains one model per target_id. It reads only the A-clean Parquet cache and
never queries the database.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

TARGET_FILE = "target_timeseries_1h.parquet"
FEATURE_FILE = "feature_timeseries_1h.parquet"
SCALER_FILE = "scaler_manifest.json"
MANIFEST_FILE = "manifest.json"

DEFAULT_TARGETS = [
    "T1_group__central_cooling__P",
    "T1_group__local_cooling__P",
    "T1_group__server_power__P",
    "T1_group__ventilation__P",
]

CYCLIC_FEATURES = [
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "month_sin",
    "month_cos",
]
MODEL_FEATURES = [
    "target_scaled",
    "Ta_scaled",
    "Igm_scaled",
    "Ta_observed_float",
    "Igm_observed_float",
    *CYCLIC_FEATURES,
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def scale_values(values: pd.Series | np.ndarray, data_min: float, data_max: float, clip: bool = True) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float32)
    denom = float(data_max) - float(data_min)
    if denom <= 0:
        scaled = np.zeros_like(arr, dtype=np.float32)
    else:
        scaled = (arr - float(data_min)) / denom
    if clip:
        scaled = np.clip(scaled, 0.0, 1.0)
    return scaled.astype(np.float32)


def inverse_scale(values: np.ndarray, data_min: float, data_max: float) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float32)
    return arr * (float(data_max) - float(data_min)) + float(data_min)


def metrics(actual: np.ndarray, pred: np.ndarray, prefix: str, near_zero_threshold: float = 1e-6) -> dict[str, Any]:
    mask = np.isfinite(actual) & np.isfinite(pred)
    actual = actual[mask].astype(float)
    pred = pred[mask].astype(float)
    if actual.size == 0:
        return {
            f"{prefix}_rows": 0,
            f"{prefix}_mae": None,
            f"{prefix}_rmse": None,
            f"{prefix}_mape": None,
            f"{prefix}_mape_rows": 0,
            f"{prefix}_near_zero_rows": 0,
            f"{prefix}_bias": None,
        }
    err = pred - actual
    abs_err = np.abs(err)
    mape_mask = np.abs(actual) > near_zero_threshold
    return {
        f"{prefix}_rows": int(actual.size),
        f"{prefix}_mae": float(np.mean(abs_err)),
        f"{prefix}_rmse": float(math.sqrt(np.mean(np.square(err)))),
        f"{prefix}_mape": float(np.mean(abs_err[mape_mask] / np.abs(actual[mape_mask])) * 100.0) if np.any(mape_mask) else None,
        f"{prefix}_mape_rows": int(np.sum(mape_mask)),
        f"{prefix}_near_zero_rows": int(np.sum(~mape_mask)),
        f"{prefix}_bias": float(np.mean(err)),
    }


class LSTMRegressor(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, num_layers: int, dropout: float) -> None:
        super().__init__()
        lstm_dropout = dropout if num_layers > 1 else 0.0
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=lstm_dropout,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2 if hidden_size >= 2 else hidden_size),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size // 2 if hidden_size >= 2 else hidden_size, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :]).squeeze(-1)


@dataclass
class SequenceBundle:
    X: np.ndarray
    y_scaled: np.ndarray
    y_actual: np.ndarray
    ts: np.ndarray
    split: np.ndarray
    is_gateway_outage: np.ndarray
    target_version_id: np.ndarray


def make_sequences(df: pd.DataFrame, seq_len: int, horizon: int) -> SequenceBundle:
    df = df.sort_values("ts").reset_index(drop=True)
    feature_matrix = df[MODEL_FEATURES].to_numpy(dtype=np.float32)
    y_scaled_all = df["target_scaled"].to_numpy(dtype=np.float32)
    y_actual_all = df["target_value"].to_numpy(dtype=np.float32)
    usable = (
        df["target_observed"].fillna(False).to_numpy(dtype=bool)
        & df["is_full_component_observed"].fillna(False).to_numpy(dtype=bool)
        & (~df["is_replacement_gap"].fillna(False).to_numpy(dtype=bool))
    )
    gateway = df["is_gateway_outage"].fillna(False).to_numpy(dtype=bool)
    versions = df["target_version_id"].astype(str).to_numpy()
    splits = df["split"].astype(str).to_numpy()
    ts = df["ts"].to_numpy()

    X_list: list[np.ndarray] = []
    y_scaled_list: list[float] = []
    y_actual_list: list[float] = []
    ts_list: list[Any] = []
    split_list: list[str] = []
    gateway_list: list[bool] = []
    version_list: list[str] = []

    label_offset = seq_len + horizon - 1
    for start in range(0, len(df) - label_offset):
        end = start + seq_len
        label_idx = start + label_offset
        window_slice = slice(start, end)
        # Keep one semantic target definition per training/evaluation example.
        if not np.all(versions[window_slice] == versions[label_idx]):
            continue
        if not np.all(usable[window_slice]) or not usable[label_idx]:
            continue
        X_list.append(feature_matrix[window_slice])
        y_scaled_list.append(float(y_scaled_all[label_idx]))
        y_actual_list.append(float(y_actual_all[label_idx]))
        ts_list.append(ts[label_idx])
        split_list.append(str(splits[label_idx]))
        gateway_list.append(bool(gateway[label_idx]))
        version_list.append(str(versions[label_idx]))

    if not X_list:
        raise RuntimeError("No sequences created; check seq_len/horizon and quality filters")

    return SequenceBundle(
        X=np.stack(X_list).astype(np.float32),
        y_scaled=np.asarray(y_scaled_list, dtype=np.float32),
        y_actual=np.asarray(y_actual_list, dtype=np.float32),
        ts=np.asarray(ts_list),
        split=np.asarray(split_list),
        is_gateway_outage=np.asarray(gateway_list, dtype=bool),
        target_version_id=np.asarray(version_list),
    )


def loader_from_arrays(X: np.ndarray, y: np.ndarray, batch_size: int, shuffle: bool) -> DataLoader:
    ds = TensorDataset(torch.from_numpy(X), torch.from_numpy(y))
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle, drop_last=False, num_workers=0)


def run_epoch(model: nn.Module, loader: DataLoader, optimizer: torch.optim.Optimizer | None, device: torch.device) -> float:
    training = optimizer is not None
    model.train(training)
    criterion = nn.MSELoss()
    losses: list[float] = []
    with torch.set_grad_enabled(training):
        for xb, yb in loader:
            xb = xb.to(device)
            yb = yb.to(device)
            pred = model(xb)
            loss = criterion(pred, yb)
            if training:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
            losses.append(float(loss.detach().cpu().item()) * len(xb))
    denom = len(loader.dataset) if len(loader.dataset) else 1
    return float(sum(losses) / denom)


def predict(model: nn.Module, X: np.ndarray, batch_size: int, device: torch.device) -> np.ndarray:
    model.eval()
    preds: list[np.ndarray] = []
    loader = DataLoader(TensorDataset(torch.from_numpy(X)), batch_size=batch_size, shuffle=False)
    with torch.no_grad():
        for (xb,) in loader:
            out = model(xb.to(device)).detach().cpu().numpy()
            preds.append(out)
    return np.concatenate(preds).astype(np.float32)


def load_scalers(dataset_dir: Path) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    with (dataset_dir / SCALER_FILE).open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    target_scalers = {
        row["scaler_key"]: {"data_min": float(row["data_min"]), "data_max": float(row["data_max"])}
        for row in manifest["target_scalers"]
    }
    feature_scalers = {
        row["scaler_key"]: {"data_min": float(row["data_min"]), "data_max": float(row["data_max"])}
        for row in manifest["feature_scalers"]
    }
    return target_scalers, feature_scalers


def prepare_target_frame(dataset_dir: Path, target_id: str) -> tuple[pd.DataFrame, dict[str, float]]:
    target = pd.read_parquet(dataset_dir / TARGET_FILE)
    feature = pd.read_parquet(dataset_dir / FEATURE_FILE)
    target = target[target["target_id"] == target_id].copy()
    if target.empty:
        raise RuntimeError(f"target_id not found: {target_id}")
    target_scalers, feature_scalers = load_scalers(dataset_dir)
    if target_id not in target_scalers:
        raise RuntimeError(f"missing target scaler: {target_id}")
    scaler = target_scalers[target_id]
    df = target.merge(feature, on=["ts", "split", "is_gateway_outage", "gateway_outage_name"], how="left")
    df = df.sort_values("ts").reset_index(drop=True)

    df["target_scaled"] = scale_values(df["target_value"], scaler["data_min"], scaler["data_max"], clip=True)
    for feature_name in ["Ta", "Igm"]:
        fscaler = feature_scalers[feature_name]
        observed_col = f"{feature_name}_observed"
        scaled = scale_values(df[feature_name], fscaler["data_min"], fscaler["data_max"], clip=True)
        scaled = np.where(df[observed_col].fillna(False).to_numpy(dtype=bool), scaled, 0.0)
        df[f"{feature_name}_scaled"] = scaled.astype(np.float32)
        df[f"{feature_name}_observed_float"] = df[observed_col].fillna(False).astype(np.float32)
    for col in CYCLIC_FEATURES:
        df[col] = df[col].fillna(0.0).astype(np.float32)
    return df, scaler


def train_one_target(args: argparse.Namespace, target_id: str, device: torch.device) -> dict[str, Any]:
    dataset_dir = args.dataset_dir.resolve()
    out_dir = (args.out_dir / target_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    df, target_scaler = prepare_target_frame(dataset_dir, target_id)
    bundle = make_sequences(df, args.seq_len, args.horizon)

    train_mask = (bundle.split == "train") & (~bundle.is_gateway_outage)
    val_mask = (bundle.split == "validation") & (~bundle.is_gateway_outage)
    test_mask = bundle.split == "test"
    if train_mask.sum() == 0 or val_mask.sum() == 0:
        raise RuntimeError(f"insufficient train/validation sequences for {target_id}")

    train_loader = loader_from_arrays(bundle.X[train_mask], bundle.y_scaled[train_mask], args.batch_size, shuffle=True)
    val_loader = loader_from_arrays(bundle.X[val_mask], bundle.y_scaled[val_mask], args.batch_size, shuffle=False)

    model = LSTMRegressor(
        input_size=bundle.X.shape[-1],
        hidden_size=args.hidden_size,
        num_layers=args.num_layers,
        dropout=args.dropout,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)

    history: list[dict[str, Any]] = []
    best_val = float("inf")
    best_state: dict[str, torch.Tensor] | None = None
    bad_epochs = 0

    for epoch in range(1, args.epochs + 1):
        train_loss = run_epoch(model, train_loader, optimizer, device)
        val_loss = run_epoch(model, val_loader, None, device)
        row = {"epoch": epoch, "train_loss": train_loss, "validation_loss": val_loss}
        history.append(row)
        print(json.dumps({"target_id": target_id, **row}, ensure_ascii=False), flush=True)
        if val_loss < best_val - args.min_delta:
            best_val = val_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            bad_epochs = 0
        else:
            bad_epochs += 1
            if bad_epochs >= args.patience:
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    torch.save({
        "model_state_dict": model.state_dict(),
        "target_id": target_id,
        "model_features": MODEL_FEATURES,
        "params": vars(args),
        "target_scaler": target_scaler,
    }, out_dir / "model.pt")
    pd.DataFrame(history).to_csv(out_dir / "loss_history.csv", index=False)

    metrics_row: dict[str, Any] = {
        "target_id": target_id,
        "device": str(device),
        "epochs_ran": len(history),
        "best_validation_loss_scaled_mse": best_val,
        "sequence_count": int(len(bundle.y_scaled)),
        "train_sequences": int(train_mask.sum()),
        "validation_sequences": int((bundle.split == "validation").sum()),
        "validation_non_gateway_sequences": int(val_mask.sum()),
        "test_sequences": int(test_mask.sum()),
    }

    for split_name in ["validation", "test"]:
        mask = bundle.split == split_name
        if not np.any(mask):
            continue
        pred_scaled = predict(model, bundle.X[mask], args.batch_size, device)
        pred_scaled = np.clip(pred_scaled, 0.0, 1.0)
        pred = inverse_scale(pred_scaled, target_scaler["data_min"], target_scaler["data_max"])
        pred_df = pd.DataFrame({
            "ts": bundle.ts[mask],
            "target_id": target_id,
            "target_version_id": bundle.target_version_id[mask],
            "split": split_name,
            "actual": bundle.y_actual[mask],
            "prediction": pred,
            "prediction_scaled": pred_scaled,
            "is_gateway_outage": bundle.is_gateway_outage[mask],
        })
        pred_df.to_parquet(out_dir / f"predictions_{split_name}.parquet", index=False)
        metrics_row.update(metrics(pred_df["actual"].to_numpy(), pred_df["prediction"].to_numpy(), split_name))
        non_gateway_df = pred_df[~pred_df["is_gateway_outage"].fillna(False)]
        metrics_row.update(metrics(non_gateway_df["actual"].to_numpy(), non_gateway_df["prediction"].to_numpy(), f"{split_name}_non_gateway"))

    with (out_dir / "run_manifest.json").open("w", encoding="utf-8") as f:
        json.dump({
            "created_at_utc": utc_now(),
            "dataset_dir": str(dataset_dir),
            "out_dir": str(out_dir),
            "target_id": target_id,
            "model_features": MODEL_FEATURES,
            "params": vars(args),
            "target_scaler": target_scaler,
            "metrics": metrics_row,
        }, f, ensure_ascii=False, indent=2, default=str)

    return metrics_row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("outputs/modeling/a_clean_targets_1h"))
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/modeling/a_clean_lstm_1h"))
    parser.add_argument("--target-id", action="append", default=None, help="Target id to train; repeatable. Defaults to all A-clean targets.")
    parser.add_argument("--seq-len", type=int, default=24)
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--hidden-size", type=int, default=64)
    parser.add_argument("--num-layers", type=int, default=2)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--min-delta", type=float, default=1e-6)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    set_seed(args.seed)
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    targets = args.target_id or DEFAULT_TARGETS
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Save input dataset manifest snapshot path/hash if present.
    input_manifest: dict[str, Any] | None = None
    manifest_path = args.dataset_dir / MANIFEST_FILE
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as f:
            input_manifest = json.load(f)

    all_metrics = []
    for target_id in targets:
        all_metrics.append(train_one_target(args, target_id, device))

    metrics_df = pd.DataFrame(all_metrics)
    metrics_df.to_csv(args.out_dir / "lstm_metrics.csv", index=False)
    summary = {
        "created_at_utc": utc_now(),
        "dataset_dir": str(args.dataset_dir.resolve()),
        "out_dir": str(args.out_dir.resolve()),
        "targets": targets,
        "device": str(device),
        "params": vars(args),
        "model_features": MODEL_FEATURES,
        "input_manifest_target_family": input_manifest.get("target_family") if input_manifest else None,
        "metrics_path": "lstm_metrics.csv",
        "best_test_non_gateway_by_mae": metrics_df[[
            "target_id",
            "test_non_gateway_mae",
            "test_non_gateway_rmse",
            "test_non_gateway_mape",
            "epochs_ran",
        ]].to_dict(orient="records"),
    }
    with (args.out_dir / "run_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({
        "status": "ok",
        "out_dir": str(args.out_dir.resolve()),
        "target_count": len(targets),
        "device": str(device),
        "metrics": summary["best_test_non_gateway_by_mae"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
