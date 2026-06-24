#!/usr/bin/env bash
set -euo pipefail

ROOT="${CMS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
MANIFEST="${MODEL_ARTIFACT_MANIFEST:-$ROOT/artifacts/manifests/manifest.yaml}"

python3 - "$ROOT" "$MANIFEST" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
if not manifest_path.is_absolute():
    manifest_path = root / manifest_path
if not manifest_path.is_file():
    raise SystemExit(f"model_serving_health=FAIL missing_manifest={manifest_path}")

manifest = yaml.safe_load(manifest_path.read_text()) or {}
artifacts = manifest.get("artifacts", [])
if not isinstance(artifacts, list) or not artifacts:
    raise SystemExit("model_serving_health=FAIL artifacts_empty")

required_names = {"pmax_artifacts", "anomaly_artifacts"}
seen = {item.get("artifact_name") for item in artifacts if isinstance(item, dict)}
missing_names = sorted(required_names - seen)
if missing_names:
    raise SystemExit(f"model_serving_health=FAIL missing_artifacts={missing_names}")

failures: list[str] = []
for item in artifacts:
    if not isinstance(item, dict):
        failures.append("artifact:not_mapping")
        continue
    name = item.get("artifact_name", "<unknown>")
    checksum_file = item.get("remote_sha256_manifest")
    verify_command = item.get("verification_command")
    fetch_command = item.get("fetch_command")
    for label, value in [("remote_sha256_manifest", checksum_file), ("verification_command", verify_command), ("fetch_command", fetch_command)]:
        if not value:
            failures.append(f"{name}:missing_{label}")
    if checksum_file and not (root / checksum_file).is_file():
        failures.append(f"{name}:checksum_file_missing:{checksum_file}")
    if verify_command:
        script = verify_command.split()[0]
        if not (root / script).is_file():
            failures.append(f"{name}:verify_script_missing:{script}")
    if fetch_command:
        script = fetch_command.split()[0]
        if not (root / script).is_file():
            failures.append(f"{name}:fetch_script_missing:{script}")

policy = manifest.get("git_artifact_policy", {})
if not isinstance(policy, dict) or policy.get("commit_binary_payload") is not False:
    failures.append("git_artifact_policy:commit_binary_payload_not_false")

if failures:
    print("model_serving_health=FAIL")
    for failure in failures[:80]:
        print(failure)
    raise SystemExit(1)
print(f"model_serving_health=PASS artifacts={len(artifacts)} validation_status={manifest.get('validation_status', 'unknown')}")
PY
