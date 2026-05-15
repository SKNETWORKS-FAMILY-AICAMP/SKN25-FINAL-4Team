"""
통계 기반 이상탐지 (1단계).
Z-score, IQR fence, STL 잔차 세 가지 방법을 조합한다.

핵심: Z-score/IQR는 hour × month 그룹 내에서 계산 (contextual).
전체 평균 대신 "겨울 밤 3시"끼리, "여름 낮 12시"끼리 비교해서
정상 계절·시간대 패턴이 이상으로 잡히는 것을 방지.

입력: ts 컬럼이 있는 DataFrame (loader.load_reduced 출력과 동일한 구조)
출력: 각 행에 anomaly_stat (bool), score_stat (float) 컬럼 추가
"""

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL


TARGET_COLS = ["grid_P", "pv_P", "chp_P", "cool_elec_P", "cop"]

Z_THRESH       = 3.0
IQR_MULT       = 3.0   # 통상값 3.0 (2.5는 오탐 과다)
STL_Z          = 3.0
MIN_GROUP_SIZE = 5     # 그룹 샘플 최솟값 — 미달 시 해당 그룹 스킵


# ── 컨텍스트 기반 (hour × month 그룹) ────────────────────────────────────────

def _contextual_zscore(series: pd.Series, ts: pd.Series, thresh: float = Z_THRESH) -> pd.Series:
    """hour × month 그룹 내 Z-score 이상 여부."""
    hour, month = ts.dt.hour, ts.dt.month
    flags = pd.Series(False, index=series.index)
    for (h, m), grp_idx in series.groupby([hour, month]).groups.items():
        s = series.loc[grp_idx].dropna()
        if len(s) < MIN_GROUP_SIZE:
            continue
        mu, sigma = s.mean(), s.std()
        if sigma == 0:
            continue
        flags.loc[grp_idx] = ((series.loc[grp_idx] - mu).abs() / sigma) > thresh
    return flags


def _contextual_iqr(series: pd.Series, ts: pd.Series, mult: float = IQR_MULT) -> pd.Series:
    """hour × month 그룹 내 IQR fence 이상 여부."""
    hour, month = ts.dt.hour, ts.dt.month
    flags = pd.Series(False, index=series.index)
    for (h, m), grp_idx in series.groupby([hour, month]).groups.items():
        s = series.loc[grp_idx].dropna()
        if len(s) < MIN_GROUP_SIZE:
            continue
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        flags.loc[grp_idx] = (
            (series.loc[grp_idx] < q1 - mult * iqr) |
            (series.loc[grp_idx] > q3 + mult * iqr)
        )
    return flags


# ── 전역 폴백 (ts 없을 때) ────────────────────────────────────────────────────

def _zscore_flags(series: pd.Series, thresh: float = Z_THRESH) -> pd.Series:
    mu, sigma = series.mean(), series.std()
    if sigma == 0:
        return pd.Series(False, index=series.index)
    return ((series - mu).abs() / sigma) > thresh


def _iqr_flags(series: pd.Series, mult: float = IQR_MULT) -> pd.Series:
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return pd.Series(False, index=series.index)
    return (series < q1 - mult * iqr) | (series > q3 + mult * iqr)


# ── STL 잔차 (계절 분해 후 잔차 Z-score) ─────────────────────────────────────

def _stl_residual_flags(series: pd.Series, period: int = 24,
                        z_thresh: float = STL_Z) -> pd.Series:
    """STL 분해 잔차 Z-score 이상 여부. NaN이 많으면 건너뜀."""
    clean = series.dropna()
    if len(clean) < period * 2:
        return pd.Series(False, index=series.index)
    try:
        stl = STL(clean, period=period, robust=True)
        res = stl.fit()
        resid = pd.Series(res.resid, index=clean.index)
        mu, sigma = resid.mean(), resid.std()
        if sigma == 0:
            return pd.Series(False, index=series.index)
        flags = ((resid - mu).abs() / sigma) > z_thresh
        return flags.reindex(series.index, fill_value=False)
    except Exception:
        return pd.Series(False, index=series.index)


# ── 메인 탐지 함수 ────────────────────────────────────────────────────────────

def detect(df: pd.DataFrame) -> pd.DataFrame:
    """
    통계 기반 이상탐지 수행.

    ts 컬럼이 있으면 hour × month 컨텍스트 기반 Z-score/IQR 사용.
    없으면 전역 통계 폴백.

    반환: 원본 df에 아래 컬럼 추가
      - anomaly_stat  : bool  — 2개 이상 방법에서 이상 탐지
      - score_stat    : float — 이상 탐지된 (방법 × 컬럼) 수
      - flag_<col>    : bool  — 컬럼별 이상 여부 (디버깅용)
    """
    result = df.copy()
    flag_cols = []

    has_ts = "ts" in result.columns
    ts_parsed = pd.to_datetime(result["ts"]) if has_ts else None

    for col in TARGET_COLS:
        if col not in result.columns:
            continue
        series = result[col].copy()

        # pv_P 야간 NaN은 정상 → 분석 제외
        if col == "pv_P":
            series = series.dropna()

        if has_ts:
            ts_for_col = ts_parsed.loc[series.index]
            z   = _contextual_zscore(series, ts_for_col)
            iqr = _contextual_iqr(series, ts_for_col)
        else:
            z   = _zscore_flags(series)
            iqr = _iqr_flags(series)

        stl = _stl_residual_flags(series)

        combined = (z | iqr | stl).reindex(result.index, fill_value=False)
        fname = f"flag_{col}"
        result[fname] = combined
        flag_cols.append(fname)

    if flag_cols:
        result["score_stat"]  = result[flag_cols].sum(axis=1).astype(float)
        result["anomaly_stat"] = result["score_stat"] >= 2  # 최소 2개 방법 동의
    else:
        result["score_stat"]  = 0.0
        result["anomaly_stat"] = False

    return result


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from data.loader import load_range

    df = load_range("2022-07-01", "2022-08-01")
    out = detect(df)
    n = out["anomaly_stat"].sum()
    print(f"탐지된 이상: {n}건 / {len(out)}행 ({n/len(out):.1%})")
    print(out[out["anomaly_stat"]][["ts", "score_stat"] + [f"flag_{c}" for c in TARGET_COLS if f"flag_{c}" in out.columns]].head(10).to_string(index=False))
