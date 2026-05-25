"""LightGBM 기반 Grid 전기 소비량 1시간 앞 예측 모델 학습.

Usage:
    uv run --with lightgbm --with pandas --with "psycopg[binary]" \
           --with python-dotenv --with scikit-learn --with mlflow \
           python scripts/ml/train_lgbm.py
"""

from __future__ import annotations

import json
import os
import pickle
from pathlib import Path

import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import MinMaxScaler

from data_loader import FEATURE_COLS, TARGET_COL, add_features, get_splits, load_raw

load_dotenv()

OUT_DIR = Path("outputs/models")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    mask = y_true > 1.0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def main() -> None:
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", ""))
    mlflow.set_experiment("SSA-IPSO-LSTM")

    print("▶ 데이터 로드 중...")
    df = load_raw()
    df = add_features(df)
    train, val, test = get_splits(df)

    print(f"  학습: {train.index[0].date()} ~ {train.index[-1].date()} ({len(train):,}행)")
    print(f"  검증: {val.index[0].date()} ~ {val.index[-1].date()} ({len(val):,}행)")
    print(f"  테스트: {test.index[0].date()} ~ {test.index[-1].date()} ({len(test):,}행)")

    scaler = MinMaxScaler()
    X_train = scaler.fit_transform(train[FEATURE_COLS])
    X_val   = scaler.transform(val[FEATURE_COLS])
    X_test  = scaler.transform(test[FEATURE_COLS])

    y_train = train[TARGET_COL].values
    y_val   = val[TARGET_COL].values
    y_test  = test[TARGET_COL].values

    X_train_df = pd.DataFrame(X_train, columns=FEATURE_COLS)
    X_val_df   = pd.DataFrame(X_val,   columns=FEATURE_COLS)
    X_test_df  = pd.DataFrame(X_test,  columns=FEATURE_COLS)

    params = {
        "objective":             "regression",
        "metric":                "rmse",
        "learning_rate":         0.05,
        "num_leaves":            64,
        "max_depth":             -1,
        "min_child_samples":     20,
        "subsample":             0.8,
        "colsample_bytree":      0.8,
        "reg_alpha":             0.1,
        "reg_lambda":            0.1,
        "n_estimators":          1000,
        "early_stopping_rounds": 50,
        "verbose":               -1,
        "n_jobs":                -1,
        "random_state":          42,
    }

    with mlflow.start_run(run_name="LightGBM"):
        mlflow.log_params({k: v for k, v in params.items()
                           if k not in ("verbose", "n_jobs", "random_state",
                                        "early_stopping_rounds")})

        print("▶ LightGBM 학습 중...")
        model = lgb.LGBMRegressor(**params)
        model.fit(
            X_train_df, y_train,
            eval_set=[(X_val_df, y_val)],
            callbacks=[lgb.log_evaluation(period=100)],
        )

        for split_name, X_s, y_s in [("val", X_val_df, y_val), ("test", X_test_df, y_test)]:
            y_pred = np.maximum(model.predict(X_s), 0)
            mae_v  = mean_absolute_error(y_s, y_pred)
            rmse_v = np.sqrt(mean_squared_error(y_s, y_pred))
            mape_v = mape(y_s, y_pred)
            print(f"\n▶ {split_name} 결과")
            print(f"  MAE  : {mae_v:.2f} W")
            print(f"  RMSE : {rmse_v:.2f} W")
            print(f"  MAPE : {mape_v:.2f} %")
            mlflow.log_metrics({
                f"{split_name}_mae":  round(mae_v, 4),
                f"{split_name}_rmse": round(rmse_v, 4),
                f"{split_name}_mape": round(mape_v, 4),
            })

        # 피처 중요도
        importance = pd.Series(model.feature_importances_, index=FEATURE_COLS)
        print("\n▶ 피처 중요도 Top 10")
        print(importance.nlargest(10).to_string())

        # 저장
        model_path = OUT_DIR / "lgbm_grid_electricity.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)

        scaler_path = OUT_DIR / "lgbm_scaler.pkl"
        with open(scaler_path, "wb") as f:
            pickle.dump(scaler, f)

        mlflow.log_artifact(str(model_path))
        mlflow.log_artifact(str(scaler_path))
        print(f"\n▶ 모델 저장: {model_path}")

        metrics = {
            "model":  "LightGBM",
            "target": "grid_P (W)",
            "best_iteration": int(model.best_iteration_),
            "top_features": importance.nlargest(10).round(1).to_dict(),
        }
        metrics_path = OUT_DIR / "lgbm_metrics.json"
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        mlflow.log_artifact(str(metrics_path))


if __name__ == "__main__":
    main()
