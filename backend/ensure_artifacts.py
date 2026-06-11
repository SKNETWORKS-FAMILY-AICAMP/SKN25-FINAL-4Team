"""
컨테이너 시작 시 ML artifacts가 없으면 HF Hub에서 자동 다운로드.
이미 있으면 즉시 종료 (재다운로드 없음).
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # backend/

TARGETS = {
    "pipeline":    (ROOT / "ml" / "pipeline"    / "artifacts", "pipeline"),
    "forecasting": (ROOT / "ml" / "forecasting" / "artifacts", "forecasting"),
}

REPO_ID   = "mintmarket/ems-agent-artifacts"
REPO_TYPE = "dataset"


def _has_artifacts(path: Path) -> bool:
    """모델 파일이 하나라도 있으면 True."""
    return any(path.rglob("*.pt")) or any(path.rglob("*.cbm")) or any(path.rglob("*.joblib"))


def ensure():
    missing = {name: (p, sub) for name, (p, sub) in TARGETS.items() if not _has_artifacts(p)}
    if not missing:
        print("[artifacts] 모두 존재 — 다운로드 스킵")
        return

    print(f"[artifacts] {list(missing)} 없음 — HF Hub에서 다운로드 시작")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("[artifacts] huggingface_hub 없음 — 스킵 (예측/이상탐지 기능 제한)")
        return

    token = os.getenv("HF_TOKEN")

    for name, (local_path, subfolder) in missing.items():
        local_path.mkdir(parents=True, exist_ok=True)
        print(f"[artifacts] {name} 다운로드 중...")
        snapshot_download(
            repo_id=REPO_ID,
            repo_type=REPO_TYPE,
            allow_patterns=[f"{subfolder}/**"],
            local_dir=str(ROOT / "ml"),
            token=token,
        )
        print(f"[artifacts] {name} 완료")

    print("[artifacts] 모든 artifacts 준비 완료")


if __name__ == "__main__":
    ensure()
    sys.exit(0)
