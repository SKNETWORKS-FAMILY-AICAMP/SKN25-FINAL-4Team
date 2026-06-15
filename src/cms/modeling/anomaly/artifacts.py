"""모델 및 라우팅 결정 저장/로드."""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import joblib
import numpy as np
import torch

from cms.modeling.anomaly.config import ARTIFACTS_DIR


def _artifact_dir(meter_urn: str, horizon: int, artifacts_root: Path | None = None) -> Path:
    d = (artifacts_root or ARTIFACTS_DIR) / f"{horizon}h" / meter_urn
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── LSTM 모델 저장/로드 ──────────────────────────────────────────────────────

def save_lstm_model(model, meter_urn: str, horizon: int, version: str,
                    artifacts_root: Path | None = None) -> None:
    path = _artifact_dir(meter_urn, horizon, artifacts_root) / f"lstm_{version}.pt"
    torch.save(model.state_dict(), path)


def load_lstm_model(model_cls, input_size: int, hidden_size: int, output_size: int,
                    architecture: str, dropout: float,
                    meter_urn: str, horizon: int, version: str):
    path = _artifact_dir(meter_urn, horizon) / f"lstm_{version}.pt"
    model = model_cls(input_size=input_size, hidden_size=hidden_size, output_size=output_size,
                      model_architecture=architecture, model_dropout=dropout)
    model.load_state_dict(torch.load(path, map_location="cpu"))
    model.eval()
    return model


# ── CatBoost 저장/로드 ───────────────────────────────────────────────────────

def save_catboost(model, meter_urn: str, horizon: int,
                  artifacts_root: Path | None = None) -> None:
    path = _artifact_dir(meter_urn, horizon, artifacts_root) / "catboost.cbm"
    model.save_model(str(path))


def load_catboost(meter_urn: str, horizon: int):
    from catboost import CatBoostRegressor
    path = _artifact_dir(meter_urn, horizon) / "catboost.cbm"
    model = CatBoostRegressor()
    model.load_model(str(path))
    return model


# ── Ridge 저장/로드 ──────────────────────────────────────────────────────────

def save_ridge(model, meter_urn: str, horizon: int,
               artifacts_root: Path | None = None) -> None:
    path = _artifact_dir(meter_urn, horizon, artifacts_root) / "ridge.joblib"
    joblib.dump(model, path)


def load_ridge(meter_urn: str, horizon: int):
    path = _artifact_dir(meter_urn, horizon) / "ridge.joblib"
    return joblib.load(path)


# ── Scaler 저장/로드 ─────────────────────────────────────────────────────────

def save_scalers(input_scaler, target_scaler, meter_urn: str, horizon: int,
                 artifacts_root: Path | None = None) -> None:
    d = _artifact_dir(meter_urn, horizon, artifacts_root)
    joblib.dump(input_scaler, d / "input_scaler.joblib")
    joblib.dump(target_scaler, d / "target_scaler.joblib")


def load_scalers(meter_urn: str, horizon: int):
    d = _artifact_dir(meter_urn, horizon)
    return joblib.load(d / "input_scaler.joblib"), joblib.load(d / "target_scaler.joblib")


# ── 라우팅 결정 저장/로드 (inference 전용) ──────────────────────────────────

def save_routing(routing: dict, meter_urn: str, horizon: int,
                 artifacts_root: Path | None = None) -> None:
    """inference 런타임에 필요한 키만 저장. 학습 메타데이터는 save_train_meta() 사용."""
    path = _artifact_dir(meter_urn, horizon, artifacts_root) / "routing.json"
    with open(path, "w") as f:
        json.dump(routing, f, indent=2)


def load_routing(meter_urn: str, horizon: int) -> dict:
    path = _artifact_dir(meter_urn, horizon) / "routing.json"
    with open(path) as f:
        return json.load(f)


def save_train_meta(meta: dict, meter_urn: str, horizon: int,
                    artifacts_root: Path | None = None) -> None:
    """학습 기록 전용 (ridge_alpha, anomaly_threshold 등). inference에서 읽지 않음."""
    path = _artifact_dir(meter_urn, horizon, artifacts_root) / "train_meta.json"
    with open(path, "w") as f:
        json.dump(meta, f, indent=2)


# ── Bias correction 저장/로드 ────────────────────────────────────────────────

def save_bias_corrections(corrections_df, meter_urn: str, horizon: int,
                          artifacts_root: Path | None = None) -> None:
    path = _artifact_dir(meter_urn, horizon, artifacts_root) / "hour_bias_corrections.csv"
    corrections_df.to_csv(path, index=False)


def load_bias_corrections(meter_urn: str, horizon: int):
    import pandas as pd
    path = _artifact_dir(meter_urn, horizon) / "hour_bias_corrections.csv"
    return pd.read_csv(path)


# ── Feature columns 저장/로드 ────────────────────────────────────────────────

def save_feature_columns(feature_columns: list[str], meter_urn: str, horizon: int,
                         artifacts_root: Path | None = None) -> None:
    path = _artifact_dir(meter_urn, horizon, artifacts_root) / "feature_columns.json"
    with open(path, "w") as f:
        json.dump(feature_columns, f)


def load_feature_columns(meter_urn: str, horizon: int) -> list[str]:
    path = _artifact_dir(meter_urn, horizon) / "feature_columns.json"
    with open(path) as f:
        return json.load(f)
