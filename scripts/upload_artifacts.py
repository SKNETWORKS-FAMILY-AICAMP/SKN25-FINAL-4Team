"""
ML 아티팩트를 Hugging Face Hub dataset repo에 업로드.

사용법:
    # 1. 로그인 (최초 1회)
    .venv/bin/huggingface-cli login

    # 2. 업로드
    .venv/bin/python scripts/upload_artifacts.py

    # 특정 폴더만
    .venv/bin/python scripts/upload_artifacts.py --only pipeline
    .venv/bin/python scripts/upload_artifacts.py --only forecasting
"""

import argparse
import os
from pathlib import Path
from dotenv import load_dotenv
from huggingface_hub import HfApi, create_repo

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

REPO_ID   = "mintmarket/ems-agent-artifacts"   # HF 계정명/repo명
REPO_TYPE = "dataset"
ROOT      = Path(__file__).resolve().parents[1] / "backend" / "ml"

TARGETS = {
    "pipeline":    ROOT / "pipeline"    / "artifacts",
    "forecasting": ROOT / "forecasting" / "artifacts",
}


def upload(only: str | None = None):
    token = os.getenv("HF_TOKEN")
    create_repo(REPO_ID, repo_type=REPO_TYPE, exist_ok=True, private=True, token=token)
    api = HfApi(token=token)

    for name, local_path in TARGETS.items():
        if only and only != name:
            continue
        if not local_path.exists():
            print(f"[SKIP] {local_path} 없음")
            continue

        print(f"\n[UPLOAD] {name}  ({local_path})  →  {REPO_ID}/{name}/")
        api.upload_folder(
            repo_id=REPO_ID,
            repo_type=REPO_TYPE,
            folder_path=str(local_path),
            path_in_repo=name,
            ignore_patterns=["__pycache__", "*.pyc", "*.log", "*.txt"],
        )
        print(f"[DONE]  {name} 업로드 완료")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=list(TARGETS), default=None,
                        help="특정 폴더만 업로드")
    args = parser.parse_args()
    upload(args.only)
