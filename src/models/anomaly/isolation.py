"""
Isolation Forest 기반 이상탐지 (2단계).
다변량 피처(전기·냉방·날씨)를 동시에 입력해 잠재적 이상 패턴 포착.

학습/추론 분리 구조:
  - fit(df)   : 정상 구간 데이터로 모델 학습, 파일에 저장
  - detect(df): 저장된 모델로 이상 점수 계산
"""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

MODEL_PATH  = Path(__file__).parent / "saved" / "isolation_forest.pkl"
SCALER_PATH = Path(__file__).parent / "saved" / "iso_scaler.pkl"

# 다변량 피처 — P(전력) + 날씨 + COP
FEATURE_COLS = ["grid_P", "pv_P", "chp_P", "cool_elec_P", "cool_output_P", "Ta", "cop"]

# 오염률: 전체의 약 2~3% 이상 예상 (FN 최소화)
CONTAMINATION = 0.02


def _prepare_features(df: pd.DataFrame) -> tuple[np.ndarray, pd.Index]:
    """피처 행렬 준비. 발전량 NaN→0, 나머지 ffill/bfill."""
    cols = [c for c in FEATURE_COLS if c in df.columns]
    sub  = df[cols].copy()

    # 발전량 미터 미수집 구간은 0으로 처리 (장치 정지 = 0)
    for c in ["pv_P", "chp_P"]:
        if c in sub.columns:
            sub[c] = sub[c].fillna(0.0)

    sub = sub.ffill().bfill().fillna(0.0)

    # 시간 피처 추가 (주간/계절 패턴 반영)
    if "ts" in df.columns:
        ts = pd.to_datetime(df["ts"])
        sub["hour"]  = ts.dt.hour
        sub["month"] = ts.dt.month

    return sub.values, df.index


def fit(df: pd.DataFrame, contamination: float = CONTAMINATION) -> None:
    """
    Isolation Forest 학습 및 저장.
    df: 정상 구간 대표 데이터 (loader.load_range 출력)
    """
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    X, _ = _prepare_features(df)
    if len(X) < 100:
        raise ValueError(f"학습 데이터 부족: {len(X)}행 (최소 100행 필요)")

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_scaled)

    with open(MODEL_PATH,  "wb") as f: pickle.dump(model,  f)
    with open(SCALER_PATH, "wb") as f: pickle.dump(scaler, f)
    print(f"Isolation Forest 학습 완료 ({len(X)}행) → {MODEL_PATH}")


def detect(df: pd.DataFrame) -> pd.DataFrame:
    """
    저장된 모델로 이상 탐지.
    모델이 없으면 자동으로 전체 df로 학습 후 탐지.

    반환: 원본 df에 아래 컬럼 추가
      - anomaly_iso : bool
      - score_iso   : float (낮을수록 이상 — sklearn 부호 반전해 양수 이상점수로 변환)
    """
    result = df.copy()
    result["anomaly_iso"] = False
    result["score_iso"]   = 0.0

    if not MODEL_PATH.exists():
        print("[IsolationForest] 저장된 모델 없음 → 현재 데이터로 학습")
        fit(df)

    with open(MODEL_PATH,  "rb") as f: model  = pickle.load(f)
    with open(SCALER_PATH, "rb") as f: scaler = pickle.load(f)

    X, valid_idx = _prepare_features(df)
    if len(X) == 0:
        return result

    # 피처 수 불일치 시 재학습
    if X.shape[1] != scaler.n_features_in_:
        print(f"[IsolationForest] 피처 수 불일치({X.shape[1]} vs {scaler.n_features_in_}) → 재학습")
        fit(df)
        with open(MODEL_PATH,  "rb") as f: model  = pickle.load(f)
        with open(SCALER_PATH, "rb") as f: scaler = pickle.load(f)

    X_scaled = scaler.transform(X)
    preds    = model.predict(X_scaled)          # 1=정상, -1=이상
    scores   = -model.score_samples(X_scaled)   # 양수 = 더 이상

    result.loc[valid_idx, "anomaly_iso"] = preds == -1
    result.loc[valid_idx, "score_iso"]   = scores

    return result


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from data.loader import load_range

    df = load_range("2022-01-01", "2023-01-01")
    print(f"학습 데이터: {len(df)}행")
    fit(df)

    df_test = load_range("2022-07-01", "2022-08-01")
    out = detect(df_test)
    n = out["anomaly_iso"].sum()
    print(f"탐지된 이상: {n}건 / {len(out)}행 ({n/len(out):.1%})")
    print(out[out["anomaly_iso"]][["ts", "score_iso"]].head(10).to_string(index=False))
