#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
EXCLUDE_PARTS = {".git", "_archive", "_staging", ".venv", "venv", "node_modules", "dist", "__pycache__"}
FAILURES: list[str] = []
WARNINGS: list[str] = []
DETAILS: dict[str, object] = {}


def fail(msg: str) -> None:
    FAILURES.append(msg)


def warn(msg: str) -> None:
    WARNINGS.append(msg)


def read_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text())


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def check_path_exists(label: str, value: str, *, allow_manual: bool = False) -> None:
    if not value:
        fail(f"{label}:empty")
        return
    if allow_manual and value.startswith("manual:"):
        return
    token = value.split()[0]
    if token.startswith(("http://", "https://", "docker", "python", "bash", "cd")):
        return
    p = ROOT / token
    if not p.exists():
        fail(f"{label}:missing:{token}")


def audit_service_manifest() -> None:
    data = read_yaml(ROOT / "service_manifest.yaml") or {}
    items = data.get("items", []) if isinstance(data, dict) else []
    targets: set[str] = set()
    dupes: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            fail("service_manifest:item_not_mapping")
            continue
        target = item.get("target_path")
        if not target:
            fail("service_manifest:item_missing_target_path")
            continue
        if target in targets:
            dupes.append(target)
        targets.add(target)
        p = ROOT / target
        if not p.is_file():
            fail(f"service_manifest:target_missing:{target}")
        if not item.get("checksum"):
            fail(f"service_manifest:checksum_missing:{target}")
    if dupes:
        fail("service_manifest:duplicate_targets:" + ",".join(sorted(dupes)[:20]))
    DETAILS["service_manifest_items"] = len(items)
    DETAILS["service_manifest_targets"] = len(targets)


def audit_build_matrix() -> None:
    data = read_yaml(ROOT / "build_matrix.yaml") or []
    entries = data if isinstance(data, list) else data.get("services", [])
    for idx, item in enumerate(entries):
        if not isinstance(item, dict):
            fail(f"build_matrix[{idx}]:not_mapping")
            continue
        service = item.get("service", f"idx{idx}")
        for compose_file in item.get("compose_files", []) or []:
            if not (ROOT / compose_file).is_file():
                fail(f"build_matrix:{service}:compose_missing:{compose_file}")
        env_example = item.get("env_example")
        if env_example and not (ROOT / env_example).is_file():
            fail(f"build_matrix:{service}:env_example_missing:{env_example}")
        health = item.get("health_check")
        if health:
            check_path_exists(f"build_matrix:{service}:health_check", health, allow_manual=True)
    DETAILS["build_matrix_entries"] = len(entries)


def audit_env_key_manifest() -> None:
    data = read_yaml(ROOT / "env_key_manifest.yaml") or {}
    keys = data.get("keys", [])
    source_files: set[str] = set()
    for idx, item in enumerate(keys):
        if not isinstance(item, dict):
            fail(f"env_key_manifest[{idx}]:not_mapping")
            continue
        key = item.get("key", f"idx{idx}")
        src = item.get("source_env_file")
        if not src:
            fail(f"env_key_manifest:{key}:source_env_file_missing")
        elif not (ROOT / src).is_file():
            fail(f"env_key_manifest:{key}:source_env_file_not_found:{src}")
        else:
            source_files.add(src)
        verification = item.get("verification")
        if verification:
            check_path_exists(f"env_key_manifest:{key}:verification", verification)
    DETAILS["env_key_count"] = len(keys)
    DETAILS["env_source_files"] = sorted(source_files)


def load_compose(path: Path) -> dict:
    try:
        obj = yaml.safe_load(path.read_text()) or {}
        return obj if isinstance(obj, dict) else {}
    except Exception as exc:
        fail(f"compose:parse_error:{rel(path)}:{exc}")
        return {}


def audit_compose() -> None:
    compose_files = sorted((ROOT / "stacks").rglob("*.yml")) + sorted((ROOT / "stacks").rglob("*.yaml"))
    all_runtime_dags: set[str] = set()
    all_scheduled_dags: set[str] = set()
    for path in compose_files:
        obj = load_compose(path)
        services = obj.get("services", {}) if isinstance(obj, dict) else {}
        for service_name, service in services.items():
            if not isinstance(service, dict):
                continue
            compose_dir = path.parent
            build = service.get("build")
            if isinstance(build, str):
                context_path = (compose_dir / build).resolve()
                if not context_path.exists():
                    fail(f"compose:{rel(path)}:{service_name}:build_context_missing:{build}")
            elif isinstance(build, dict):
                context = build.get("context")
                dockerfile = build.get("dockerfile")
                context_path = (compose_dir / context).resolve() if context else compose_dir.resolve()
                if context and not context_path.exists():
                    fail(f"compose:{rel(path)}:{service_name}:build_context_missing:{context}")
                if dockerfile:
                    dpath = (context_path / dockerfile).resolve()
                    if not dpath.is_file():
                        fail(f"compose:{rel(path)}:{service_name}:dockerfile_missing:{dockerfile}")
            env_file = service.get("env_file")
            env_files = env_file if isinstance(env_file, list) else ([env_file] if env_file else [])
            for env in env_files:
                if isinstance(env, str) and not env.startswith(("/", "${")):
                    env_path = (compose_dir / env).resolve()
                    if not env_path.is_file():
                        fail(f"compose:{rel(path)}:{service_name}:env_file_missing:{env}")
            volumes = service.get("volumes", []) or []
            for vol in volumes:
                if not isinstance(vol, str):
                    continue
                left = vol.split(":", 1)[0]
                if not left or left.startswith(("/", "${")):
                    continue
                if re.match(r"^[A-Za-z0-9_.-]+$", left) and not left.startswith("."):
                    continue
                vol_path = (compose_dir / left).resolve()
                try:
                    within_root = vol_path == ROOT.resolve() or ROOT.resolve() in vol_path.parents
                except Exception:
                    within_root = False
                if within_root and not vol_path.exists():
                    fail(f"compose:{rel(path)}:{service_name}:volume_source_missing:{left}")
            labels = service.get("labels", {}) or {}
            if isinstance(labels, list):
                label_map = {}
                for label in labels:
                    if isinstance(label, str) and "=" in label:
                        k, v = label.split("=", 1)
                        label_map[k] = v
                labels = label_map
            if isinstance(labels, dict):
                for key in ("cms.runtime.dags", "cms.runtime.scheduled_dags", "cms.runtime.manual_dags"):
                    value = labels.get(key)
                    if not value:
                        continue
                    dags = [x.strip() for x in str(value).split(",") if x.strip()]
                    all_runtime_dags.update(dags)
                    if key == "cms.runtime.scheduled_dags":
                        all_scheduled_dags.update(dags)
    dag_files = {p.stem for p in (ROOT / "dags").glob("*.py")}
    missing = sorted(all_runtime_dags - dag_files)
    if missing:
        fail("compose:runtime_dag_files_missing:" + ",".join(missing))
    DETAILS["compose_files"] = [rel(p) for p in compose_files]
    DETAILS["runtime_dags_declared"] = sorted(all_runtime_dags)
    DETAILS["scheduled_dags_declared"] = sorted(all_scheduled_dags)
    DETAILS["dag_files"] = sorted(dag_files)


def audit_dockerfiles() -> None:
    dockerfiles = [
        p for p in ROOT.glob("**/Dockerfile") if not (set(p.relative_to(ROOT).parts) & EXCLUDE_PARTS)
    ]
    copy_re = re.compile(r"^\s*(?:COPY|ADD)\s+(?:--[^\s]+\s+)*(.+?)\s+\S+\s*$", re.IGNORECASE)
    for path in dockerfiles:
        for lineno, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = copy_re.match(line)
            if not m:
                continue
            srcs = m.group(1).split()
            for src in srcs:
                src = src.strip('"\'')
                if src.startswith(("--", "http://", "https://", "$", "/")):
                    continue
                if any(ch in src for ch in "*?["):
                    continue
                # Dockerfiles in docker/ use repository root as build context unless compose says otherwise;
                # here we only flag obviously missing root-relative paths.
                candidate = ROOT / src
                if not candidate.exists():
                    warn(f"dockerfile:{rel(path)}:{lineno}:copy_source_not_root_present:{src}")
    DETAILS["dockerfiles_checked"] = [rel(p) for p in dockerfiles]


def audit_workflow_dag_wrappers() -> None:
    workflow_dags: dict[str, str] = {}
    for path in (ROOT / "src/cms/workflow").glob("*_airflow.py"):
        text = path.read_text(errors="ignore")
        m = re.search(r'DAG_ID\s*=\s*["\']([^"\']+)["\']', text)
        if m:
            workflow_dags[m.group(1)] = rel(path)
    dag_files = {p.stem for p in (ROOT / "dags").glob("*.py")}
    missing = sorted(set(workflow_dags) - dag_files)
    # Not every workflow airflow module must be a top-level DAG; report as warning unless compose declares it.
    for dag_id in missing:
        warn(f"workflow_dag_without_wrapper:{dag_id}:{workflow_dags[dag_id]}")
    DETAILS["workflow_dag_ids"] = workflow_dags


def audit_git_ignore() -> None:
    if not (ROOT / ".git").exists():
        warn("git:repository_not_initialized")
        return
    probes = [
        "artifacts/external/pmax/import_pmax_production_release_20260608/import_pmax_v29_60min/input_24h/predict_60min/h2_z36x/_candidate_models/v23.joblib",
        "artifacts/external/anomaly/test6_residual_v84_3h_share_20260609",
        "_archive/2026-06-22_folder_restructure/moved_from_active/incoming/orchestrator/gate8_grafana_final_query_summary.log",
        "_archive/2026-06-22_folder_restructure/moved_from_active/incoming/orchestrator/gate8_pc3_exporter_tunnel.pid",
    ]
    ignored: list[str] = []
    not_ignored: list[str] = []
    for probe in probes:
        proc = subprocess.run(["git", "check-ignore", probe], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode == 0:
            ignored.append(probe)
        else:
            not_ignored.append(probe)
    if not_ignored:
        fail("git:expected_ignore_missing:" + ",".join(not_ignored))
    DETAILS["git_ignored_probes"] = ignored


def audit_junk() -> None:
    patterns = ["._*", "*.bak_*", "*.pyc"]
    hits: list[str] = []
    for pattern in patterns:
        hits.extend(
            rel(p) for p in ROOT.rglob(pattern)
            if not (set(p.relative_to(ROOT).parts) & EXCLUDE_PARTS)
        )
    # ignored logs are allowed; junk files in service dirs are not.
    if hits:
        fail("junk_files_present:" + ",".join(sorted(hits)[:40]))
    DETAILS["junk_hits"] = sorted(hits)


def main() -> int:
    audit_service_manifest()
    audit_build_matrix()
    audit_env_key_manifest()
    audit_compose()
    audit_dockerfiles()
    audit_workflow_dag_wrappers()
    audit_git_ignore()
    audit_junk()
    result = {
        "status": "PASS" if not FAILURES else "FAIL",
        "failure_count": len(FAILURES),
        "warning_count": len(WARNINGS),
        "failures": FAILURES,
        "warnings": WARNINGS,
        "details": DETAILS,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
