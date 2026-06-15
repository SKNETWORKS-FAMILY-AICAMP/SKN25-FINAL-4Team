#!/usr/bin/env python3
"""Build a deterministic release manifest for the current tree.

The manifest is a classification/audit artifact only. It does not delete,
move, or rewrite project source, generated reports, credentials, or artifacts.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = REPO_ROOT / "docs" / "release" / "release_manifest.json"

EXCLUDED_PREFIXES = (
    ".git/",
    "frontend/node_modules/",
    "frontend/dist/",
    ".pytest_cache/",
    "__pycache__/",
)
BINARY_ARTIFACT_PREFIXES = ("artifacts/anomaly/", "artifacts/pmax/")
SECRET_NAME_MARKERS = (".env", "secret", "password", "credential", "token", "key.pem", "id_rsa")

CLASSIFICATION_RULES: tuple[tuple[str, str], ...] = (
    ("src/", "application_source"),
    ("scripts/", "operator_scripts"),
    ("tests/", "verification_tests"),
    ("docker/", "container_runtime_config"),
    ("docker_compose.yml", "container_runtime_config"),
    ("frontend/", "frontend_source"),
    ("evaluation/", "evaluation_assets"),
    ("knowledge/", "knowledge_graph_assets"),
    ("artifacts/pmax/", "model_artifact_pmax"),
    ("artifacts/anomaly/", "model_artifact_anomaly"),
    ("docs/", "documentation"),
    ("requirements", "python_dependency_lock"),
)


def main() -> int:
    manifest = build_manifest()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(OUTPUT_PATH.relative_to(REPO_ROOT))
    return 0


def build_manifest() -> dict[str, Any]:
    tracked = git_lines("ls-files")
    status = parse_status(git_z("status", "--porcelain=v1", "-z"))
    candidate_paths = sorted(set(tracked) | set(status))
    files = [entry for path in candidate_paths if (entry := classify_file(path)) is not None]
    counts: dict[str, int] = {}
    for entry in files:
        counts[entry["classification"]] = counts.get(entry["classification"], 0) + 1
    return {
        "schema_version": 1,
        "repo_root_name": REPO_ROOT.name,
        "git_head": git_text("rev-parse", "HEAD"),
        "git_branch": git_text("branch", "--show-current"),
        "dirty_entry_count": len(status),
        "policy": {
            "destructive_cleanup_performed": False,
            "credential_contents_included": False,
            "binary_artifacts_hashed_only_by_metadata": True,
            "current_tree_reports_preserved": True,
        },
        "entrypoint_reflection": runtime_reflection(),
        "classification_counts": dict(sorted(counts.items())),
        "files": files,
    }


def classify_file(path: str) -> dict[str, Any] | None:
    normalized = path.replace("\\", "/")
    if any(normalized.startswith(prefix) or f"/{prefix}" in normalized for prefix in EXCLUDED_PREFIXES):
        return None
    classification = "other"
    for prefix, value in CLASSIFICATION_RULES:
        if normalized == prefix or normalized.startswith(prefix):
            classification = value
            break
    entry: dict[str, Any] = {
        "path": normalized,
        "classification": classification,
        "release_packet": include_in_release_packet(normalized),
    }
    disk_path = REPO_ROOT / normalized
    if disk_path.is_file() and entry["release_packet"] and not is_secret_path(normalized):
        entry["sha256"] = sha256_file(disk_path)
        entry["bytes"] = disk_path.stat().st_size
    if is_secret_path(normalized):
        entry["release_packet"] = False
        entry["note"] = "credential-like path excluded from packet and content hashing"
    elif normalized.startswith(BINARY_ARTIFACT_PREFIXES):
        entry["note"] = "model artifact tracked by path/classification; binary payload is not packetized"
    return entry


def include_in_release_packet(path: str) -> bool:
    if is_secret_path(path):
        return False
    if path.startswith(BINARY_ARTIFACT_PREFIXES):
        return path.endswith(("readme.md", "manifest.json", "ensemble_weights.csv", "routing.json", "train_meta.json", "feature_columns.json", "val_thresholds.csv"))
    if path.startswith(("frontend/node_modules/", "frontend/dist/")):
        return False
    return path.startswith(("src/", "scripts/", "tests/", "docker/", "docs/", "frontend/", "evaluation/", "knowledge/")) or path in {
        "docker_compose.yml",
        "requirements.txt",
        "readme.md",
    }


def is_secret_path(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in SECRET_NAME_MARKERS)


def runtime_reflection() -> dict[str, Any]:
    return {
        "frontend": {
            "compose_service": "cms-frontend",
            "profile": "frontend",
            "dockerfile": "frontend/dockerfile",
            "build_command": "npm run build",
        },
        "backend": {
            "compose_services": ["cms", "cms-backend-api", "cms-ingestion-api"],
            "dockerfile": "docker/backend_containerfile",
            "factories": ["cms.service.api:create_app", "cms.service.api:create_backend_app", "cms.service.api:create_ingestion_app"],
        },
        "langgraph_router_eval_knowledge": {
            "modules": [
                "cms.workflow.langgraph_review",
                "cms.workflow.router",
                "cms.knowledge",
                "evaluation.router.router_dataset_preflight",
            ],
            "compose_profile": "backend",
        },
        "pmax": {
            "artifact_root": "artifacts/pmax/import_pmax_v29_60min",
            "loader": "cms.modeling.pmax_artifact_loader:PmaxReleaseArtifactLoader",
            "serving_entrypoint": "scripts/serving/run_model_serving.py --pmax-artifact-root",
        },
        "anomaly": {
            "artifact_root": "artifacts/anomaly",
            "loader": "cms.modeling.anomaly_artifact_loader:AnomalyArtifactInventoryLoader",
            "serving_entrypoint": "scripts/serving/validate_artifacts.py --anomaly-root",
        },
        "airflow": {
            "compose_profile": "airflow",
            "disabled_by_default_modules": [
                "cms.workflow.airflow_skeleton",
                "cms.workflow.champion_airflow_skeleton",
                "cms.workflow.model_serving_airflow_skeleton",
            ],
        },
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_status(payload: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    entries = [item for item in payload.split(b"\0") if item]
    index = 0
    while index < len(entries):
        raw = entries[index].decode("utf-8", errors="replace")
        code = raw[:2]
        path = raw[3:]
        if code.startswith("R") or code.startswith("C"):
            index += 1
            if index < len(entries):
                path = entries[index].decode("utf-8", errors="replace")
        result[path] = code.strip() or "modified"
        index += 1
    return result


def git_text(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO_ROOT, text=True).strip()


def git_lines(*args: str) -> list[str]:
    output = git_text(*args)
    return [line for line in output.splitlines() if line]


def git_z(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
