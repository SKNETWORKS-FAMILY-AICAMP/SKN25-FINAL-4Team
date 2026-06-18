"""파이프라인 전체 상수 + MeterSpec 정의. energy_v84: 잔차(P(t)-P(t-1)) 타겟 실험."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

import pandas as pd

# ── 경로 ─────────────────────────────────────────────────────────────────────
PIPELINE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS_DIR = Path(os.getenv("MODEL_ARTIFACTS_DIR", str(PROJECT_ROOT / "artifacts"))).resolve()

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

# 전이 실패로 개별 학습 전환된 6개 멤버 (energy_v84_member 실험 결과 반영)
ELECTRIC_MEMBER_SINGLETON_SPECS: tuple[MeterSpec, ...] = (
    MeterSpec("H2.ZE67",  "electric", "singleton", ("P","U1"),      "C2",  "C2 member: transfer failed, individual model"),
    MeterSpec("H2.T.Z32", "electric", "singleton", ("P","U1","PF"), "C5",  "C5 member: transfer failed, individual model"),
    MeterSpec("H2.Z70",   "electric", "singleton", ("P","U1","PF"), "C5",  "C5 member: transfer failed, individual model"),
    MeterSpec("H3.ZE44",  "electric", "singleton", ("P","U1"),      "C11", "C11 member: transfer failed, individual model"),
    MeterSpec("H3.Z49",   "electric", "singleton", ("P","U1","PF"), "C12", "C12 member: transfer failed, individual model"),
    MeterSpec("V.ZE84",   "electric", "singleton", ("P","U1"),      "P1",  "P1 member: transfer failed, individual model"),
)

# 전이 멤버: artifact 없음, 대표 artifact 사용 — 데이터 조회/물리 체크용 spec만 필요
ELECTRIC_TRANSFER_MEMBER_SPECS: tuple[MeterSpec, ...] = (
    MeterSpec("H2.Z67",   "electric", "transfer_member", ("P","U1","PF"), "C1",  "C1 transfer member → H2.Z66"),
    MeterSpec("H1.Z11",   "electric", "transfer_member", ("P","U1","PF"), "C3",  "C3 transfer member → H1.Z12"),
    MeterSpec("H4.ZE51",  "electric", "transfer_member", ("P","U1","PF"), "C4",  "C4 transfer member → H4.Z51"),
    MeterSpec("H1.Z14",   "electric", "transfer_member", ("P","U1","PF"), "C6",  "C6 transfer member → H1.Z13"),
    MeterSpec("H1.Z22",   "electric", "transfer_member", ("P","U1","PF"), "C7",  "C7 transfer member → H1.Z21"),
    MeterSpec("H1.Z25",   "electric", "transfer_member", ("P","U1","PF"), "C8",  "C8 transfer member → H1.Z24"),
    MeterSpec("H2.ZE64",  "electric", "transfer_member", ("P","U1","PF"), "C9",  "C9 transfer member → H2.Z64"),
    MeterSpec("H3.ZE43",  "electric", "transfer_member", ("P","U1"),      "C10", "C10 transfer member → H3.Z43"),
    MeterSpec("H4.ZE50",  "electric", "transfer_member", ("P","U1","PF"), "C13", "C13 transfer member → H4.Z50"),
    MeterSpec("H1.Z310",  "electric", "transfer_member", ("P","U1"),      "P1",  "P1 transfer member → V.Z84"),
    MeterSpec("H3.Z312",  "electric", "transfer_member", ("P","U1"),      "P1",  "P1 transfer member → V.Z84"),
    MeterSpec("H1.ZE20",  "electric", "transfer_member", ("P","U1","PF"), "P2",  "P2 transfer member → H1.Z20"),
)

# 직접 학습 대상 51개. 전이 멤버는 별도 artifact를 만들지 않고 대표 artifact를 사용한다.
TRAINING_METER_SPECS: tuple[MeterSpec, ...] = (
    *ELECTRIC_REPRESENTATIVE_SPECS,
    *ELECTRIC_SINGLETON_SPECS,
    *ELECTRIC_MEMBER_SINGLETON_SPECS,
    *THERMAL_SINGLETON_SPECS,
)

# 전체 추론 대상 63개. 학습 대상 51개 + 대표 artifact를 공유하는 전이 멤버 12개.
ALL_METER_SPECS: tuple[MeterSpec, ...] = (
    *ELECTRIC_REPRESENTATIVE_SPECS,
    *ELECTRIC_SINGLETON_SPECS,
    *ELECTRIC_MEMBER_SINGLETON_SPECS,
    *ELECTRIC_TRANSFER_MEMBER_SPECS,
    *THERMAL_SINGLETON_SPECS,
)
METER_SPECS_BY_URN: dict[str, MeterSpec] = {s.meter_urn: s for s in ALL_METER_SPECS}

_TRAINING_URNS = {s.meter_urn for s in TRAINING_METER_SPECS}
_TRANSFER_URNS = {s.meter_urn for s in ELECTRIC_TRANSFER_MEMBER_SPECS}
_ALL_URNS = {s.meter_urn for s in ALL_METER_SPECS}
assert _TRAINING_URNS.isdisjoint(_TRANSFER_URNS), "training specs must not contain transfer members"
assert _TRAINING_URNS | _TRANSFER_URNS == _ALL_URNS, "all specs must equal training specs plus transfer members"


def training_specs_for_group(group: str | None = None) -> list[MeterSpec]:
    specs = list(TRAINING_METER_SPECS)
    if group is not None:
        specs = [s for s in specs if s.group == group]
    return specs


def specs_for_group(group: str | None = None) -> list[MeterSpec]:
    specs = list(ALL_METER_SPECS)
    if group is not None:
        specs = [s for s in specs if s.group == group]
    return specs
