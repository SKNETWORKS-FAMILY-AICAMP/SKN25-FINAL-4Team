"""
계량기 inference 라우팅 테이블 (energy_v84 기준).

각 meter_urn → {action, model_urn}
- action   : "predict" | "skip"
- model_urn: artifact 디렉터리명
             - 본인 urn: 직접 학습 계량기 (대표·싱글턴·개별멤버, 총 51개)
             - 대표 urn: 전이 멤버 (12개, 대표 artifact 공유)

모든 artifact는 artifacts/{horizon}/{model_urn}/ 에 있음.

총 63개 예측 대상:
  51개 (직접 학습: 대표 15 + 전기싱글 21 + 전기멤버싱글 6 + 열싱글 9)
  12개 (전이: 대표 artifact 사용)
"""
from __future__ import annotations

from pathlib import Path
import os

ARTIFACTS_DIR = Path(os.getenv("MODEL_ARTIFACTS_DIR", str(Path(__file__).resolve().parents[3] / "artifacts"))).resolve()

METER_MAP: dict[str, dict] = {

    # ════════════════════════════════════════════════════════════════════════
    # [A] 직접 학습 계량기 — model_urn = 본인 urn
    # ════════════════════════════════════════════════════════════════════════

    # ── 전기 대표 계량기 ──────────────────────────────────────────────────────
    "H2.Z66":   {"action": "predict", "model_urn": "H2.Z66"},
    "H2.ZE66":  {"action": "predict", "model_urn": "H2.ZE66"},
    "H1.Z12":   {"action": "predict", "model_urn": "H1.Z12"},
    "H4.Z51":   {"action": "predict", "model_urn": "H4.Z51"},
    "H2.T.Z31": {"action": "predict", "model_urn": "H2.T.Z31"},
    "H1.Z13":   {"action": "predict", "model_urn": "H1.Z13"},
    "H1.Z21":   {"action": "predict", "model_urn": "H1.Z21"},
    "H1.Z24":   {"action": "predict", "model_urn": "H1.Z24"},
    "H2.Z64":   {"action": "predict", "model_urn": "H2.Z64"},
    "H3.Z43":   {"action": "predict", "model_urn": "H3.Z43"},
    "H3.Z44":   {"action": "predict", "model_urn": "H3.Z44"},
    "H3.Z48":   {"action": "predict", "model_urn": "H3.Z48"},
    "H4.Z50":   {"action": "predict", "model_urn": "H4.Z50"},
    "V.Z84":    {"action": "predict", "model_urn": "V.Z84"},
    "H1.Z20":   {"action": "predict", "model_urn": "H1.Z20"},

    # ── 전기 단독 계량기 ──────────────────────────────────────────────────────
    "H1.Z10":   {"action": "predict", "model_urn": "H1.Z10"},
    "H1.Z16":   {"action": "predict", "model_urn": "H1.Z16"},
    "H1.Z18":   {"action": "predict", "model_urn": "H1.Z18"},
    "H1.Z19":   {"action": "predict", "model_urn": "H1.Z19"},
    "H1.Z23":   {"action": "predict", "model_urn": "H1.Z23"},
    "H1.Z26":   {"action": "predict", "model_urn": "H1.Z26"},
    "H1.Z27":   {"action": "predict", "model_urn": "H1.Z27"},
    "H2.Z61":   {"action": "predict", "model_urn": "H2.Z61"},
    "H2.Z62":   {"action": "predict", "model_urn": "H2.Z62"},
    "H2.Z63":   {"action": "predict", "model_urn": "H2.Z63"},
    "H2.Z65":   {"action": "predict", "model_urn": "H2.Z65"},
    "H2.Z68":   {"action": "predict", "model_urn": "H2.Z68"},
    "H2.Z69":   {"action": "predict", "model_urn": "H2.Z69"},
    "H2.ZE65":  {"action": "predict", "model_urn": "H2.ZE65"},
    "H2.ZE74":  {"action": "predict", "model_urn": "H2.ZE74"},
    "H3.Z42":   {"action": "predict", "model_urn": "H3.Z42"},
    "H3.Z45":   {"action": "predict", "model_urn": "H3.Z45"},
    "H3.Z46":   {"action": "predict", "model_urn": "H3.Z46"},
    "H3.Z47":   {"action": "predict", "model_urn": "H3.Z47"},
    "H3.Z71":   {"action": "predict", "model_urn": "H3.Z71"},
    "H2.Z311":  {"action": "predict", "model_urn": "H2.Z311"},

    # ── 전이 실패 → 개별 학습 전환 (6개) ─────────────────────────────────────
    "H2.ZE67":  {"action": "predict", "model_urn": "H2.ZE67"},   # C2
    "H2.T.Z32": {"action": "predict", "model_urn": "H2.T.Z32"},  # C5
    "H2.Z70":   {"action": "predict", "model_urn": "H2.Z70"},    # C5
    "H3.ZE44":  {"action": "predict", "model_urn": "H3.ZE44"},   # C11
    "H3.Z49":   {"action": "predict", "model_urn": "H3.Z49"},    # C12
    "V.ZE84":   {"action": "predict", "model_urn": "V.ZE84"},    # P1

    # ── 열 단독 계량기 ────────────────────────────────────────────────────────
    "V.K21":    {"action": "predict", "model_urn": "V.K21"},
    "H1.K11":   {"action": "predict", "model_urn": "H1.K11"},
    "H1.K12":   {"action": "predict", "model_urn": "H1.K12"},
    "H1.K14":   {"action": "predict", "model_urn": "H1.K14"},
    "H1.K15":   {"action": "predict", "model_urn": "H1.K15"},
    "H1.K16":   {"action": "predict", "model_urn": "H1.K16"},
    "H2.K21":   {"action": "predict", "model_urn": "H2.K21"},
    "H1.W11":   {"action": "predict", "model_urn": "H1.W11"},
    "H1.W12":   {"action": "predict", "model_urn": "H1.W12"},

    # ════════════════════════════════════════════════════════════════════════
    # [B] 전이 멤버 — model_urn = 대표 urn (대표 artifact 공유)
    # ════════════════════════════════════════════════════════════════════════
    # C1 (rep=H2.Z66)
    "H2.Z67":   {"action": "predict", "model_urn": "H2.Z66"},
    # C3 (rep=H1.Z12)
    "H1.Z11":   {"action": "predict", "model_urn": "H1.Z12"},
    # C4 (rep=H4.Z51)
    "H4.ZE51":  {"action": "predict", "model_urn": "H4.Z51"},
    # C6 (rep=H1.Z13)
    "H1.Z14":   {"action": "predict", "model_urn": "H1.Z13"},
    # C7 (rep=H1.Z21)
    "H1.Z22":   {"action": "predict", "model_urn": "H1.Z21"},
    # C8 (rep=H1.Z24)
    "H1.Z25":   {"action": "predict", "model_urn": "H1.Z24"},
    # C9 (rep=H2.Z64)
    "H2.ZE64":  {"action": "predict", "model_urn": "H2.Z64"},
    # C10 (rep=H3.Z43)
    "H3.ZE43":  {"action": "predict", "model_urn": "H3.Z43"},
    # C13 (rep=H4.Z50)
    "H4.ZE50":  {"action": "predict", "model_urn": "H4.Z50"},
    # P1 (rep=V.Z84)
    "H1.Z310":  {"action": "predict", "model_urn": "V.Z84"},
    "H3.Z312":  {"action": "predict", "model_urn": "V.Z84"},
    # P2 (rep=H1.Z20)
    "H1.ZE20":  {"action": "predict", "model_urn": "H1.Z20"},
}


def get_routing(meter_urn: str) -> dict | None:
    """매핑 없으면 None (예측 대상 아님)."""
    return METER_MAP.get(meter_urn)


def get_artifacts_dir(meter_urn: str, horizon: int) -> tuple[Path, str] | None:
    """
    (artifacts_dir, model_urn) 반환.
    artifacts_dir / f"{horizon}h" / model_urn 에 artifact 파일들이 있음.
    매핑 없거나 skip이면 None.
    """
    entry = METER_MAP.get(meter_urn)
    if entry is None or entry["action"] == "skip":
        return None
    return ARTIFACTS_DIR, entry["model_urn"]
