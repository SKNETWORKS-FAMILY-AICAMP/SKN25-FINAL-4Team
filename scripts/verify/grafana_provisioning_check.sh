#!/usr/bin/env bash
set -euo pipefail

ROOT="${CMS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
URL="${GRAFANA_HEALTH_URL:-${GRAFANA_URL:-http://127.0.0.1:13000}/api/health}"
TIMEOUT="${CMS_HEALTH_TIMEOUT_SECONDS:-5}"

python3 - "$ROOT" "$URL" "$TIMEOUT" <<'PY'
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

root = Path(sys.argv[1])
url = sys.argv[2]
timeout = float(sys.argv[3])

required_paths = [
    root / "configs/grafana/provisioning/datasources/postgres_cms_live.yaml",
    root / "configs/grafana/provisioning/datasources/prometheus_cms_stream.yaml",
    root / "configs/grafana/provisioning/datasources/prometheus_edge_cluster.yaml",
]
missing = [str(p.relative_to(root)) for p in required_paths if not p.is_file()]
if missing:
    raise SystemExit(f"grafana_provisioning=FAIL missing={missing}")

try:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
        code = resp.status
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", "replace")
    raise SystemExit(f"grafana_health=FAIL http_status={exc.code} body={body[:300]}")
except Exception as exc:
    raise SystemExit(f"grafana_health=FAIL error={exc}")

try:
    payload = json.loads(body)
except Exception:
    payload = {}

database = str(payload.get("database", "")).lower()
if not (200 <= code < 300):
    raise SystemExit(f"grafana_health=FAIL http_status={code}")
if database and database != "ok":
    raise SystemExit(f"grafana_health=FAIL database={database}")
print(f"grafana_provisioning=PASS files={len(required_paths)}")
print(f"grafana_health=PASS url={url} http_status={code} database={database or 'unknown'}")
PY
