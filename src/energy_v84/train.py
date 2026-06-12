"""
v84 풀 파이프라인 학습 스크립트 (2패스, test2 의존 없음).

패스 1: 전체 계량기 v1~v7 LSTM 학습 + v10/v12/v15 예측 → 메모리 보관
패스 2: 그룹별 v19 결정 → 계량기별 v24/v25/v36/v52/v53/v57 → v67/v71/v84 앙상블 → 저장

실행:
  conda run -n skn25 python -m energy_v84.train --horizon 1
  conda run -n skn25 python -m energy_v84.train --horizon 3
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")

import numpy as np
import pandas as pd
import torch

from energy_v84.common.config import (
    ALL_METER_SPECS,
    ARTIFACTS_DIR,
    BATCH_SIZE,
    EPOCHS,
    LSTM_VARIANTS,
    SEED,
    V25_MIN_MEDIAN_IMPROVEMENT,
    V57_MIN_METER_IMPROVEMENT,
    USE_RESIDUAL_TARGET,
    MeterSpec,
    specs_for_group,
)
from energy_v84.common.db import build_engine, fetch_meter_frame
from energy_v84.common.preprocessing import build_windows, prepare_model_frame
from energy_v84.common.model import (
    RecurrentPredictor,
    add_anomaly_flags,
    build_prediction_frame,
    device,
    evaluate_objective_scaled,
    inverse_target,
    make_loss_fn,
    predict_scaled,
    set_seed,
    thresholds_from_validation,
    train_lstm,
    HIDDEN_SIZE,
    LEARNING_RATE,
    PATIENCE,
)
from energy_v84.common.catboost_model import fit_catboost, predict_catboost_scaled
from energy_v84.common.lightgbm_model import fit_lightgbm, predict_lightgbm_scaled, save_lightgbm
from energy_v84.common.ridge import fit_ridge, predict_ridge_scaled
from energy_v84.common.naive import predict_seasonal_naive
from energy_v84.common.router import v63_route_group
from energy_v84.common.ensemble import (
    build_prediction_df,
    build_median_ensemble,
    fit_shrunk_hour_bias_corrections,
    apply_hour_bias_corrections,
    compute_metrics,
    compute_persistence_mae,
)
from energy_v84.common.plots import save_plots
from energy_v84.common.artifacts import (
    save_lstm_model,
    save_catboost,
    save_ridge,
    save_scalers,
    save_routing,
    save_train_meta,
    save_bias_corrections,
    save_feature_columns,
)
from energy_v84.common.preprocessing import make_loader
from energy_v84.common.config import SMOOTH_L1_BETA


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--horizon", type=int, choices=[1, 3], required=True)
    p.add_argument("--meters", nargs="*", default=None)
    p.add_argument("--groups", nargs="*", choices=["electric", "thermal"], default=None)
    p.add_argument("--epochs", type=int, default=EPOCHS)
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--output-dir", type=str, default=None,
                   help="artifact 저장 루트. 미지정 시 candidate/run_YYYYMMDD 자동 생성.")
    return p.parse_args()


def _get_specs(args):
    specs = specs_for_group()
    if args.groups:
        gs = set(args.groups)
        specs = [s for s in specs if s.group in gs]
    if args.meters:
        ms = set(args.meters)
        specs = [s for s in specs if s.meter_urn in ms]
    return specs


def _mae(pred, y):
    return float(np.mean(np.abs(pred - y)))


def _persist_mae_sc(y_val: np.ndarray) -> float:
    """스케일 공간에서 window 간 t+1 persistence MAE."""
    a = y_val[:, 0]
    return float(np.mean(np.abs(a[1:] - a[:-1])))


def _inter_row(model: str, val_loss, val_pred: np.ndarray, y_val: np.ndarray, persist_mae: float) -> dict:
    """중간 계층 한 행: 모델명 / val_loss / scaled MAE / persistence 비교."""
    mae = float(np.mean(np.abs(val_pred[:, 0] - y_val[:, 0])))
    return {
        "model": model,
        "val_loss": val_loss,
        "val_mae_scaled": round(mae, 6),
        "persistence_mae_scaled": round(persist_mae, 6),
        "beats_persistence": mae < persist_mae,
    }


def _stepwise_topk(results, y_val, y_test, top_k):
    horizon = y_val.shape[1]
    vp = np.zeros_like(y_val)
    tp = np.zeros_like(y_test)
    step_versions: list[list[str]]   = []
    step_weights:  list[list[float]] = []
    for step in range(horizon):
        scores = {v: float(np.mean(np.abs(r["val_pred"][:, step] - y_val[:, step])))
                  for v, r in results.items()}
        top = sorted(scores.items(), key=lambda x: (x[1], x[0]))[:top_k]
        versions, maes = zip(*top)
        inv = np.array([1.0 / max(m, 1e-9) for m in maes])
        w = inv / inv.sum()
        step_versions.append(list(versions))
        step_weights.append(w.tolist())
        for i, v in enumerate(versions):
            vp[:, step] += w[i] * results[v]["val_pred"][:, step]
            tp[:, step] += w[i] * results[v]["test_pred"][:, step]
    return vp, tp, step_versions, step_weights


def _resolve_v52_source(v52_source_raw: str, v19_version: str, use_v24: bool) -> str:
    """v52_source alias(v19/v25)를 concrete 값(v3/v10/v12/v15/v24)으로 해소."""
    if v52_source_raw == "v19":
        return v19_version              # v10/v12/v15 중 하나
    if v52_source_raw == "v25":
        return "v24" if use_v24 else v19_version
    return v52_source_raw               # v10/v12/v15/v3 → 그대로


# ────────────────────────────────────────────────────────────────────────────
# 패스 1
# ────────────────────────────────────────────────────────────────────────────

def pass1_train_meter(engine, spec: MeterSpec, horizon: int, args,
                      output_dir: Path | None = None) -> dict:
    urn = spec.meter_urn
    print(f"  [패스1] {urn}", end="", flush=True)

    raw = fetch_meter_frame(engine, spec)
    bundle_24  = _make_bundle(raw, spec, horizon, 24,  False)
    bundle_24t = _make_bundle(raw, spec, horizon, 24,  True)
    bundle_168 = _make_bundle(raw, spec, horizon, 168, True)

    variant_bundles = {"v1": bundle_24, "v2": bundle_24t, "v3": bundle_168,
                       "v4": bundle_24t, "v6": bundle_24t, "v7": bundle_24t}

    lstm_results = {}
    for variant in LSTM_VARIANTS:
        b = variant_bundles[variant.version]
        model, best_val, val_history = train_lstm(b, variant, horizon, args.batch_size, args.epochs)
        save_lstm_model(model, urn, horizon, variant.version, artifacts_root=output_dir)
        model.eval()
        with torch.no_grad():
            vp = predict_scaled(model, b.x_val, args.batch_size)
            tp = predict_scaled(model, b.x_test, args.batch_size)
        lstm_results[variant.version] = {"val_pred": vp, "test_pred": tp, "val_loss": best_val, "val_history": val_history}
        print(f" {variant.version}✓", end="", flush=True)

    # v10/v12/v15 (v3 제외)
    no_v3 = {v: r for v, r in lstm_results.items() if v != "v3"}
    y_val, y_test = bundle_24t.y_val, bundle_24t.y_test

    scores = {v: _mae(r["val_pred"], y_val) for v, r in no_v3.items()}
    top2 = sorted(scores.items(), key=lambda x: (x[1], x[0]))[:2]
    inv = np.array([1.0 / max(m, 1e-9) for _, m in top2])
    w = inv / inv.sum()
    v10_vp = sum(w[i] * no_v3[v]["val_pred"]  for i, (v, _) in enumerate(top2))
    v10_tp = sum(w[i] * no_v3[v]["test_pred"] for i, (v, _) in enumerate(top2))
    # inference에서 재사용할 top-2 LSTM 버전 + 가중치 저장
    lstm_top2_versions = [v for v, _ in top2]
    lstm_top2_weights  = w.tolist()

    v12_vp, v12_tp, v12_step_versions, v12_step_weights = _stepwise_topk(no_v3, y_val, y_test, 2)
    v15_vp, v15_tp, v15_step_versions, v15_step_weights = _stepwise_topk(no_v3, y_val, y_test, 3)

    # LSTM 중간 metrics: val_loss + scaled MAE vs persistence
    persist_mae_24t  = _persist_mae_sc(y_val)
    persist_mae_168  = _persist_mae_sc(bundle_168.y_val)
    bundle_yval_map  = {"v1": y_val, "v2": y_val, "v3": bundle_168.y_val,
                        "v4": y_val, "v6": y_val, "v7": y_val}
    bundle_persist_map = {"v1": persist_mae_24t, "v2": persist_mae_24t, "v3": persist_mae_168,
                          "v4": persist_mae_24t, "v6": persist_mae_24t, "v7": persist_mae_24t}
    lstm_intermediate = []
    for v, r in lstm_results.items():
        row = _inter_row(
            f"lstm_{v}",
            r["val_loss"],
            r["val_pred"],
            bundle_yval_map[v],
            bundle_persist_map[v],
        )
        row["val_loss_history"] = str(r.get("val_history", []))
        lstm_intermediate.append(row)

    print(" 완료", flush=True)
    return {
        "spec": spec, "bundle_24t": bundle_24t, "bundle_168": bundle_168,
        "y_val": y_val, "y_test": y_test, "lstm_results": lstm_results,
        "lstm_top2_versions": lstm_top2_versions,
        "lstm_top2_weights": lstm_top2_weights,
        "persist_mae_24t": persist_mae_24t,
        "lstm_intermediate": lstm_intermediate,
        "v10": {"val_pred": v10_vp, "test_pred": v10_tp},
        "v12": {"val_pred": v12_vp, "test_pred": v12_tp},
        "v15": {"val_pred": v15_vp, "test_pred": v15_tp},
        "v12_step_versions": v12_step_versions,
        "v12_step_weights":  v12_step_weights,
        "v15_step_versions": v15_step_versions,
        "v15_step_weights":  v15_step_weights,
    }


def _make_bundle(raw, spec, horizon, window_size, use_time_features):
    from energy_v84.common.preprocessing import prepare_model_frame, build_windows
    frame, rows_raw, feat_cols = prepare_model_frame(raw, spec, use_time_features)
    return build_windows(frame, spec, horizon, rows_raw, feat_cols, window_size)


# ────────────────────────────────────────────────────────────────────────────
# v19 그룹 선택
# ────────────────────────────────────────────────────────────────────────────

def select_v19(pass1: dict[str, dict], group: str, role: str) -> str:
    urns = [d["spec"].meter_urn for d in pass1.values()
            if d["spec"].group == group and d["spec"].role == role]
    if not urns:
        return "v10"
    best_v, best_m = "v10", float("inf")
    for v in ("v10", "v12", "v15"):
        maes = [_mae(pass1[u][v]["val_pred"], pass1[u]["y_val"]) for u in urns if u in pass1]
        if not maes:
            continue
        m = float(np.median(maes))
        if m < best_m:
            best_m, best_v = m, v
    return best_v


# ────────────────────────────────────────────────────────────────────────────
# 패스 2
# ────────────────────────────────────────────────────────────────────────────

def pass2_stage1(data: dict, args, v19_version: str, horizon: int,
                 output_dir: Path | None = None) -> dict:
    """v57(LSTM+CatBoost 라우팅)과 v61(LightGBM) 예측을 계산해 반환. v63 라우팅은 그룹 단위라 메인에서 수행."""
    spec = data["spec"]
    urn  = spec.meter_urn
    b    = data["bundle_24t"]
    y_val, y_test = data["y_val"], data["y_test"]
    v10, v12, v15 = data["v10"], data["v12"], data["v15"]
    lstm_results      = data["lstm_results"]
    v12_step_versions = data["v12_step_versions"]
    v12_step_weights  = data["v12_step_weights"]
    v15_step_versions = data["v15_step_versions"]
    v15_step_weights  = data["v15_step_weights"]

    print(f"  [패스2] {urn} v19={v19_version}", end="", flush=True)

    # v19
    v19 = data[v19_version]

    # v24: convex grid
    best_w, best_m = None, float("inf")
    for a in range(11):
        for bb in range(11 - a):
            c = 10 - a - bb
            w = (a/10, bb/10, c/10)
            p = w[0]*v10["val_pred"] + w[1]*v12["val_pred"] + w[2]*v15["val_pred"]
            m = _mae(p, y_val)
            if m < best_m:
                best_m, best_w = m, w
    v24_vp      = best_w[0]*v10["val_pred"]  + best_w[1]*v12["val_pred"]  + best_w[2]*v15["val_pred"]
    v24_tp      = best_w[0]*v10["test_pred"] + best_w[1]*v12["test_pred"] + best_w[2]*v15["test_pred"]
    v24_weights = list(best_w)

    # v25
    use_v24 = (_mae(v19["val_pred"], y_val) - _mae(v24_vp, y_val)) / max(_mae(v19["val_pred"], y_val), 1e-9) >= V25_MIN_MEDIAN_IMPROVEMENT
    v25_vp = v24_vp if use_v24 else v19["val_pred"]
    v25_tp = v24_tp if use_v24 else v19["test_pred"]

    # v36: median residual bias correction
    bias_v36 = np.median(y_val - v25_vp, axis=0)
    v36_vp, v36_tp = v25_vp + bias_v36, v25_tp + bias_v36

    # v52: broad-source gate + bias gate
    broad = {"v10": v10, "v12": v12, "v15": v15, "v19": v19,
             "v25": {"val_pred": v25_vp, "test_pred": v25_tp}}
    v3 = lstm_results.get("v3", {})
    if v3 and v3["val_pred"].shape == y_val.shape:
        broad["v3"] = v3
    base_mae = _mae(v25_vp, y_val)
    v52_source = "v25"
    v52_vp, v52_tp = v25_vp.copy(), v25_tp.copy()
    for v, d in broad.items():
        if v == "v25":
            continue
        cm = _mae(d["val_pred"], y_val)
        if (base_mae - cm) / max(base_mae, 1e-9) >= 0.0001 and cm < _mae(v52_vp, y_val):
            v52_vp, v52_tp = d["val_pred"].copy(), d["test_pred"].copy()
            v52_source = v
    v52_bias_correction = np.zeros(horizon, dtype=np.float64)
    n = len(v52_vp)
    split = max(1, min(int(n * 0.5), n - 1))
    fit_b = np.median(y_val[:split] - v52_vp[:split], axis=0)
    if _mae(v52_vp[split:] + fit_b, y_val[split:]) < _mae(v52_vp[split:], y_val[split:]):
        full_b = np.median(y_val - v52_vp, axis=0)
        v52_vp, v52_tp = v52_vp + full_b, v52_tp + full_b
        v52_bias_correction = full_b

    v52_source_raw      = v52_source
    v52_source_resolved = _resolve_v52_source(v52_source_raw, v19_version, use_v24)

    # v53: CatBoost
    print(" CB...", end="", flush=True)
    cb = fit_catboost(b, horizon, seed=args.seed)
    cb_vp = predict_catboost_scaled(cb, b.x_val, horizon)
    cb_tp = predict_catboost_scaled(cb, b.x_test, horizon)
    save_catboost(cb, urn, horizon, artifacts_root=output_dir)

    # v57: 계량기별 라우팅 (base=v52, 0.25% 게이트)
    cands = {"v52": (v52_vp, v52_tp), "v53": (cb_vp, cb_tp),
             "v36": (v36_vp, v36_tp)}
    base = _mae(v52_vp, y_val)
    v57_ver, v57_vp, v57_tp = "v52", v52_vp, v52_tp
    for v, (vp, tp) in cands.items():
        if v == "v52":
            continue
        cm = _mae(vp, y_val)
        if (base - cm) / max(base, 1e-9) >= V57_MIN_METER_IMPROVEMENT and cm < _mae(v57_vp, y_val):
            v57_ver, v57_vp, v57_tp = v, vp, tp
    print(f" v57→{v57_ver}", end="", flush=True)

    # v61: LightGBM (v63 라우팅 후보)
    v61_entry = None
    try:
        print(" LGB...", end="", flush=True)
        lgb_models = fit_lightgbm(b, horizon, seed=args.seed)
        lgb_vp = predict_lightgbm_scaled(lgb_models, b.x_val)
        lgb_tp = predict_lightgbm_scaled(lgb_models, b.x_test)
        save_lightgbm(lgb_models, urn, horizon, output_dir or ARTIFACTS_DIR)
        v61_entry = {"val_pred_scaled": lgb_vp, "test_pred_scaled": lgb_tp, "y_val": y_val}
    except Exception as e:
        print(f" LGB실패({e})", end="", flush=True)

    # v52 / v53(CatBoost) / v57 / v61(LightGBM) 중간 metrics
    persist_mae_sc = data["persist_mae_24t"]
    def _inter_row_nb(model, val_pred, y_val, persist_mae):
        row = _inter_row(model, None, val_pred, y_val, persist_mae)
        row["val_loss_history"] = ""
        return row

    stage1_intermediate = [
        _inter_row_nb("v52",              v52_vp, y_val, persist_mae_sc),
        _inter_row_nb("v53_catboost",     cb_vp,  y_val, persist_mae_sc),
        _inter_row_nb(f"v57({v57_ver})",  v57_vp, y_val, persist_mae_sc),
    ]
    if v61_entry is not None:
        stage1_intermediate.append(
            _inter_row_nb("v61_lightgbm",
                          v61_entry["val_pred_scaled"], y_val, persist_mae_sc)
        )

    print(" 완료", flush=True)
    return {
        "spec": spec,
        "b": b,
        "y_val": y_val,
        "y_test": y_test,
        "use_v24": use_v24,
        "bias_v36": bias_v36,
        "v19_version": v19_version,
        "v57_ver": v57_ver,
        "v57": {"val_pred_scaled": v57_vp, "test_pred_scaled": v57_tp, "y_val": y_val},
        "v61": v61_entry,
        "persist_mae_sc": persist_mae_sc,
        "stage1_intermediate": stage1_intermediate,
        # v52 재현용 artifact
        "v52_source_raw":      v52_source_raw,
        "v52_source_resolved": v52_source_resolved,
        "v52_bias_correction": v52_bias_correction.tolist(),
        "v24_weights":         v24_weights,
        "v12_step_versions":   v12_step_versions,
        "v12_step_weights":    v12_step_weights,
        "v15_step_versions":   v15_step_versions,
        "v15_step_weights":    v15_step_weights,
    }


def pass2_finalize(pass1_data: dict, cand: dict, v63_vp: np.ndarray, v63_tp: np.ndarray,
                   v63_ver: str, args, output_dir: Path | None = None) -> dict:
    """v63 선택 결과를 받아 v67/v71/v84 앙상블을 구성하고 모든 아티팩트를 저장한다."""
    spec         = cand["spec"]
    urn          = spec.meter_urn
    b            = cand["b"]
    y_val        = cand["y_val"]
    y_test       = cand["y_test"]
    use_v24             = cand["use_v24"]
    bias_v36            = cand["bias_v36"]
    v19_version         = cand["v19_version"]
    v57_ver             = cand["v57_ver"]
    v52_source_raw      = cand["v52_source_raw"]
    v52_source_resolved = cand["v52_source_resolved"]
    v52_bias_correction = cand["v52_bias_correction"]
    v24_weights         = cand["v24_weights"]
    v12_step_versions   = cand["v12_step_versions"]
    v12_step_weights    = cand["v12_step_weights"]
    v15_step_versions   = cand["v15_step_versions"]
    v15_step_weights    = cand["v15_step_weights"]
    horizon      = b.y_val.shape[1]

    print(f"  [앙상블] {urn} v63→{v63_ver}", end="", flush=True)

    # v67: Ridge
    ridge, alpha = fit_ridge(b, horizon)
    r_vp = predict_ridge_scaled(ridge, b.x_val)
    r_tp = predict_ridge_scaled(ridge, b.x_test)
    # v71: Seasonal Naive
    n_vp = predict_seasonal_naive(b, "val", horizon)
    n_tp = predict_seasonal_naive(b, "test", horizon)

    # v84: median 앙상블 + shrunk hour bias 보정
    ts = b.target_scaler
    # USE_RESIDUAL_TARGET=True면 anchor를 전달해 잔차 → 원래 값으로 복원
    anc_val  = b.anchor_val  if USE_RESIDUAL_TARGET else None
    anc_test = b.anchor_test if USE_RESIDUAL_TARGET else None
    def _pdf(meta, y, p, anc): return build_prediction_df(meta, y, p, ts, horizon, anchor=anc)
    val_ens  = build_median_ensemble({"v63": _pdf(b.meta_val,  y_val,  v63_vp, anc_val),
                                      "v67": _pdf(b.meta_val,  y_val,  r_vp,   anc_val),
                                      "v71": _pdf(b.meta_val,  y_val,  n_vp,   anc_val)}, horizon)
    test_ens = build_median_ensemble({"v63": _pdf(b.meta_test, y_test, v63_tp, anc_test),
                                      "v67": _pdf(b.meta_test, y_test, r_tp,   anc_test),
                                      "v71": _pdf(b.meta_test, y_test, n_tp,   anc_test)}, horizon)
    corr = fit_shrunk_hour_bias_corrections(val_ens, horizon)
    val_final  = apply_hour_bias_corrections(val_ens,  corr, horizon)
    test_final = apply_hour_bias_corrections(test_ens, corr, horizon)

    # 이상 탐지 threshold (validation 기준)
    threshold = float(val_final["anomaly_score_mae"].quantile(0.995))
    val_final  = add_anomaly_flags(val_final,  threshold)
    test_final = add_anomaly_flags(test_final, threshold)

    # 저장
    _root = output_dir
    save_scalers(b.input_scaler, b.target_scaler, urn, horizon, artifacts_root=_root)
    save_feature_columns(b.feature_columns, urn, horizon, artifacts_root=_root)
    save_bias_corrections(corr, urn, horizon, artifacts_root=_root)
    save_routing({
        "v57": v57_ver, "v63": v63_ver,
        "lstm_top2_versions": pass1_data["lstm_top2_versions"],
        "lstm_top2_weights":  pass1_data["lstm_top2_weights"],
        "v36_bias":           bias_v36.tolist(),
        "v52_source":          v52_source_resolved,
        "v52_bias_correction": v52_bias_correction,
        "v24_weights":         v24_weights,
        "v12_step_versions":   v12_step_versions,
        "v12_step_weights":    v12_step_weights,
        "v15_step_versions":   v15_step_versions,
        "v15_step_weights":    v15_step_weights,
    }, urn, horizon, artifacts_root=_root)
    save_train_meta({
        "meter_urn": urn, "horizon": horizon,
        "v19": v19_version, "v25": "v24" if use_v24 else v19_version,
        "v52_source_raw":      v52_source_raw,
        "v52_source_resolved": v52_source_resolved,
        "ridge_alpha": alpha,
        "anomaly_threshold": threshold,
    }, urn, horizon, artifacts_root=_root)
    save_ridge(ridge, urn, horizon, artifacts_root=_root)
    out = (_root or ARTIFACTS_DIR) / f"{horizon}h" / urn
    val_final.to_csv(out / "validation_predictions.csv", index=False)
    test_final.to_csv(out / "test_predictions.csv", index=False)

    vm = compute_metrics(val_final, horizon, "val")
    tm = compute_metrics(test_final, horizon, "test")
    persist_mae = round(compute_persistence_mae(val_final), 4)

    pd.DataFrame([vm, tm]).to_csv(out / "metrics.csv", index=False)

    # intermediate_metrics.csv: LSTM ~ v71 전 계층 persistence 진단
    persist_mae_sc = cand["persist_mae_sc"]
    finalize_intermediate = []
    for name, pred in [("v67_ridge", r_vp), ("v71_naive", n_vp)]:
        row = _inter_row(name, None, pred, cand["y_val"], persist_mae_sc)
        row["val_loss_history"] = ""
        finalize_intermediate.append(row)
    all_intermediate = (
        pass1_data["lstm_intermediate"]
        + cand["stage1_intermediate"]
        + finalize_intermediate
    )
    pd.DataFrame(all_intermediate).to_csv(out / "intermediate_metrics.csv", index=False)

    save_plots(val_final, test_final, horizon, urn, out, threshold)

    beats = "✓" if vm["beats_persistence"] else "✗"
    print(f" val_mae={vm['mae']:.1f} persist_mae={persist_mae:.1f} {beats}", flush=True)
    return {"meter_urn": urn, "horizon": horizon,
            "val_mae": vm["mae"], "val_rmse": vm["rmse"],
            "val_mape": vm["mape"], "val_wape": vm["wape"],
            "test_mae": tm["mae"], "test_rmse": tm["rmse"],
            "test_mape": tm["mape"], "test_wape": tm["wape"],
            "persistence_mae": persist_mae,
            "beats_persistence": vm["beats_persistence"],
            "v19": v19_version, "v57": v57_ver, "v63": v63_ver, "ridge_alpha": alpha}


# ────────────────────────────────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    set_seed(args.seed)
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        run_name = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        output_dir = ARTIFACTS_DIR / "candidate" / run_name
        print(f"[참고] --output-dir 미지정 → candidate 자동 생성: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    engine = build_engine()
    specs = _get_specs(args)
    horizon = args.horizon

    print(f"=== v84 파이프라인 학습 | {horizon}h | {len(specs)}개 계량기 ===\n")

    # ── 패스 1 ───────────────────────────────────────────────────────────────
    print("─── 패스 1: LSTM 학습 ───")
    pass1 = {}
    for spec in specs:
        try:
            pass1[spec.meter_urn] = pass1_train_meter(engine, spec, horizon, args, output_dir)
        except Exception as e:
            print(f"  [오류] {spec.meter_urn}: {e}")
            traceback.print_exc()

    # ── v19 그룹별 결정 ───────────────────────────────────────────────────────
    print("\n─── v19 그룹 선택 ───")
    v19_map: dict[tuple, str] = {}
    for group, role in sorted({(d["spec"].group, d["spec"].role) for d in pass1.values()}):
        v = select_v19(pass1, group, role)
        v19_map[(group, role)] = v
        cnt = sum(1 for d in pass1.values() if d["spec"].group == group and d["spec"].role == role)
        print(f"  {group}/{role} ({cnt}개) → {v}")

    # ── 패스 2a: v57 + v61 계산 (계량기별) ──────────────────────────────────
    print("\n─── 패스 2a: v57/v61 계산 ───")
    stage1: dict[str, dict] = {}
    for spec in specs:
        if spec.meter_urn not in pass1:
            continue
        v19 = v19_map.get((spec.group, spec.role), "v10")
        try:
            stage1[spec.meter_urn] = pass2_stage1(pass1[spec.meter_urn], args, v19, horizon, output_dir)
        except Exception as e:
            print(f"  [오류] {spec.meter_urn}: {e}")
            traceback.print_exc()

    # ── v63 라우팅 (그룹별: v57 vs v61) ──────────────────────────────────────
    print("\n─── v63 그룹 라우팅 ───")
    v63_versions: dict[str, str] = {}
    groups_seen = sorted({stage1[u]["spec"].group for u in stage1})
    for group in groups_seen:
        group_urns = [u for u in stage1 if stage1[u]["spec"].group == group]
        v57_preds = {u: stage1[u]["v57"] for u in group_urns}
        v61_preds = {u: stage1[u]["v61"] for u in group_urns if stage1[u]["v61"] is not None}
        routing = v63_route_group(
            v57_preds, v61_preds if v61_preds else None,
            group, horizon, specs,
        )
        v63_versions.update(routing)
        winner = next(iter(routing.values()), "v57")
        print(f"  {group} ({len(group_urns)}개) → v63={winner}")

    # ── 패스 2b: v84 앙상블 + 저장 (계량기별) ────────────────────────────────
    print("\n─── 패스 2b: 앙상블 + 저장 ───")
    rows = []
    for spec in specs:
        if spec.meter_urn not in stage1:
            continue
        cand    = stage1[spec.meter_urn]
        v63_ver = v63_versions.get(spec.meter_urn, "v57")
        if v63_ver == "v61" and cand.get("v61") is None:
            v63_ver = "v57"
        v63_vp  = cand[v63_ver]["val_pred_scaled"]
        v63_tp  = cand[v63_ver]["test_pred_scaled"]
        try:
            rows.append(pass2_finalize(pass1[spec.meter_urn], cand, v63_vp, v63_tp, v63_ver, args, output_dir))
        except Exception as e:
            print(f"  [오류] {spec.meter_urn}: {e}")
            traceback.print_exc()

    summary = pd.DataFrame(rows)
    summary.to_csv(output_dir / f"train_summary_{horizon}h.csv", index=False)
    print(f"\n=== 완료 ===")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
