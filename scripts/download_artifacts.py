"""
Hugging Face Hub에서 ML 아티팩트 다운로드.

사용법:
    .venv/bin/python scripts/download_artifacts.py

    # 특정 폴더만
    .venv/bin/python scripts/download_artifacts.py --only pipeline
    .venv/bin/python scripts/download_artifacts.py --only forecasting

환경변수 (비공개 repo인 경우):
    HF_TOKEN=hf_xxx .venv/bin/python scripts/download_artifacts.py
"""

import argparse
import os
from pathlib import Path
from dotenv import load_dotenv
from huggingface_hub import snapshot_download

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

REPO_ID   = "mintmarket/ems-agent-artifacts"
REPO_TYPE = "dataset"
ROOT      = Path(__file__).resolve().parents[1] / "backend" / "ml"

TARGETS = {
    "pipeline":    (ROOT / "pipeline"    / "artifacts", "pipeline"),
    "forecasting": (ROOT / "forecasting" / "artifacts", "forecasting"),
}


def download(only: str | None = None):
    token = os.getenv("HF_TOKEN")

    for name, (local_path, repo_subfolder) in TARGETS.items():
        if only and only != name:
            continue

        print(f"\n[DOWNLOAD] {REPO_ID}/{repo_subfolder}  →  {local_path}")
        local_path.mkdir(parents=True, exist_ok=True)

        snapshot_download(
            repo_id=REPO_ID,
            repo_type=REPO_TYPE,
            allow_patterns=[f"{repo_subfolder}/**"],
            local_dir=str(ROOT),
            token=token,
        )
        print(f"[DONE]  {name} 다운로드 완료")

    print("\n모든 아티팩트 준비 완료.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=list(TARGETS), default=None,
                        help="특정 폴더만 다운로드")
    args = parser.parse_args()
    download(args.only)
