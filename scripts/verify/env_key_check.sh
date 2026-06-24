#!/usr/bin/env bash
set -euo pipefail

ROOT="${CMS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
MANIFEST="${1:-$ROOT/env_key_manifest.yaml}"

python3 - "$ROOT" "$MANIFEST" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
if not manifest_path.is_absolute():
    manifest_path = root / manifest_path

if not manifest_path.is_file():
    raise SystemExit(f"env_key_manifest_missing: {manifest_path}")

data = yaml.safe_load(manifest_path.read_text()) or {}
keys = data.get("keys", [])
if not isinstance(keys, list) or not keys:
    raise SystemExit("env_key_manifest_empty")

assign_re = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
cache: dict[str, set[str]] = {}
failures: list[str] = []

for idx, item in enumerate(keys):
    if not isinstance(item, dict):
        failures.append(f"item_{idx}:not_mapping")
        continue
    key = item.get("key")
    source_env_file = item.get("source_env_file")
    verification = item.get("verification")
    if not key:
        failures.append(f"item_{idx}:missing_key")
        continue
    if verification and verification != "scripts/verify/env_key_check.sh":
        failures.append(f"{key}:unexpected_verification:{verification}")
    if not source_env_file:
        failures.append(f"{key}:missing_source_env_file")
        continue
    env_path = root / source_env_file
    if not env_path.is_file():
        failures.append(f"{key}:source_env_file_missing:{source_env_file}")
        continue
    if source_env_file not in cache:
        found: set[str] = set()
        for line in env_path.read_text(errors="ignore").splitlines():
            m = assign_re.match(line)
            if m:
                found.add(m.group(1))
        cache[source_env_file] = found
    if key not in cache[source_env_file]:
        failures.append(f"{key}:not_declared_in:{source_env_file}")

print(f"env_key_count={len(keys)}")
print(f"source_env_files={len(cache)}")
if failures:
    print("env_key_check=FAIL")
    for failure in failures[:80]:
        print(failure)
    raise SystemExit(1)
print("env_key_check=PASS")
PY
