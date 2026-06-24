#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = Path(os.environ.get("CMS_ACTIVE_TREE_AUDIT_DIR", "/tmp/cms_release_audits/active_tree")).expanduser()
EXCLUDE_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "_archive"}
LOCAL_ONLY_NAMES = {".env"}
SERVICE_TOP = {"src", "stacks", "deploy", "env", "configs", "scripts", "dags", "sql", "runtime", "frontend", "docs", "tests"}
MANIFEST_REQUIRED_TOP = {"src", "stacks", "deploy", "env", "configs", "scripts", "dags", "sql", "runtime", "frontend", "artifacts"}
ROOT_ALLOWED = {".dockerignore", ".gitignore", "build_matrix.yaml", "env_key_manifest.yaml", "import_decisions.yaml", "requirements.txt", "service_manifest.yaml"}
MODEL_KEEP = {"artifacts/manifests/manifest.yaml", "artifacts/manifests/pmax_remote.sha256", "artifacts/manifests/anomaly_remote.sha256"}
TEXT_EXTS = {".py", ".sh", ".sql", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf", ".txt", ".md", ".js", ".jsx", ".ts", ".tsx", ".css", ".html"}
CONVENTIONAL_NAMES = {"README.md", "Dockerfile", "Containerfile", "package-lock.json", "package.json", "docker-compose.yml"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_status_map() -> dict[str, str]:
    proc = subprocess.run(["git", "status", "--porcelain=v1", "--ignored"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    status: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if not line:
            continue
        code = line[:2]
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        status[path] = code
    return status


def is_snake_path(rel: str) -> bool:
    allowed_hidden = {".gitignore", ".dockerignore"}
    for part in rel.split("/"):
        if part in allowed_hidden or part in CONVENTIONAL_NAMES:
            continue
        stem = part
        # keep extension separator, but names should be lower snake/dot for service files.
        if not re.fullmatch(r"[a-z0-9_.-]+", stem):
            return False
        # hyphen is allowed only for ecosystem lockfile handled above; flag service paths otherwise.
        if "-" in stem:
            return False
        if re.search(r"[A-Z]", stem):
            return False
    return True


def classify(rel: str, path: Path, git_code: str) -> tuple[str, str]:
    parts = rel.split("/")
    name = path.name
    suffix = path.suffix.lower()
    if name in LOCAL_ONLY_NAMES or name.endswith(".env") and not name.endswith(".env.example"):
        return "local_only", "runtime env or secret-bearing local file"
    if git_code == "!!":
        if rel.startswith("artifacts/external/"):
            return "externalized", "large model payload ignored; manifest/checksum only"
        if suffix in {".log", ".pid"} or "__pycache__" in parts:
            return "archive", "ignored runtime/evidence/cache output"
        return "local_only", "ignored local/generated path"
    if rel in MODEL_KEEP:
        return "keep", "model artifact manifest/checksum contract"
    if rel.startswith("artifacts/external/"):
        return "externalized", "model payload or artifact detail outside git push boundary"
    if parts[0] in SERVICE_TOP or rel in ROOT_ALLOWED:
        if rel.startswith("incoming/"):
            return "evidence", "server snapshot/audit evidence outside service push boundary"
        return "keep", "service source/config/script/doc candidate"
    if rel.startswith("incoming/snapshots/"):
        return "evidence", "server-derived source snapshot"
    if rel.startswith("incoming/audits/"):
        return "evidence", "audit output and inventory evidence"
    if rel.startswith("incoming/evidence/"):
        return "evidence", "structured gate/worker evidence outside service push boundary"
    if rel.startswith("incoming/"):
        return "review", "incoming evidence/worker handoff needs folder-level placement"
    return "review", "top-level path outside current service allowlist"


def load_manifest_targets() -> set[str]:
    path = ROOT / "service_manifest.yaml"
    if not path.exists():
        return set()
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    return {str(item.get("target_path")) for item in data.get("items", []) if isinstance(item, dict) and item.get("target_path")}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    status = git_status_map()
    manifest_targets = load_manifest_targets()
    rows: list[dict[str, object]] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(ROOT)
        rel = rel_path.as_posix()
        if any(part in EXCLUDE_DIRS for part in rel_path.parts):
            continue
        git_code = status.get(rel, "")
        decision, reason = classify(rel, path, git_code)
        rows.append({
            "path": rel,
            "top": rel.split("/", 1)[0],
            "suffix": path.suffix.lower(),
            "size": path.stat().st_size,
            "sha256": sha256(path),
            "git_status": git_code,
            "decision": decision,
            "reason": reason,
            "manifest_target": rel in manifest_targets,
            "snake_path_ok": is_snake_path(rel),
        })

    csv_path = OUT_DIR / "active_tree_manifest.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["path"])
        writer.writeheader()
        writer.writerows(rows)

    by_decision = Counter(r["decision"] for r in rows)
    by_top_decision: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by_top_decision[str(r["top"])][str(r["decision"])] += 1
    non_snake = [r["path"] for r in rows if not r["snake_path_ok"] and r["decision"] in {"keep", "review"}]
    review = [r for r in rows if r["decision"] == "review"]
    unmanifested_keep = [
        r["path"] for r in rows
        if r["decision"] == "keep"
        and str(r["top"]) in MANIFEST_REQUIRED_TOP
        and not r["manifest_target"]
    ]
    summary = {
        "status": "PASS" if not review and not unmanifested_keep else "REVIEW_REQUIRED",
        "file_count": len(rows),
        "by_decision": dict(sorted(by_decision.items())),
        "by_top_decision": {k: dict(sorted(v.items())) for k, v in sorted(by_top_decision.items())},
        "review_count": len(review),
        "review_sample": [r["path"] for r in review[:100]],
        "unmanifested_keep_count": len(unmanifested_keep),
        "unmanifested_keep_sample": unmanifested_keep[:100],
        "non_snake_keep_or_review_count": len(non_snake),
        "non_snake_keep_or_review_sample": non_snake[:100],
        "manifest": csv_path.as_posix(),
    }
    json_path = OUT_DIR / "active_tree_audit.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
