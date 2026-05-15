"""EMS 이상탐지 모듈.
 
analysis_requirements.md 3절 기준 단계적 앙상블 이상탐지를 구현한다.
 
단계:
    1. 통계 기준 (Z-score, IQR, rolling deviation)
    2. 계절 보정 (STL)
    3. 다변량 모델 (Isolation Forest)
    4. 복원 기반 모델 (LSTM Autoencoder)
    5. 앙상블 결합 및 신뢰도 등급 산출
 
입력 원천: DB ems.cr_measurement_15min (data_contract.md 기준)
출력: outputs/ 파일 기반 (DB write 승인 전)
"""
 
from __future__ import annotations
 
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
 
import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.tsa.seasonal import STL
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
 
logger = logging.getLogger(__name__)
 
AnomalyLevel = Literal["high", "medium", "low", "normal"]
 
 
# ---------------------------------------------------------------------------
# 결과 데이터 구조
# ---------------------------------------------------------------------------
 
@dataclass
class AnomalyResult:
    """이상탐지 단계별 결과 및 앙상블 신뢰도."""
 
    meter_urn: str
    measurement: str
    resolution_code: str
    start_ts: str
    end_ts: str
    method: str
    anomaly_flags: pd.Series        # index=ts, bool
    scores: pd.Series | None        # index=ts, float (선택)
    threshold: float | None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
 
 
@dataclass
class EnsembleResult:
    """앙상블 결합 결과."""
 
    meter_urn: str
    measurement: str
    ts: pd.DatetimeIndex
    flag_zscore: pd.Series
    flag_iqr: pd.Series
    flag_rolling: pd.Series
    flag_stl: pd.Series
    flag_iforest: pd.Series
    ensemble_score: pd.Series       # 0~5 합산
    anomaly_level: pd.Series        # high/medium/low/normal
 
 
# ---------------------------------------------------------------------------
# 1단계: 통계 기준 이상탐지
# ---------------------------------------------------------------------------
 
def detect_zscore(
    series: pd.Series,
    window: int | None = None,
    threshold: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """Z-score 기반 이상탐지.
 
    Parameters
    ----------
    series:
        시계열 값. index는 DatetimeIndex.
    window:
        None이면 전체 구간 기준. 정수이면 rolling Z-score.
    threshold:
        이상치 판단 Z-score 임계값 (기본 3.0).
 
    Returns
    -------
    (flag, z_scores)
    """
    if window is None:
        z_scores = pd.Series(
            np.abs(stats.zscore(series.fillna(series.median()))),
            index=series.index,
        )
    else:
        rolling_mean = series.rolling(window=window, min_periods=1).mean()
        rolling_std = series.rolling(window=window, min_periods=1).std().replace(0, np.nan)
        z_scores = ((series - rolling_mean) / rolling_std).abs().fillna(0)
 
    flag = z_scores > threshold
    return flag, z_scores
 
 
def detect_iqr(
    series: pd.Series,
    multiplier: float = 1.5,
) -> tuple[pd.Series, float, float]:
    """IQR 기반 이상탐지.
 
    Returns
    -------
    (flag, lower_bound, upper_bound)
    """
    q1 = series.quantile(0.25)
    q3 = series.quantile(0.75)
    iqr = q3 - q1
    lower = q1 - multiplier * iqr
    upper = q3 + multiplier * iqr
    flag = (series < lower) | (series > upper)
    return flag, lower, upper
 
 
def detect_rolling_deviation(
    series: pd.Series,
    window: int = 96,           # 15min 기준 24시간
    threshold_sigma: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """Rolling 편차 기반 이상탐지.
 
    Returns
    -------
    (flag, deviation_scores)
    """
    rolling_mean = series.rolling(window=window, min_periods=1).mean()
    rolling_std = series.rolling(window=window, min_periods=1).std().replace(0, np.nan)
    deviation = ((series - rolling_mean) / rolling_std).abs().fillna(0)
    flag = deviation > threshold_sigma
    return flag, deviation
 
 
# ---------------------------------------------------------------------------
# 2단계: STL 계절 보정 이상탐지
# ---------------------------------------------------------------------------
 
def detect_stl(
    series: pd.Series,
    period: int = 96,           # 15min 기준 1일 주기
    threshold_sigma: float = 3.0,
    robust: bool = True,
) -> tuple[pd.Series, pd.Series]:
    """STL 분해 후 잔차 기반 이상탐지.
 
    Parameters
    ----------
    period:
        계절 주기. 15min 기준 일주기=96, 주주기=672.
    robust:
        True이면 이상치에 강건한 STL 사용.
 
    Returns
    -------
    (flag, residuals)
    """
    filled = series.ffill().bfill()
    if filled.isna().all():
        logger.warning("STL: 전체 결측 구간. 플래그 없음.")
        return pd.Series(False, index=series.index), pd.Series(0.0, index=series.index)
 
    try:
        stl = STL(filled, period=period, robust=robust)
        result = stl.fit()
        residuals = pd.Series(result.resid, index=series.index)
        resid_std = residuals.std()
        if resid_std == 0:
            flag = pd.Series(False, index=series.index)
        else:
            flag = (residuals.abs() / resid_std) > threshold_sigma
        return flag, residuals
    except Exception as exc:  # noqa: BLE001
        logger.warning("STL 실패: %s. rolling deviation으로 대체합니다.", exc)
        return detect_rolling_deviation(series, window=period, threshold_sigma=threshold_sigma)
 
 
# ---------------------------------------------------------------------------
# 3단계: Isolation Forest 다변량 이상탐지
# ---------------------------------------------------------------------------
 
def detect_isolation_forest(
    df: pd.DataFrame,
    feature_cols: list[str],
    contamination: float = 0.05,
    random_state: int = 42,
) -> tuple[pd.Series, pd.Series]:
    """Isolation Forest 다변량 이상탐지.
 
    Parameters
    ----------
    df:
        wide format DataFrame. index는 DatetimeIndex 또는 ts column 포함.
    feature_cols:
        사용할 피처 컬럼 목록.
    contamination:
        예상 이상치 비율 (기본 0.05).
 
    Returns
    -------
    (flag, scores)
        scores는 음수일수록 이상치에 가깝다.
    """
    X = df[feature_cols].copy()
 
    # 결측 처리: 중앙값 대체
    X = X.fillna(X.median())
 
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
 
    clf = IsolationForest(
        contamination=contamination,
        random_state=random_state,
        n_jobs=-1,
    )
    predictions = clf.fit_predict(X_scaled)
    scores = pd.Series(clf.score_samples(X_scaled), index=df.index)
    flag = pd.Series(predictions == -1, index=df.index)
 
    logger.info(
        "Isolation Forest: 이상 %d건 / 전체 %d건 (%.2f%%)",
        flag.sum(),
        len(flag),
        flag.mean() * 100,
    )
    return flag, scores
 
 
# ---------------------------------------------------------------------------
# 4단계: LSTM Autoencoder 복원 기반 이상탐지
# ---------------------------------------------------------------------------
 
def detect_lstm_autoencoder(
    series: pd.Series,
    sequence_length: int = 96,      # 15min 기준 24시간
    epochs: int = 30,
    batch_size: int = 64,
    threshold_percentile: float = 95.0,
    hidden_dim: int = 32,
) -> tuple[pd.Series, pd.Series, float]:
    """LSTM Autoencoder 복원 오차 기반 이상탐지.
 
    PyTorch 기반 구현. GPU 사용 가능하면 자동 선택.
 
    Returns
    -------
    (flag, reconstruction_errors, threshold)
    """
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError:
        logger.error("PyTorch가 설치되지 않았습니다. LSTM Autoencoder를 건너뜁니다.")
        empty = pd.Series(False, index=series.index)
        return empty, pd.Series(0.0, index=series.index), 0.0
 
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("LSTM Autoencoder 학습 디바이스: %s", device)
 
    # 정규화
    filled = series.ffill().bfill().values.astype(np.float32)
    scaler_min = filled.min()
    scaler_max = filled.max()
    scaler_range = scaler_max - scaler_min if scaler_max != scaler_min else 1.0
    normalized = (filled - scaler_min) / scaler_range
 
    # sequence 생성
    sequences = _make_sequences(normalized, sequence_length)
    if len(sequences) == 0:
        logger.warning("LSTM Autoencoder: sequence 생성 실패. 데이터 길이 부족.")
        empty = pd.Series(False, index=series.index)
        return empty, pd.Series(0.0, index=series.index), 0.0
 
    X_tensor = torch.FloatTensor(sequences).unsqueeze(-1).to(device)
    dataset = TensorDataset(X_tensor, X_tensor)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
 
    # 모델 정의
    class LSTMAutoencoder(nn.Module):
        def __init__(self, input_dim: int, hidden_dim: int) -> None:
            super().__init__()
            self.encoder = nn.LSTM(input_dim, hidden_dim, batch_first=True)
            self.decoder = nn.LSTM(hidden_dim, input_dim, batch_first=True)
 
        def forward(self, x: torch.Tensor) -> torch.Tensor:
            _, (hidden, _) = self.encoder(x)
            repeated = hidden.permute(1, 0, 2).repeat(1, x.size(1), 1)
            output, _ = self.decoder(repeated)
            return output
 
    model = LSTMAutoencoder(input_dim=1, hidden_dim=hidden_dim).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.MSELoss()
 
    model.train()
    for epoch in range(epochs):
        epoch_loss = 0.0
        for batch_x, batch_y in loader:
            optimizer.zero_grad()
            output = model(batch_x)
            loss = criterion(output, batch_y)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
        if (epoch + 1) % 10 == 0:
            logger.info("LSTM AE epoch %d/%d loss=%.6f", epoch + 1, epochs, epoch_loss / len(loader))
 
    # 복원 오차 계산
    model.eval()
    errors = np.zeros(len(normalized))
    with torch.no_grad():
        for i in range(len(sequences)):
            seq = torch.FloatTensor(sequences[i]).unsqueeze(0).unsqueeze(-1).to(device)
            recon = model(seq).squeeze().cpu().numpy()
            mse = np.mean((sequences[i] - recon) ** 2)
            errors[i + sequence_length - 1] = mse
 
    error_series = pd.Series(errors, index=series.index)
    threshold = float(np.percentile(errors[errors > 0], threshold_percentile))
    flag = error_series > threshold
 
    logger.info(
        "LSTM AE: 이상 %d건 / 전체 %d건 (threshold=%.6f)",
        flag.sum(),
        len(flag),
        threshold,
    )
    return flag, error_series, threshold
 
 
def _make_sequences(data: np.ndarray, length: int) -> np.ndarray:
    sequences = []
    for i in range(length, len(data) + 1):
        sequences.append(data[i - length:i])
    return np.array(sequences)
 
 
# ---------------------------------------------------------------------------
# 5단계: 앙상블 결합
# ---------------------------------------------------------------------------
 
def build_ensemble(
    ts_index: pd.DatetimeIndex,
    flag_zscore: pd.Series,
    flag_iqr: pd.Series,
    flag_rolling: pd.Series,
    flag_stl: pd.Series,
    flag_iforest: pd.Series,
    meter_urn: str,
    measurement: str,
    high_threshold: int = 4,
    medium_threshold: int = 2,
) -> EnsembleResult:
    """이상탐지 결과 앙상블 결합.
 
    5개 방법의 flag 합산으로 신뢰도 등급을 산출한다.
 
    | 합산 점수 | 등급   |
    |-----------|--------|
    | 4~5       | high   |
    | 2~3       | medium |
    | 1         | low    |
    | 0         | normal |
    """
    score = (
        flag_zscore.astype(int)
        + flag_iqr.astype(int)
        + flag_rolling.astype(int)
        + flag_stl.astype(int)
        + flag_iforest.astype(int)
    )
 
    def _level(s: int) -> AnomalyLevel:
        if s >= high_threshold:
            return "high"
        if s >= medium_threshold:
            return "medium"
        if s == 1:
            return "low"
        return "normal"
 
    anomaly_level = score.map(_level)
 
    logger.info(
        "앙상블 결과 meter=%s measurement=%s | high=%d medium=%d low=%d",
        meter_urn,
        measurement,
        (anomaly_level == "high").sum(),
        (anomaly_level == "medium").sum(),
        (anomaly_level == "low").sum(),
    )
 
    return EnsembleResult(
        meter_urn=meter_urn,
        measurement=measurement,
        ts=ts_index,
        flag_zscore=flag_zscore,
        flag_iqr=flag_iqr,
        flag_rolling=flag_rolling,
        flag_stl=flag_stl,
        flag_iforest=flag_iforest,
        ensemble_score=score,
        anomaly_level=anomaly_level,
    )
 
 
# ---------------------------------------------------------------------------
# 통합 실행 함수
# ---------------------------------------------------------------------------
 
def run_anomaly_pipeline(
    series: pd.Series,
    meter_urn: str,
    measurement: str,
    resolution_code: str = "15min",
    use_lstm: bool = False,
    iforest_features: pd.DataFrame | None = None,
    iforest_feature_cols: list[str] | None = None,
) -> EnsembleResult:
    """단계적 앙상블 이상탐지 파이프라인 전체 실행.
 
    Parameters
    ----------
    series:
        단변량 시계열. index는 DatetimeIndex(UTC).
    meter_urn, measurement:
        대상 계량기 및 측정 항목.
    use_lstm:
        True이면 LSTM Autoencoder 단계 포함. 기본 False(시간 절약).
    iforest_features:
        Isolation Forest용 wide format DataFrame. None이면 series만 사용.
    iforest_feature_cols:
        Isolation Forest 피처 컬럼 목록.
    """
    logger.info("이상탐지 시작: meter=%s measurement=%s rows=%d", meter_urn, measurement, len(series))
 
    # 1단계: 통계 기준
    flag_zscore, _ = detect_zscore(series)
    flag_iqr, _, _ = detect_iqr(series)
    flag_rolling, _ = detect_rolling_deviation(series)
 
    # 2단계: STL
    period = 96 if resolution_code == "15min" else 24
    flag_stl, _ = detect_stl(series, period=period)
 
    # 3단계: Isolation Forest
    if iforest_features is not None and iforest_feature_cols:
        flag_iforest, _ = detect_isolation_forest(iforest_features, iforest_feature_cols)
        flag_iforest = flag_iforest.reindex(series.index, fill_value=False)
    else:
        # 단변량으로 대체
        df_single = series.to_frame(name=measurement)
        flag_iforest, _ = detect_isolation_forest(df_single, [measurement])
 
    # 4단계: LSTM Autoencoder (선택)
    if use_lstm:
        flag_lstm, _, _ = detect_lstm_autoencoder(series)
        # LSTM 결과를 iforest 대신 추가 점수로 반영
        flag_iforest = flag_iforest | flag_lstm
 
    # 5단계: 앙상블
    result = build_ensemble(
        ts_index=series.index,
        flag_zscore=flag_zscore,
        flag_iqr=flag_iqr,
        flag_rolling=flag_rolling,
        flag_stl=flag_stl,
        flag_iforest=flag_iforest,
        meter_urn=meter_urn,
        measurement=measurement,
    )
    return result
 
 
# ---------------------------------------------------------------------------
# 결과 저장
# ---------------------------------------------------------------------------
 
def save_anomaly_result(
    result: EnsembleResult,
    output_dir: Path,
) -> Path:
    """앙상블 결과를 CSV로 저장한다."""
    output_dir.mkdir(parents=True, exist_ok=True)
 
    df = pd.DataFrame({
        "ts": result.ts,
        "meter_urn": result.meter_urn,
        "measurement": result.measurement,
        "flag_zscore": result.flag_zscore.values,
        "flag_iqr": result.flag_iqr.values,
        "flag_rolling": result.flag_rolling.values,
        "flag_stl": result.flag_stl.values,
        "flag_iforest": result.flag_iforest.values,
        "ensemble_score": result.ensemble_score.values,
        "anomaly_level": result.anomaly_level.values,
    })
 
    meter_code = result.meter_urn.replace(".", "_")
    filename = f"anomaly_{meter_code}_{result.measurement}.csv"
    path = output_dir / filename
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("이상탐지 결과 저장: %s (%d rows)", path, len(df))
    return path
 
 
def save_anomaly_summary(
    results: list[EnsembleResult],
    output_dir: Path,
) -> Path:
    """전체 계량기 이상탐지 요약을 저장한다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in results:
        rows.append({
            "meter_urn": r.meter_urn,
            "measurement": r.measurement,
            "total_rows": len(r.ts),
            "anomaly_high": int((r.anomaly_level == "high").sum()),
            "anomaly_medium": int((r.anomaly_level == "medium").sum()),
            "anomaly_low": int((r.anomaly_level == "low").sum()),
            "anomaly_rate": round((r.ensemble_score > 0).mean(), 6),
        })
    summary_df = pd.DataFrame(rows)
    path = output_dir / "anomaly_summary.csv"
    summary_df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("이상탐지 요약 저장: %s", path)
    return path
 