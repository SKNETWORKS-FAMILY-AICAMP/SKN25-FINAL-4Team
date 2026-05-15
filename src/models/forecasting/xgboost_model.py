"""
XGBoost 예측 모델 — 계통 전력 소비 (grid_P) 24시간 예측.
lag 피처 + 날씨 + 시간 피처, MLflow 실험 추적.
"""

import pickle
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit

SAVE_DIR  = Path(__file__).parent / "saved"
MODEL_DIR = SAVE_DIR / "xgboost"
MLFLOW_URI = "sqlite:////Users/khl/workspace/final/mlruns.db"

TARGET = "grid_P"   # kW 단위로 변환 후 학습


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """시계열 lag + 날씨 + 시간 피처 생성."""
    d = df[["ts", TARGET, "Ta", "Igm"]].copy()
    d["ts"] = pd.to_datetime(d["ts"]).dt.tz_localize(None)
    d = d.sort_values("ts").set_index("ts")
    d[TARGET] = d[TARGET] / 1000   # W → kW

    # 시간 피처
    d["hour"]      = d.index.hour
    d["dayofweek"] = d.index.dayofweek
    d["month"]     = d.index.month
    d["is_weekend"]= (d.index.dayofweek >= 5).astype(int)

    # lag 피처 (1h / 24h / 168h = 1주)
    for lag in [1, 2, 3, 6, 12, 24, 48, 168]:
        d[f"lag_{lag}h"] = d[TARGET].shift(lag)

    # 롤링 평균
    d["roll_24h_mean"] = d[TARGET].shift(1).rolling(24).mean()
    d["roll_168h_mean"]= d[TARGET].shift(1).rolling(168).mean()

    # 날씨 결측 처리
    d["Ta"]  = d["Ta"].ffill().bfill()
    d["Igm"] = d["Igm"].ffill().fillna(0)

    return d.dropna().reset_index()


FEATURE_COLS = [
    "hour", "dayofweek", "month", "is_weekend",
    "lag_1h", "lag_2h", "lag_3h", "lag_6h", "lag_12h",
    "lag_24h", "lag_48h", "lag_168h",
    "roll_24h_mean", "roll_168h_mean",
    "Ta", "Igm",
]


def train(df: pd.DataFrame) -> xgb.XGBRegressor:
    """XGBoost 학습 + MLflow 기록."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    d = _build_features(df)

    X = d[FEATURE_COLS]
    y = d[TARGET]

    # TimeSeriesSplit으로 마지막 fold 검증
    tscv  = TimeSeriesSplit(n_splits=5)
    splits = list(tscv.split(X))
    tr_idx, val_idx = splits[-1]
    X_tr, X_val = X.iloc[tr_idx], X.iloc[val_idx]
    y_tr, y_val = y.iloc[tr_idx], y.iloc[val_idx]

    params = dict(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        n_jobs=-1,
    )

    mlflow.set_tracking_uri(MLFLOW_URI)
    mlflow.set_experiment("forecasting_xgboost")

    with mlflow.start_run(run_name="xgb_grid_P"):
        mlflow.log_params(params)
        mlflow.log_param("train_rows", len(X_tr))
        mlflow.log_param("val_rows",   len(X_val))
        mlflow.log_param("features",   len(FEATURE_COLS))

        model = xgb.XGBRegressor(**params)
        model.fit(X_tr, y_tr,
                  eval_set=[(X_val, y_val)],
                  verbose=False)

        preds = model.predict(X_val)
        mae   = mean_absolute_error(y_val, preds)
        rmse  = np.sqrt(mean_squared_error(y_val, preds))
        mape  = np.mean(np.abs((y_val - preds) / (y_val + 1e-6)))

        mlflow.log_metric("val_mae",  mae)
        mlflow.log_metric("val_rmse", rmse)
        mlflow.log_metric("val_mape", mape)

        # 피처 중요도
        imp = pd.Series(model.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
        mlflow.log_dict(imp.head(10).to_dict(), "feature_importance_top10.json")

        model.save_model(str(MODEL_DIR / "model.json"))
        mlflow.log_artifact(str(MODEL_DIR / "model.json"))

        print(f"XGBoost 학습 완료 — MAE={mae:.1f} kW  RMSE={rmse:.1f} kW  MAPE={mape:.3f}")
        print("피처 중요도 Top 5:")
        print(imp.head(5).to_string())

    return model


def predict(df: pd.DataFrame, hours: int = 24) -> pd.DataFrame:
    """
    저장된 모델로 rolling 예측 (최대 168h).
    반환: ts, yhat (kW)
    """
    model_path = MODEL_DIR / "model.json"
    if not model_path.exists():
        raise FileNotFoundError("XGBoost 모델 없음. train() 먼저 실행하세요.")

    model = xgb.XGBRegressor()
    model.load_model(str(model_path))

    # 피처 구성을 위해 원본 데이터 유지 + 예측값 채워넣기
    d = _build_features(df)
    last_row = d.iloc[-1]
    last_ts  = pd.to_datetime(last_row["ts"])

    records = []
    # rolling buffer: 이전 예측값으로 lag 채움
    history = d[TARGET].values.tolist()

    for i in range(1, hours + 1):
        next_ts = last_ts + pd.Timedelta(hours=i)
        row = {
            "hour":          next_ts.hour,
            "dayofweek":     next_ts.dayofweek,
            "month":         next_ts.month,
            "is_weekend":    int(next_ts.dayofweek >= 5),
            "lag_1h":        history[-1]   if len(history) >= 1   else 0,
            "lag_2h":        history[-2]   if len(history) >= 2   else 0,
            "lag_3h":        history[-3]   if len(history) >= 3   else 0,
            "lag_6h":        history[-6]   if len(history) >= 6   else 0,
            "lag_12h":       history[-12]  if len(history) >= 12  else 0,
            "lag_24h":       history[-24]  if len(history) >= 24  else 0,
            "lag_48h":       history[-48]  if len(history) >= 48  else 0,
            "lag_168h":      history[-168] if len(history) >= 168 else 0,
            "roll_24h_mean": np.mean(history[-24:])  if len(history) >= 24  else np.mean(history),
            "roll_168h_mean":np.mean(history[-168:]) if len(history) >= 168 else np.mean(history),
            "Ta":            float(d["Ta"].iloc[-1]),   # 마지막 알려진 값 사용
            "Igm":           0.0 if next_ts.hour < 6 or next_ts.hour > 20 else float(d["Igm"].mean()),
        }
        feat = pd.DataFrame([row])[FEATURE_COLS]
        yhat = float(model.predict(feat)[0])
        history.append(yhat)
        records.append({"ts": next_ts, "yhat": round(yhat, 2)})

    return pd.DataFrame(records)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from data.loader import load_range

    print("데이터 로드 중...")
    df = load_range("2018-01-01", "2024-01-01")
    print(f"  {len(df)}행 로드")

    print("XGBoost 학습 중...")
    model = train(df)

    print("24시간 예측...")
    fc = predict(df, hours=24)
    print(fc.to_string(index=False))
