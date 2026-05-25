#!/usr/bin/env python3
"""Huang et al. 2022-style EMS A-clean benchmark for multiple forecast horizons.

This script keeps the Huang-style input boundary used in the 1-hour report:
historical target consumption plus time-of-day sine/cosine only. It extends the
forecast target from next-hour to direct h-hour ahead forecasting.

Important horizon rule:
- `origin_ts` is the last timestamp whose target value is allowed as input.
- `target_ts = origin_ts + horizon_hours` is the timestamp being predicted.
- train/validation/test split, gateway flag, and metrics are assigned by
  `target_ts`, not by `origin_ts`.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
try:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
except ModuleNotFoundError:  # Allows frame/unit tests without the optional training dependency.
    torch = None
    nn = None
    DataLoader = None
    TensorDataset = None
try:
    from xgboost import XGBRegressor
except ModuleNotFoundError:  # Allows frame/unit tests without the optional training dependency.
    XGBRegressor = None

TARGET_FILE = "target_timeseries_1h.parquet"
FEATURE_FILE = "feature_timeseries_1h.parquet"
DEFAULT_TARGETS = [
    "T1_group__central_cooling__P",
    "T1_group__local_cooling__P",
    "T1_group__server_power__P",
    "T1_group__ventilation__P",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_seed(seed: int) -> None:
    np.random.seed(seed)
    if torch is None:
        return
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def metric_dict(actual: np.ndarray, pred: np.ndarray, prefix: str) -> dict[str, Any]:
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    mask = np.isfinite(actual) & np.isfinite(pred)
    actual = actual[mask]
    pred = pred[mask]
    if actual.size == 0:
        return {f"{prefix}_rows": 0, f"{prefix}_mae": None, f"{prefix}_rmse": None, f"{prefix}_r2": None}
    err = actual - pred
    ss_res = float(np.sum(err ** 2))
    ss_tot = float(np.sum((actual - float(np.mean(actual))) ** 2))
    r2 = 1.0 - ss_res / ss_tot if actual.size >= 2 and ss_tot != 0 else None
    return {
        f"{prefix}_rows": int(actual.size),
        f"{prefix}_mae": float(np.mean(np.abs(err))),
        f"{prefix}_rmse": float(math.sqrt(float(np.mean(err ** 2)))),
        f"{prefix}_r2": float(r2) if r2 is not None else None,
    }


def make_frame(target: pd.DataFrame, feature: pd.DataFrame, target_id: str, lookback: int, horizon_hours: int) -> pd.DataFrame:
    """Build direct h-hour-ahead samples without future-target leakage.

    Lag features are anchored at `origin_ts`: `target_lag_1` is the observed
    target value at origin time, and `target_lag_k` is k-1 hours before origin.
    The supervised label is the target value at `target_ts`.
    """
    if horizon_hours < 1:
        raise ValueError("horizon_hours must be >= 1")
    if lookback < 1:
        raise ValueError("lookback must be >= 1")

    g = target[target["target_id"] == target_id].copy().sort_values("ts").reset_index(drop=True)
    if g.empty:
        raise RuntimeError(f"target_id not found: {target_id}")

    horizon = pd.Timedelta(hours=horizon_hours)
    origin = g[["ts", "target_id", "target_value"]].rename(columns={"ts": "origin_ts", "target_value": "origin_value"})
    origin["target_ts"] = origin["origin_ts"] + horizon

    future_cols = [
        "ts", "target_id", "target_version_id", "target_value", "target_observed",
        "is_full_component_observed", "is_replacement_gap",
    ]
    future = g[future_cols].rename(columns={"ts": "target_ts"})
    df = origin.merge(future, on=["target_id", "target_ts"], how="left")

    target_feature = feature[["ts", "split", "is_gateway_outage", "gateway_outage_name", "hour_sin", "hour_cos"]].rename(columns={"ts": "target_ts"})
    df = df.merge(target_feature, on="target_ts", how="left")

    origin_feature = feature[["ts", "hour_sin", "hour_cos"]].rename(
        columns={"ts": "origin_ts", "hour_sin": "origin_hour_sin", "hour_cos": "origin_hour_cos"}
    )
    df = df.merge(origin_feature, on="origin_ts", how="left")
    df = df.sort_values("origin_ts").reset_index(drop=True)

    for lag in range(1, lookback + 1):
        df[f"target_lag_{lag}"] = df["origin_value"].shift(lag - 1)

    train_clean = (df["split"] == "train") & (~df["is_gateway_outage"].eq(True))
    for col in ["hour_sin", "hour_cos", "origin_hour_sin", "origin_hour_cos"]:
        df[col] = df[col].astype(float).ffill().bfill().fillna(0.0)
    for lag in range(1, lookback + 1):
        col = f"target_lag_{lag}"
        med = df.loc[train_clean, col].median()
        df[col] = df[col].fillna(float(med) if pd.notna(med) else 0.0)

    df["has_full_lookback"] = df.groupby("target_id").cumcount() >= (lookback - 1)
    df["has_future_target"] = df["target_value"].notna()
    df["ts"] = df["target_ts"]
    df["horizon_hours"] = int(horizon_hours)
    return df


def usable_mask(df: pd.DataFrame) -> pd.Series:
    return (
        df["has_full_lookback"].eq(True)
        & df["has_future_target"].eq(True)
        & df["target_observed"].eq(True)
        & df["is_full_component_observed"].eq(True)
        & (~df["is_replacement_gap"].eq(True))
    )


def deterministic_subset(mask: pd.Series, max_rows: int) -> np.ndarray:
    idx = np.flatnonzero(mask.to_numpy())
    if max_rows <= 0 or len(idx) <= max_rows:
        return idx
    pos = np.linspace(0, len(idx) - 1, num=max_rows, dtype=int)
    return idx[pos]


@dataclass
class CandidateResult:
    row: dict[str, Any]
    prediction: np.ndarray
    artifact: Any | None


class LSTMRegressor(nn.Module if nn is not None else object):
    def __init__(self, input_size: int, hidden_size: int = 64, num_layers: int = 1, dropout: float = 0.0):
        if nn is None:
            raise RuntimeError("torch is required for LSTM training. Install torch or run in the RunPod environment.")
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :]).squeeze(-1)


def eval_prediction(df: pd.DataFrame, pred: np.ndarray, name: str, family: str, target_id: str, fit_seconds: float, train_rows: int, train_sample_rows: int) -> dict[str, Any]:
    pred = np.maximum(np.asarray(pred, dtype=float), 0.0)
    row: dict[str, Any] = {"candidate": name, "model_family": family}
    for split in ["validation", "test"]:
        mask = (usable_mask(df) & (df["split"] == split)).to_numpy()
        row.update(metric_dict(df.loc[mask, "target_value"].to_numpy(), pred[mask], split))
        ng_mask = mask & (~df["is_gateway_outage"].eq(True).to_numpy(dtype=bool))
        row.update(metric_dict(df.loc[ng_mask, "target_value"].to_numpy(), pred[ng_mask], f"{split}_non_gateway"))
    row.update({
        "target_id": target_id,
        "horizon_hours": int(df["horizon_hours"].iloc[0]),
        "fit_seconds": float(fit_seconds),
        "train_rows": int(train_rows),
        "train_sample_rows": int(train_sample_rows),
    })
    return row


def fit_sklearn_candidate(df: pd.DataFrame, cols: list[str], estimator: Any, name: str, family: str, target_id: str, max_train_rows: int = 0) -> CandidateResult:
    from sklearn.base import clone

    train_mask = usable_mask(df) & (df["split"] == "train") & (~df["is_gateway_outage"].eq(True))
    train_idx_all = np.flatnonzero(train_mask.to_numpy())
    train_idx = deterministic_subset(train_mask, max_train_rows) if max_train_rows else train_idx_all
    X_train = df.iloc[train_idx][cols].to_numpy(dtype=float)
    y_train = df.iloc[train_idx]["target_value"].to_numpy(dtype=float)
    X_all = df[cols].to_numpy(dtype=float)
    assert np.isfinite(X_train).all(), f"non-finite training inputs for {name}"
    assert np.isfinite(y_train).all(), f"non-finite training target for {name}"
    assert np.isfinite(X_all).all(), f"non-finite prediction inputs for {name}"
    model = clone(estimator)
    start = time.time()
    model.fit(X_train, y_train)
    fit_seconds = time.time() - start
    pred = model.predict(X_all)
    row = eval_prediction(df, pred, name, family, target_id, fit_seconds, len(train_idx_all), len(train_idx))
    return CandidateResult(row=row, prediction=np.maximum(pred, 0.0), artifact=model)


def _safe_best_attr(model: Any, attr: str) -> Any | None:
    try:
        value = getattr(model, attr)
    except Exception:
        return None
    if value is None:
        return None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def fit_xgboost_candidate(
    df: pd.DataFrame,
    cols: list[str],
    name: str,
    target_id: str,
    n_estimators: int,
    max_depth: int,
    learning_rate: float,
    seed: int,
) -> CandidateResult:
    if XGBRegressor is None:
        raise RuntimeError("xgboost is required for the XGBoost model family. Install xgboost or run in the RunPod environment.")
    train_mask = usable_mask(df) & (df["split"] == "train") & (~df["is_gateway_outage"].eq(True))
    val_mask = usable_mask(df) & (df["split"] == "validation") & (~df["is_gateway_outage"].eq(True))
    train_idx = np.flatnonzero(train_mask.to_numpy())
    val_idx = np.flatnonzero(val_mask.to_numpy())
    if len(train_idx) == 0 or len(val_idx) == 0:
        raise RuntimeError(f"XGBoost requires non-empty train and validation rows for {target_id}")
    X_train = df.iloc[train_idx][cols].to_numpy(dtype=float)
    y_train = df.iloc[train_idx]["target_value"].to_numpy(dtype=float)
    X_val = df.iloc[val_idx][cols].to_numpy(dtype=float)
    y_val = df.iloc[val_idx]["target_value"].to_numpy(dtype=float)
    X_all = df[cols].to_numpy(dtype=float)
    assert np.isfinite(X_train).all(), f"non-finite XGBoost training inputs for {target_id}"
    assert np.isfinite(y_train).all(), f"non-finite XGBoost training target for {target_id}"
    assert np.isfinite(X_val).all(), f"non-finite XGBoost validation inputs for {target_id}"
    assert np.isfinite(y_val).all(), f"non-finite XGBoost validation target for {target_id}"
    assert np.isfinite(X_all).all(), f"non-finite XGBoost prediction inputs for {target_id}"
    model = XGBRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="reg:squarederror",
        tree_method="hist",
        random_state=seed,
        n_jobs=-1,
        early_stopping_rounds=30,
    )
    start = time.time()
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    fit_seconds = time.time() - start
    pred = model.predict(X_all)
    row = eval_prediction(df, pred, name, "XGBoost", target_id, fit_seconds, len(train_idx), len(train_idx))
    row["best_iteration"] = _safe_best_attr(model, "best_iteration")
    row["best_score"] = _safe_best_attr(model, "best_score")
    return CandidateResult(row=row, prediction=np.maximum(pred, 0.0), artifact=model)


def build_lstm_arrays_from_lag_columns(df: pd.DataFrame, idx: np.ndarray, lookback: int, y_mean: float, y_std: float) -> tuple[np.ndarray, np.ndarray]:
    if not np.isfinite(y_mean) or not np.isfinite(y_std) or y_std == 0:
        raise AssertionError("non-finite LSTM scaler")
    lag_cols = [f"target_lag_{lag}" for lag in range(lookback, 0, -1)]
    X = np.zeros((len(idx), lookback, 3), dtype=np.float32)
    target = np.zeros(len(idx), dtype=np.float32)
    lag_values = df.iloc[idx][lag_cols].to_numpy(dtype=float)
    hour_sin = df.iloc[idx]["hour_sin"].to_numpy(dtype=float)
    hour_cos = df.iloc[idx]["hour_cos"].to_numpy(dtype=float)
    y = df.iloc[idx]["target_value"].to_numpy(dtype=float)
    assert np.isfinite(lag_values).all(), "non-finite LSTM lag inputs"
    assert np.isfinite(hour_sin).all(), "non-finite LSTM hour_sin inputs"
    assert np.isfinite(hour_cos).all(), "non-finite LSTM hour_cos inputs"
    assert np.isfinite(y).all(), "non-finite LSTM targets"
    X[:, :, 0] = ((lag_values - y_mean) / y_std).astype(np.float32)
    X[:, :, 1] = hour_sin[:, None].astype(np.float32)
    X[:, :, 2] = hour_cos[:, None].astype(np.float32)
    target[:] = ((y - y_mean) / y_std).astype(np.float32)
    assert np.isfinite(X).all(), "non-finite LSTM input tensor"
    assert np.isfinite(target).all(), "non-finite LSTM target tensor"
    return X, target


def make_prediction_frame(df: pd.DataFrame, pred_cols: list[str], prediction: np.ndarray, candidate_col: str, candidate_name: str) -> pd.DataFrame:
    pred_df = df[pred_cols].copy()
    pred_df["prediction"] = np.asarray(prediction, dtype=float)
    pred_df[candidate_col] = candidate_name
    metric_mask = usable_mask(df)
    pred_df["usable_for_metric"] = metric_mask.to_numpy(dtype=bool)
    pred_df["usable_non_gateway"] = (metric_mask & (~df["is_gateway_outage"].eq(True))).to_numpy(dtype=bool)
    return pred_df


def make_linear_svr(C: float, eps: float, seed: int) -> Any:
    from sklearn.compose import TransformedTargetRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import LinearSVR

    reg = Pipeline([
        ("scale", StandardScaler()),
        ("model", LinearSVR(C=C, epsilon=eps, loss="epsilon_insensitive", dual="auto", tol=1e-4, max_iter=30000, random_state=seed)),
    ])
    return TransformedTargetRegressor(regressor=reg, transformer=StandardScaler())


def make_rbf_svr(C: float, eps: float, cache_size: int) -> Any:
    from sklearn.compose import TransformedTargetRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import SVR

    reg = Pipeline([
        ("scale", StandardScaler()),
        ("model", SVR(kernel="rbf", C=C, epsilon=eps, gamma="scale", cache_size=cache_size)),
    ])
    return TransformedTargetRegressor(regressor=reg, transformer=StandardScaler())


def fit_lstm_candidate(df: pd.DataFrame, lookback: int, hidden_size: int, epochs: int, patience: int, target_id: str, seed: int) -> CandidateResult:
    if torch is None or nn is None or TensorDataset is None or DataLoader is None:
        raise RuntimeError("torch is required for LSTM training. Install torch or run in the RunPod environment.")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    set_seed(seed)
    train_mask = usable_mask(df) & (df["split"] == "train") & (~df["is_gateway_outage"].eq(True))
    val_mask = usable_mask(df) & (df["split"] == "validation") & (~df["is_gateway_outage"].eq(True))
    all_mask = usable_mask(df)
    train_idx = np.flatnonzero(train_mask.to_numpy())
    val_idx = np.flatnonzero(val_mask.to_numpy())
    all_idx = np.flatnonzero(all_mask.to_numpy())
    train_y = df.iloc[train_idx]["target_value"].to_numpy(dtype=float)
    assert np.isfinite(train_y).all(), f"non-finite LSTM training target for {target_id}"
    y_mean = float(np.nanmean(train_y)); y_std = float(np.nanstd(train_y) or 1.0)

    X_train, y_train = build_lstm_arrays_from_lag_columns(df, train_idx, lookback, y_mean, y_std)
    X_val, y_val = build_lstm_arrays_from_lag_columns(df, val_idx, lookback, y_mean, y_std)
    ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    loader = DataLoader(ds, batch_size=256, shuffle=True)
    model = LSTMRegressor(input_size=3, hidden_size=hidden_size, num_layers=1).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    loss_fn = nn.MSELoss()
    best_state = None; best_val = float("inf"); bad = 0; best_epoch = -1
    history: list[dict[str, float | int]] = []
    start = time.time()
    for _epoch in range(epochs):
        model.train()
        train_losses = []
        for xb, yb in loader:
            xb = xb.to(device); yb = yb.to(device)
            opt.zero_grad(set_to_none=True)
            loss = loss_fn(model(xb), yb)
            loss.backward(); opt.step()
            train_losses.append(float(loss.detach().cpu().item()))
        model.eval()
        with torch.no_grad():
            train_pred = model(torch.from_numpy(X_train).to(device)).cpu().numpy()
            vp = model(torch.from_numpy(X_val).to(device)).cpu().numpy()
        train_mae = float(np.mean(np.abs(train_pred - y_train)))
        train_rmse = float(np.sqrt(np.mean((train_pred - y_train) ** 2)))
        val_mae = float(np.mean(np.abs(vp - y_val)))
        val_rmse = float(np.sqrt(np.mean((vp - y_val) ** 2)))
        history.append({"epoch": int(_epoch + 1), "train_loss": float(np.mean(train_losses)), "train_mae_scaled": train_mae, "train_rmse_scaled": train_rmse, "validation_mae_scaled": val_mae, "validation_rmse_scaled": val_rmse})
        if val_mae < best_val:
            best_val = val_mae
            best_epoch = _epoch + 1
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            bad = 0
        else:
            bad += 1
        if bad >= patience:
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    fit_seconds = time.time() - start
    X_all, _ = build_lstm_arrays_from_lag_columns(df, all_idx, lookback, y_mean, y_std)
    pred = np.full(len(df), np.nan, dtype=float)
    model.eval()
    with torch.no_grad():
        pp = model(torch.from_numpy(X_all).to(device)).cpu().numpy() * y_std + y_mean
    pred[all_idx] = pp
    name = f"lstm_seq{lookback}_hidden{hidden_size}"
    row = eval_prediction(df, pred, name, "LSTM", target_id, fit_seconds, len(train_idx), len(train_idx))
    row["best_epoch"] = int(best_epoch)
    row["epochs_ran"] = int(len(history))
    row["best_validation_mae_scaled"] = float(best_val)
    artifact = {"model_state_dict": model.state_dict(), "y_mean": y_mean, "y_std": y_std, "lookback": lookback, "hidden_size": hidden_size, "best_epoch": best_epoch, "history": history, "input_channels": ["target_lag_scaled_sequence", "target_hour_sin", "target_hour_cos"]}
    return CandidateResult(row=row, prediction=np.maximum(pred, 0.0), artifact=artifact)


def run_target(
    target: pd.DataFrame,
    feature: pd.DataFrame,
    target_id: str,
    out_dir: Path,
    lookback: int,
    horizon_hours: int,
    seed: int,
    quick: bool,
    lstm_epochs: int | None,
    lstm_patience: int | None,
) -> dict[str, Any]:
    target_out = out_dir / f"h{horizon_hours:03d}" / target_id
    target_out.mkdir(parents=True, exist_ok=True)
    df = make_frame(target, feature, target_id, lookback, horizon_hours)
    tabular_cols = [f"target_lag_{i}" for i in range(1, lookback + 1)] + ["hour_sin", "hour_cos"]
    results: list[CandidateResult] = []
    effective_lstm_epochs = int(lstm_epochs if lstm_epochs is not None else (8 if quick else 35))
    effective_lstm_patience = int(lstm_patience if lstm_patience is not None else (3 if quick else 10))

    linear_grid = [(1.0, 0.1)] if quick else [(0.1, 0.02), (1.0, 0.02), (10.0, 0.02), (0.1, 0.1), (1.0, 0.1), (10.0, 0.1)]
    for C, eps in linear_grid:
        results.append(fit_sklearn_candidate(
            df, tabular_cols, make_linear_svr(C, eps, seed),
            f"linear_svr_yz_C{C:g}_eps{eps:g}", "SVR", target_id,
        ))

    rbf_grid = [(5.0, 0.05)] if quick else [(1.0, 0.02), (5.0, 0.02), (1.0, 0.1), (5.0, 0.1)]
    rbf_sample = 4000 if quick else 8000
    for C, eps in rbf_grid:
        results.append(fit_sklearn_candidate(
            df, tabular_cols, make_rbf_svr(C, eps, cache_size=2000),
            f"rbf_svr_yz_s{rbf_sample}_C{C:g}_eps{eps:g}", "SVR", target_id, max_train_rows=rbf_sample,
        ))

    xgb_grid = [(300, 3, 0.06)] if quick else [(300, 3, 0.06), (500, 3, 0.03), (300, 5, 0.06)]
    for n_est, depth, lr in xgb_grid:
        results.append(fit_xgboost_candidate(
            df, tabular_cols,
            f"xgboost_n{n_est}_depth{depth}_lr{lr:g}",
            target_id,
            n_estimators=n_est,
            max_depth=depth,
            learning_rate=lr,
            seed=seed,
        ))

    lstm_hidden = [32] if quick else [32, 64]
    for hidden in lstm_hidden:
        results.append(fit_lstm_candidate(df, lookback, hidden, epochs=effective_lstm_epochs, patience=effective_lstm_patience, target_id=target_id, seed=seed))

    rows = []
    for res in results:
        rows.append(res.row)
        if res.row["model_family"] == "LSTM" and isinstance(res.artifact, dict) and "history" in res.artifact:
            pd.DataFrame(res.artifact["history"]).to_csv(target_out / f"{res.row['candidate']}_loss_history.csv", index=False)
        print(json.dumps({
            "horizon_hours": horizon_hours,
            "target_id": target_id,
            "candidate": res.row["candidate"],
            "family": res.row["model_family"],
            "validation_non_gateway_rmse": res.row.get("validation_non_gateway_rmse"),
            "test_non_gateway_rmse": res.row.get("test_non_gateway_rmse"),
        }, ensure_ascii=False), flush=True)
    metrics = pd.DataFrame(rows).sort_values(["validation_non_gateway_rmse", "validation_non_gateway_mae", "candidate"]).reset_index(drop=True)
    metrics.to_csv(target_out / "candidate_metrics.csv", index=False)
    best = metrics.iloc[0].to_dict()
    best_name = str(best["candidate"])
    best_res = next(r for r in results if r.row["candidate"] == best_name)
    pred_cols = ["origin_ts", "target_ts", "ts", "target_id", "target_version_id", "split", "origin_value", "target_value", "is_gateway_outage", "gateway_outage_name"]
    pred_df = make_prediction_frame(df, pred_cols, best_res.prediction, candidate_col="candidate", candidate_name=best_name)
    pred_df["horizon_hours"] = horizon_hours
    pred_df.to_parquet(target_out / "best_predictions.parquet", index=False)
    if best_res.artifact is not None:
        if best["model_family"] == "LSTM":
            if torch is None:
                raise RuntimeError("torch is required to save the LSTM artifact.")
            torch.save(best_res.artifact, target_out / "best_model.pt")
        else:
            import joblib

            joblib.dump(best_res.artifact, target_out / "best_model.joblib", compress=3)
    with (target_out / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump({
            "created_at_utc": utc_now(),
            "target_id": target_id,
            "horizon_hours": horizon_hours,
            "paper_adaptation": "Huang et al. 2022-style direct horizon adaptation: historical consumption + target-hour sine/cosine only.",
            "selection_metric": "validation_non_gateway_rmse",
            "gateway_policy": "Training excludes gateway-outage rows. Validation/test metrics are stored both with all usable rows and with non-gateway rows; selection uses validation_non_gateway_rmse.",
            "prediction_parquet_policy": "Saved prediction parquet keeps the full split rows and adds usable_for_metric plus usable_non_gateway flags for plot/report filtering.",
            "lag_feature_semantics": "target_lag_1 is the observed origin_ts value; target_lag_k is k-1 hours before origin_ts. The name is retained for compatibility with existing reports and verifiers.",
            "best": best,
            "tabular_features": tabular_cols,
            "lstm_channels": ["target_lag_scaled_sequence", "target_hour_sin", "target_hour_cos"],
            "lstm_epochs": effective_lstm_epochs,
            "lstm_patience": effective_lstm_patience,
        }, f, ensure_ascii=False, indent=2, default=str)
    return best


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset-dir", type=Path, default=Path("outputs/modeling/a_clean_targets_1h"))
    p.add_argument("--out-dir", type=Path, default=Path("outputs/modeling/a_clean_huang2022_multi_horizon"))
    p.add_argument("--target-id", action="append", default=None)
    p.add_argument("--lookback", type=int, default=24)
    p.add_argument("--horizon-hours", type=int, action="append", default=None)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--quick", action="store_true")
    p.add_argument("--lstm-epochs", type=int, default=None, help="Override LSTM epochs. Default: 8 quick, 35 normal.")
    p.add_argument("--lstm-patience", type=int, default=None, help="Override LSTM early-stop patience. Default: 3 quick, 10 normal.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    set_seed(args.seed)
    dataset_dir = args.dataset_dir.resolve(); out_dir = args.out_dir.resolve(); out_dir.mkdir(parents=True, exist_ok=True)
    target = pd.read_parquet(dataset_dir / TARGET_FILE)
    feature = pd.read_parquet(dataset_dir / FEATURE_FILE)
    targets = args.target_id or DEFAULT_TARGETS
    horizons = args.horizon_hours or [1, 24, 168]
    best_rows = []
    for horizon_hours in horizons:
        for target_id in targets:
            best_rows.append(run_target(target, feature, target_id, out_dir, args.lookback, horizon_hours, args.seed, args.quick, args.lstm_epochs, args.lstm_patience))
    best_df = pd.DataFrame(best_rows)
    best_df.to_csv(out_dir / "multi_horizon_summary.csv", index=False)
    by_family = []
    for horizon_hours in horizons:
        for target_id in targets:
            p = out_dir / f"h{horizon_hours:03d}" / target_id / "candidate_metrics.csv"
            if p.exists():
                df = pd.read_csv(p)
                by_family.append(df.sort_values(["validation_non_gateway_rmse", "validation_non_gateway_mae"]).groupby("model_family", as_index=False).head(1))
    if by_family:
        pd.concat(by_family, ignore_index=True).to_csv(out_dir / "multi_horizon_family_best.csv", index=False)
    effective_lstm_epochs = int(args.lstm_epochs if args.lstm_epochs is not None else (8 if args.quick else 35))
    effective_lstm_patience = int(args.lstm_patience if args.lstm_patience is not None else (3 if args.quick else 10))
    manifest = {
        "created_at_utc": utc_now(),
        "run_label": "a_clean_huang2022_multi_horizon",
        "paper": {
            "title": "Energy Forecasting in a Public Building: A Benchmarking Analysis on LSTM, SVR, and XGBoost Networks",
            "doi": "10.3390/app12199788",
            "source": "Applied Sciences",
            "year": 2022,
        },
        "adaptation_boundary": "EMS A-clean 1h direct horizon adaptation. Inputs limited to historical target consumption and target-hour sine/cosine.",
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "targets": targets,
        "horizon_hours": horizons,
        "lookback": args.lookback,
        "lstm_epochs": effective_lstm_epochs,
        "lstm_patience": effective_lstm_patience,
        "gateway_policy": "Training excludes gateway-outage rows. Validation/test metrics are stored both with all usable rows and with non-gateway rows; selection uses validation_non_gateway_rmse.",
        "prediction_parquet_policy": "Saved prediction parquet keeps full split rows and adds usable_for_metric plus usable_non_gateway flags for downstream plot/report filtering.",
        "lag_feature_semantics": "target_lag_1 is the observed origin_ts value; target_lag_k is k-1 hours before origin_ts. The name is retained for compatibility with existing reports and verifiers.",
        "selection_metric": "validation_non_gateway_rmse",
        "best": best_df.to_dict(orient="records"),
    }
    with (out_dir / "run_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"status": "ok", **manifest}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
