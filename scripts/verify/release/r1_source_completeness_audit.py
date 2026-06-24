#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SNAP_ROOT = ROOT / "incoming/snapshots"
DEFAULT_ARCHIVE_BASE = Path("/home/viowlet/Projects/CMS_active_root_archive")

INCLUDE_SUFFIXES = {
    ".py", ".sh", ".sql", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".txt", ".md", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".dockerfile",
}
INCLUDE_NAMES = {
    "Dockerfile", "Containerfile", "requirements.txt", "package.json", "package-lock.json",
    "vite.config.js", "nginx.conf", ".env.example",
}
EXCLUDE_PARTS = {
    ".git", "node_modules", "dist", "build", "__pycache__", ".pytest_cache", ".venv", "venv",
    "logs", "log", "screenshots", "evidence", "artifacts", "artifacts", "tmp", "cache",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log", ".pid", ".bak", ".png", ".jpg", ".jpeg", ".webp", ".zip", ".gz", ".joblib", ".pkl", ".parquet", ".sqlite", ".db"}

IMPORTANT_PATTERNS = [
    r"(^|/)dags/.*\.py$",
    r"(^|/)stacks/.*\.(yml|yaml)$",
    r"(^|/)stacks/.*",
    r"(^|/)configs/.*",
    r"(^|/)scripts/(verify|artifact|serving|deploy|bootstrap|backfill|ops)/.*",
    r"(^|/)sql/.*\.(sql|py)$",
    r"(^|/)runtime/.*",
    r"(^|/)src/cms/(service|workflow|data|modeling|contracts|runtime|ops)/.*\.py$",
    r"(^|/)src/frontend/(src|public|nginx|Dockerfile|package\.json|package-lock\.json|vite\.config\.js|index\.html).*",
]
IMPORTANT_RE = [re.compile(p) for p in IMPORTANT_PATTERNS]

TARGET_PREFIXES = [
    "dags", "stacks", "configs", "scripts", "sql", "runtime", "src", "frontend", "env",
]

@dataclass
class Candidate:
    source_host: str
    snapshot_rel: str
    normalized_rel: str
    category: str
    reason: str
    final_exists: bool
    manifest_exists: bool
    likely_target: str | None
    status: str


def load_manifest() -> tuple[set[str], dict[str, str]]:
    path = ROOT / "service_manifest.yaml"
    data = yaml.safe_load(path.read_text()) or {}
    targets: set[str] = set()
    source_to_target: dict[str, str] = {}
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        target = item.get("target_path")
        source = item.get("source_path")
        if target:
            targets.add(target)
        if source and target:
            source_to_target[str(source)] = str(target)
    return targets, source_to_target


def load_decisions() -> dict[str, dict[str, str]]:
    path = ROOT / "incoming/audits/release/r1/source_completeness_decisions.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    decisions: dict[str, dict[str, str]] = {}
    for item in data.get("decisions", []):
        if not isinstance(item, dict):
            continue
        source = item.get("source_path")
        if source:
            decisions[str(source)] = {str(k): str(v) for k, v in item.items() if v is not None}
    return decisions


def should_exclude(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_PARTS:
        return True
    name = path.name
    if name.startswith("._"):
        return True
    if any(name.endswith(s) for s in EXCLUDE_SUFFIXES):
        return True
    return False


def is_important(rel: str, path: Path) -> bool:
    if path.name in INCLUDE_NAMES:
        return True
    if path.suffix.lower() in INCLUDE_SUFFIXES:
        return any(rx.search(rel) for rx in IMPORTANT_RE)
    return False


def normalize_snapshot_rel(rel: str) -> str | None:
    # Strip known project roots inside snapshots.
    markers = [
        "cms-stream-deploy/",
        "cms-local/",
        "cms-agent-src/frontend/",
        "cms-langgraph-worker/",
    ]
    for marker in markers:
        if marker in rel:
            tail = rel.split(marker, 1)[1]
            if marker == "cms-agent-src/frontend/":
                return "src/frontend/" + tail
            if marker == "cms-langgraph-worker/":
                if tail == "docker/Dockerfile" or tail == "docker/base/Dockerfile":
                    return "stacks/langgraph/Dockerfile"
                if tail == "docker-compose.yml":
                    return "stacks/langgraph/langgraph.yml"
                return "runtime/langgraph/" + tail
            if tail == "docker/requirements.phase1.txt":
                return "stacks/stream_runtime/requirements.txt"
            if tail == "docker/requirements.model_serving.txt":
                return "stacks/model_serving/requirements.txt"
            if tail.startswith("docker/grafana/provisioning/"):
                return "configs/grafana/provisioning/" + tail.split("docker/grafana/provisioning/", 1)[1]
            compose_map = {
                "docker/compose_edge_stream.yml": "stacks/edge_stream/edge_stream.yml",
                "docker/compose_distributed_consumer.yml": "stacks/distributed_consumer/distributed_consumer.yml",
                "docker/compose_aws_phase1.yml": "stacks/stream_runtime/stream_runtime.yml",
                "docker/compose_aws_phase1_kafka3_override.yml": "stacks/stream_runtime/stream_runtime.yml",
                "docker/compose_local_kafka_broker.yml": "stacks/stream_runtime/kafka_broker.yml",
                "docker/compose.local.kafka-broker.yml": "stacks/stream_runtime/kafka_broker.yml",
                "docker/compose_kafka_cluster.yml": "stacks/stream_runtime/kafka_cluster.yml",
                "docker/compose_model_serving.yml": "stacks/model_serving/model_serving.yml",
            }
            if tail in compose_map:
                return compose_map[tail]
            return tail
    return None


def categorize(norm: str) -> str:
    if norm.startswith("dags/"):
        return "dag"
    if norm.startswith("stacks/"):
        return "stacks"
    if norm.startswith("stacks/") or norm.endswith("Dockerfile"):
        return "stacks"
    if norm.startswith("configs/"):
        return "config"
    if norm.startswith("scripts/"):
        return "script"
    if norm.startswith("sql/"):
        return "sql"
    if norm.startswith("runtime/"):
        return "runtime"
    if norm.startswith("src/frontend/"):
        return "frontend"
    if norm.startswith("src/cms/workflow/"):
        return "workflow"
    if norm.startswith("src/cms/service/"):
        return "service"
    if norm.startswith("src/cms/"):
        return "source"
    if norm.startswith("env/"):
        return "env"
    return "other"


def candidate_status(norm: str, final_exists: bool, manifest_exists: bool) -> str:
    if final_exists and manifest_exists:
        return "imported"
    if final_exists and not manifest_exists:
        return "file_exists_manifest_missing"
    if not final_exists and manifest_exists:
        return "manifest_points_missing_file"
    # Missing in final and manifest: decide whether likely intended exclusion or needs review.
    if any(x in norm for x in ["test", "tests", "legacy", "obsolete", "session", "graphify"]):
        return "likely_intentionally_excluded"
    if norm.startswith("incoming/"):
        return "ignore_snapshot_internal"
    return "needs_review_missing_from_final"


def resolve_snapshot_root() -> Path | None:
    env_value = os.environ.get("CMS_SNAPSHOT_ROOT")
    if env_value:
        p = Path(env_value).expanduser()
        return p if p.is_dir() else None
    if DEFAULT_SNAP_ROOT.is_dir():
        return DEFAULT_SNAP_ROOT
    if DEFAULT_ARCHIVE_BASE.is_dir():
        candidates = sorted(
            (p / "active_root_before_promotion" / "incoming" / "snapshots" for p in DEFAULT_ARCHIVE_BASE.iterdir() if p.is_dir()),
            key=lambda p: str(p),
            reverse=True,
        )
        for p in candidates:
            if p.is_dir():
                return p
    return None


def audit_output_path() -> Path:
    out_dir = Path(os.environ.get("CMS_AUDIT_OUTPUT_DIR", "/tmp/cms_release_audits")).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / "source_completeness_audit.json"


def main() -> int:
    snap_root = resolve_snapshot_root()
    if snap_root is None:
        output = {
            "status": "SKIP_NO_SNAPSHOT_ROOT",
            "candidate_count": 0,
            "needs_review_count": 0,
            "by_status": {},
            "by_category_status": {},
            "message": "No source snapshot root found in active root or CMS_active_root_archive. Clean service root remains valid; rerun with CMS_SNAPSHOT_ROOT when snapshot evidence is required.",
        }
        out_path = audit_output_path()
        out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        print(json.dumps({k: output[k] for k in ["status", "candidate_count", "needs_review_count", "by_status", "by_category_status"]}, indent=2, ensure_ascii=False, sort_keys=True))
        print(f"detail={out_path}")
        return 0
    manifest_targets, source_to_target = load_manifest()
    decisions = load_decisions()
    candidates: list[Candidate] = []
    for host_dir in sorted(p for p in snap_root.iterdir() if p.is_dir()):
        source_host = host_dir.name
        for path in host_dir.rglob("*"):
            if not path.is_file() or should_exclude(path):
                continue
            snap_rel = path.relative_to(snap_root).as_posix()
            source_rel = "/".join(snap_rel.split("/")[1:])
            decision = decisions.get(source_rel)
            manifest_target = source_to_target.get(source_rel)
            norm = (decision.get("target_path") if decision else None) or manifest_target or normalize_snapshot_rel(snap_rel)
            if not norm:
                continue
            if not is_important(norm, path) and not manifest_target and not decision:
                continue
            final_path = ROOT / norm
            final_exists = final_path.is_file()
            manifest_exists = norm in manifest_targets
            if decision:
                status = decision.get("status", "accepted_by_decision")
                if status != "intentionally_excluded":
                    if not final_exists:
                        status = "decision_target_missing"
                    elif not manifest_exists:
                        status = "decision_target_manifest_missing"
                reason = "source_completeness_decision"
            else:
                status = candidate_status(norm, final_exists, manifest_exists)
                reason = "manifest_source_path_match" if manifest_target else "snapshot_operational_candidate"
            candidates.append(Candidate(
                source_host=source_host,
                snapshot_rel=snap_rel,
                normalized_rel=norm,
                category=categorize(norm),
                reason=reason,
                final_exists=final_exists,
                manifest_exists=manifest_exists,
                likely_target=norm,
                status=status,
            ))

    by_status: dict[str, int] = {}
    by_category_status: dict[str, dict[str, int]] = {}
    for c in candidates:
        by_status[c.status] = by_status.get(c.status, 0) + 1
        by_category_status.setdefault(c.category, {})[c.status] = by_category_status.setdefault(c.category, {}).get(c.status, 0) + 1

    needs_review_statuses = {"needs_review_missing_from_final", "file_exists_manifest_missing", "manifest_points_missing_file", "decision_target_missing", "decision_target_manifest_missing"}
    needs_review = [c for c in candidates if c.status in needs_review_statuses]
    accepted_non_imported = [c for c in candidates if c.status != "imported" and c.status not in needs_review_statuses]
    output = {
        "status": "PASS" if not needs_review else "REVIEW_REQUIRED",
        "snapshot_root": str(snap_root),
        "candidate_count": len(candidates),
        "needs_review_count": len(needs_review),
        "accepted_non_imported_count": len(accepted_non_imported),
        "by_status": by_status,
        "by_category_status": by_category_status,
        "needs_review": [asdict(c) for c in needs_review[:500]],
        "accepted_non_imported": [asdict(c) for c in accepted_non_imported[:500]],
    }
    out_path = audit_output_path()
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: output[k] for k in ["status", "candidate_count", "needs_review_count", "by_status", "by_category_status"]}, indent=2, ensure_ascii=False, sort_keys=True))
    print(f"detail={out_path}")
    return 0 if output["status"] == "PASS" else 2

if __name__ == "__main__":
    raise SystemExit(main())
