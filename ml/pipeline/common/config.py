"""파이프라인 전체 상수 + MeterSpec 정의. ml: 잔차(P(t)-P(t-1)) 타겟 실험."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# ── 경로 ─────────────────────────────────────────────────────────────────────
PIPELINE_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = PIPELINE_ROOT / "artifacts"

# ── 데이터 split ─────────────────────────────────────────────────────────────
TARGET_COLUMN = "P"
TRAIN_START = pd.Timestamp("2018-01-01 00:00:00", tz="UTC")
VAL_START   = pd.Timestamp("2022-01-01 00:00:00", tz="UTC")
TEST_START  = pd.Timestamp("2023-01-01 00:00:00", tz="UTC")
END_TS      = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")

# ── 모델 하이퍼파라미터 ───────────────────────────────────────────────────────
SEED            = 42
HIDDEN_SIZE     = 32
EPOCHS          = 12
BATCH_SIZE      = 256
PATIENCE        = 4
LEARNING_RATE   = 1e-3
WINDOW_SIZE     = 24
MAX_FFILL_HOURS = 3
MODEL_DROPOUT            = 0.0
REGULARIZED_MODEL_DROPOUT = 0.2
TRAINING_LOSS   = "mse"
SMOOTH_L1_BETA  = 1.0
VAL_THRESHOLD_QUANTILE       = 0.995
ROBUST_THRESHOLD_MAD_MULTIPLIER = 6.0

# ── 피처 ─────────────────────────────────────────────────────────────────────
TIME_FEATURE_COLUMNS = (
    "hour_sin", "hour_cos",
    "day_of_week_sin", "day_of_week_cos",
    "month_sin", "month_cos",
)

# ── 잔차 타겟 ─────────────────────────────────────────────────────────────────
# True: 타겟 = P(t) - P(t-1) (lag-1 잔차), 복원 시 anchor(P(t-1)) 더함
USE_RESIDUAL_TARGET = True

# ── 파생변수 ──────────────────────────────────────────────────────────────────
USE_DERIVED_FEATURES = True
DERIVED_FEATURE_COLUMNS = (
    "diff_lag24",       # P(t) - P(t-24): 어제 같은 시각 대비 차분
    "diff_lag168",      # P(t) - P(t-168): 지난주 같은 시각 대비 차분
    "is_workday",       # 1=월~금, 0=토~일
    "rolling_mean_24h", # 최근 24h P 이동평균
)
ELECTRIC_MEASUREMENTS = ("P", "U1", "PF")
THERMAL_MEASUREMENTS  = ("P", "qv", "Tdiff")

# ── 라우팅 임계값 (test2 동일) ───────────────────────────────────────────────
V25_MIN_MEDIAN_IMPROVEMENT       = 0.001
V52_BIAS_GATE_MIN_MAE_IMPROVEMENT = 0.0
V57_MIN_METER_IMPROVEMENT        = 0.0025
V63_MIN_GROUP_IMPROVEMENT        = 0.001
V84_CORRECTION_GAIN              = 1.30
V84_SHRINKAGE_PRIOR_ROWS         = 168.0


# ── MeterSpec ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MeterSpec:
    meter_urn: str
    group: str
    role: str
    features: tuple[str, ...]
    source: str
    note: str


# ── LSTM variant (학습에 필요한 정보만) ──────────────────────────────────────
@dataclass(frozen=True)
class LSTMVariant:
    version: str
    window_size: int = WINDOW_SIZE
    use_time_features: bool = False
    model_architecture: str = "lstm"
    model_dropout: float = MODEL_DROPOUT
    training_loss: str = TRAINING_LOSS
    smooth_l1_beta: float = SMOOTH_L1_BETA


V1 = LSTMVariant("v1", window_size=24, use_time_features=False)
V2 = LSTMVariant("v2", window_size=24, use_time_features=True)
V3 = LSTMVariant("v3", window_size=168, use_time_features=True)
V4 = LSTMVariant("v4", window_size=24, use_time_features=True, model_architecture="gru")
V6 = LSTMVariant("v6", window_size=24, use_time_features=True, model_dropout=REGULARIZED_MODEL_DROPOUT)
V7 = LSTMVariant("v7", window_size=24, use_time_features=True, training_loss="smooth_l1")

LSTM_VARIANTS = (V1, V2, V3, V4, V6, V7)


# ── 계량기 스펙 ───────────────────────────────────────────────────────────────
ELECTRIC_REPRESENTATIVE_SPECS: tuple[MeterSpec, ...] = (
    MeterSpec("H2.Z66",    "electric", "representative", ("P","U1","PF"), "C1",  "consumption cluster representative"),
    MeterSpec("H2.ZE66",   "electric", "representative", ("P","U1"),      "C2",  "ZE consumption cluster representative"),
    MeterSpec("H1.Z12",    "electric", "representative", ("P","U1","PF"), "C3",  "consumption cluster representative"),
    MeterSpec("H4.Z51",    "electric", "representative", ("P","U1","PF"), "C4",  "consumption cluster representative"),
    MeterSpec("H2.T.Z31",  "electric", "representative", ("P","U1","PF"), "C5",  "consumption cluster representative"),
    MeterSpec("H1.Z13",    "electric", "representative", ("P","U1","PF"), "C6",  "consumption cluster representative"),
    MeterSpec("H1.Z21",    "electric", "representative", ("P","U1","PF"), "C7",  "consumption cluster representative"),
    MeterSpec("H1.Z24",    "electric", "representative", ("P","U1","PF"), "C8",  "consumption cluster representative"),
    MeterSpec("H2.Z64",    "electric", "representative", ("P","U1","PF"), "C9",  "consumption cluster representative"),
    MeterSpec("H3.Z43",    "electric", "representative", ("P","U1"),      "C10", "conditional representative"),
    MeterSpec("H3.Z44",    "electric", "representative", ("P","U1"),      "C11", "conditional representative"),
    MeterSpec("H3.Z48",    "electric", "representative", ("P","U1","PF"), "C12", "consumption cluster representative"),
    MeterSpec("H4.Z50",    "electric", "representative", ("P","U1","PF"), "C13", "consumption cluster representative"),
    MeterSpec("V.Z84",     "electric", "representative", ("P","U1"),      "P1",  "production cluster representative"),
    MeterSpec("H1.Z20",    "electric", "representative", ("P","U1","PF"), "P2",  "production cluster representative"),
)

ELECTRIC_SINGLETON_SPECS: tuple[MeterSpec, ...] = tuple(
    MeterSpec(m, "electric", "singleton", ("P","U1","PF"), "singleton", "electric singleton meter")
    for m in (
        "H1.Z10","H1.Z16","H1.Z18","H1.Z19","H1.Z23",
        "H1.Z26","H1.Z27","H2.Z61","H2.Z62","H2.Z63",
        "H2.Z65","H2.Z68","H2.Z69","H2.ZE65","H2.ZE74",
        "H3.Z42","H3.Z45","H3.Z46","H3.Z47","H3.Z71",
        "H2.Z311",  # P1 클러스터 소속이나 전이 성능 열위로 개별 모델 채택
    )
)

THERMAL_SINGLETON_SPECS: tuple[MeterSpec, ...] = tuple(
    MeterSpec(m, "thermal", "singleton", ("P","qv","Tdiff"), src, note)
    for m, src, note in (
        ("V.K21",  "cooling", "cooling thermal singleton"),
        ("H1.K11", "cooling", "cooling thermal singleton"),
        ("H1.K12", "cooling", "cooling thermal singleton"),
        ("H1.K14", "cooling", "cooling thermal singleton"),
        ("H1.K15", "cooling", "cooling thermal singleton"),
        ("H1.K16", "cooling", "cooling thermal singleton"),
        ("H2.K21", "cooling", "cooling thermal singleton"),
        ("H1.W11", "heating", "heating thermal singleton"),
        ("H1.W12", "heating", "heating thermal singleton"),
    )
)

ALL_METER_SPECS: tuple[MeterSpec, ...] = (
    *ELECTRIC_REPRESENTATIVE_SPECS,
    *ELECTRIC_SINGLETON_SPECS,
    *THERMAL_SINGLETON_SPECS,
)
METER_SPECS_BY_URN: dict[str, MeterSpec] = {s.meter_urn: s for s in ALL_METER_SPECS}


def specs_for_group(group: str | None = None) -> list[MeterSpec]:
    specs = list(ALL_METER_SPECS)
    if group is not None:
        specs = [s for s in specs if s.group == group]
    return specs
