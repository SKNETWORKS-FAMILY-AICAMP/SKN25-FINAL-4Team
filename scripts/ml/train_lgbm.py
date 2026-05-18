"""LightGBM 기반 Grid 전기 소비량 1시간 앞 예측 모델 학습.

Usage:
    uv run --with lightgbm --with pandas --with "psycopg[binary]" \
           --with python-dotenv --with scikit-learn \
           python scripts/ml/train_lgbm.py
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error

from data_loader import (
    FEATURE_COLS, TARGET_COL,
    add_features, get_train_val, load_raw,
)

OUT_DIR = Path("outputs/models")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    mask = y_true > 1.0  # 0에 가까운 값 제외
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def main() -> None:
    print("▶ 데이터 로드 중...")
    df = load_raw()
    df = add_features(df)
    train, val = get_train_val(df)

    X_train, y_train = train[FEATURE_COLS], train[TARGET_COL]
    X_val,   y_val   = val[FEATURE_COLS],   val[TARGET_COL]

    print(f"  학습: {train.index[0].date()} ~ {train.index[-1].date()} ({len(train):,}행)")
    print(f"  검증: {val.index[0].date()} ~ {val.index[-1].date()} ({len(val):,}행)")

    params = {
        "objective":      "regression",
        "metric":         "rmse",
        "learning_rate":  0.05,
        "num_leaves":     64,
        "max_depth":      -1,
        "min_child_samples": 20,
        "subsample":      0.8,
        "colsample_bytree": 0.8,
        "reg_alpha":      0.1,
        "reg_lambda":     0.1,
        "n_estimators":   1000,
        "early_stopping_rounds": 50,
        "verbose":        -1,
        "n_jobs":         -1,
        "random_state":   42,
    }

    print("▶ LightGBM 학습 중...")
    model = lgb.LGBMRegressor(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.log_evaluation(period=100)],
    )

    # 평가
    y_pred = model.predict(X_val)
    y_pred = np.maximum(y_pred, 0)

    mae  = mean_absolute_error(y_val, y_pred)
    rmse = np.sqrt(mean_squared_error(y_val, y_pred))
    mape_val = mape(y_val.values, y_pred)

    print(f"\n▶ 검증 결과")
    print(f"  MAE  : {mae:.2f} kW")
    print(f"  RMSE : {rmse:.2f} kW")
    print(f"  MAPE : {mape_val:.2f} %")
    print(f"  Best iteration: {model.best_iteration_}")

    # 피처 중요도 상위 10개
    importance = pd.Series(model.feature_importances_, index=FEATURE_COLS)
    print("\n▶ 피처 중요도 Top 10")
    print(importance.nlargest(10).to_string())

    # 모델 저장
    model_path = OUT_DIR / "lgbm_grid_electricity.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    print(f"\n▶ 모델 저장: {model_path}")

    # 결과 저장 (학습결과서용)
    metrics = {
        "model": "LightGBM",
        "target": "grid_kw (Grid 전기 소비량, kW)",
        "train_period": f"{train.index[0].date()} ~ {train.index[-1].date()}",
        "val_period":   f"{val.index[0].date()} ~ {val.index[-1].date()}",
        "train_rows": len(train),
        "val_rows":   len(val),
        "best_iteration": int(model.best_iteration_),
        "n_features": len(FEATURE_COLS),
        "metrics": {
            "MAE_kW":  round(mae, 4),
            "RMSE_kW": round(rmse, 4),
            "MAPE_pct": round(mape_val, 4),
        },
        "top_features": importance.nlargest(10).round(1).to_dict(),
        "params": {k: v for k, v in params.items()
                   if k not in ("verbose", "n_jobs", "random_state",
                                "early_stopping_rounds")},
    }
    metrics_path = OUT_DIR / "lgbm_metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"▶ 메트릭 저장: {metrics_path}")


if __name__ == "__main__":
    main()
