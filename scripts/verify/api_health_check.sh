#!/usr/bin/env bash
set -euo pipefail

URL="${CMS_API_HEALTH_URL:-http://127.0.0.1:18000/health}"
TIMEOUT="${CMS_HEALTH_TIMEOUT_SECONDS:-5}"

python3 - "$URL" "$TIMEOUT" <<'PY'
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

url = sys.argv[1]
timeout = float(sys.argv[2])
try:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
        code = resp.status
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", "replace")
    raise SystemExit(f"api_health=FAIL http_status={exc.code} body={body[:300]}")
except Exception as exc:
    raise SystemExit(f"api_health=FAIL error={exc}")

ok = 200 <= code < 300
status_value = None
try:
    payload = json.loads(body)
    status_value = payload.get("status") or payload.get("ok")
except Exception:
    payload = None

if not ok:
    raise SystemExit(f"api_health=FAIL http_status={code}")
if isinstance(status_value, str) and status_value.lower() not in {"ok", "healthy", "pass", "true"}:
    raise SystemExit(f"api_health=FAIL status={status_value}")
if status_value is False:
    raise SystemExit("api_health=FAIL ok=false")
print(f"api_health=PASS url={url} http_status={code}")
PY
