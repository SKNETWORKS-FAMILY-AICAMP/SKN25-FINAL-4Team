#!/usr/bin/env bash
set -euo pipefail

# Rebuild/redeploy only PC1 phase-1 app runtime containers (ingestion/backend/consumer).
# Non-destructive: preserves remote env files, Kafka volumes/brokers, and DB credentials.
# Requires local .env with SKN25_SSH_* and SKN25_PC1_* values. Does not print secrets.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/skn25/cms-stream-deploy}"
REMOTE_COMPOSE="${REMOTE_COMPOSE:-docker/compose.local.stream.yml}"
REMOTE_ENV="${REMOTE_ENV:-docker/local_stream.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 2
fi

# shellcheck disable=SC1090
set -a
. "$ENV_FILE"
set +a

: "${SKN25_SSH_USER:?missing SKN25_SSH_USER}"
: "${SKN25_SSH_PASSWORD:?missing SKN25_SSH_PASSWORD}"
: "${SKN25_PC1_SSH_HOST:?missing SKN25_PC1_SSH_HOST}"
PC1_PORT="${SKN25_PC1_SSH_PORT:-22}"
SSH_BASE=(ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$PC1_PORT" "$SKN25_SSH_USER@$SKN25_PC1_SSH_HOST")
RSYNC_SSH="sshpass -e ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p $PC1_PORT"
export SSHPASS="$SKN25_SSH_PASSWORD"

required=(
  docker/Dockerfile.phase1
  docker/requirements.phase1.txt
  scripts/live/run_consumer_service.py
  scripts/live/run_live_stream_injector.py
  src/cms/service/api.py
)
for rel in "${required[@]}"; do
  [[ -f "$REPO_ROOT/$rel" ]] || { echo "missing required build file: $rel" >&2; exit 3; }
done

cd "$REPO_ROOT"
LOCAL_SHA="$(python3 - <<'PY'
from pathlib import Path
import hashlib
roots=['src','scripts/live','docker/Dockerfile.phase1','docker/requirements.phase1.txt']
paths=[]
for root in roots:
    p=Path(root)
    if p.is_file():
        paths.append(p)
    elif p.is_dir():
        paths.extend(x for x in p.rglob('*') if x.is_file() and '__pycache__' not in x.parts)
h=hashlib.sha256()
for p in sorted(paths, key=lambda x: str(x)):
    h.update(str(p).encode()+b'\0')
    h.update(p.read_bytes()+b'\0')
print(h.hexdigest())
PY
)"
LOCAL_RUNTIME_PATHS_JSON="$(python3 - <<'PY'
from pathlib import Path
import json
roots=['src','scripts/live','docker/Dockerfile.phase1','docker/requirements.phase1.txt']
paths=[]
for root in roots:
    p=Path(root)
    if p.is_file():
        paths.append(p)
    elif p.is_dir():
        paths.extend(x for x in p.rglob('*') if x.is_file() and '__pycache__' not in x.parts)
print(json.dumps([str(p) for p in sorted(paths, key=lambda x: str(x))]))
PY
)"
echo "local_runtime_source_sha256=$LOCAL_SHA"

echo "remote preflight"
sshpass -e "${SSH_BASE[@]}" "set -e; cd '$REMOTE_ROOT'; test -f '$REMOTE_COMPOSE'; test -f '$REMOTE_ENV'; docker compose version; docker ps --format 'container={{.Names}} image={{.Image}} status={{.Status}}' | grep -E 'cms-(ingestion-api|backend-api|kafka-to-postgres-consumer)' || true"

echo "sync runtime source (no env/secrets, no delete)"
rsync -azR --no-owner --no-group \
  --exclude '.env' \
  --exclude 'docker/*.env' \
  --exclude 'docker/*.env.*' \
  --exclude '.venv/' \
  --exclude 'frontend/node_modules/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '**/__pycache__/' \
  -e "$RSYNC_SSH" \
  docker/Dockerfile.phase1 docker/requirements.phase1.txt docker/backend_containerfile docker/compose.edge_stream.yml pyproject.toml requirements.txt \
  src scripts evaluation knowledge \
  "$SKN25_SSH_USER@$SKN25_PC1_SSH_HOST:$REMOTE_ROOT/"

echo "remote build/redeploy app containers only"
sshpass -e "${SSH_BASE[@]}" "set -euo pipefail
cd '$REMOTE_ROOT'
TS=\$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p logs/deploy_evidence
{
  echo deploy_ts=\$TS
  echo local_runtime_source_sha256=$LOCAL_SHA
  docker inspect cms-ingestion-api cms-backend-api cms-kafka-to-postgres-consumer --format '{{.Name}} image={{.Image}} config={{.Config.Image}} created={{.Created}}' || true
} > logs/deploy_evidence/himmel_pre_\$TS.txt
cat >/tmp/himmel.consumer.runtime.override.yml <<'YAML'
services:
  cms-kafka-to-postgres-consumer:
    environment:
      CMS_ENABLE_RUNTIME_CONSUMER: "1"
      ALLOW_CANONICAL_WRITE: "0"
      ALLOW_PRODUCTION_DDL: "0"
    command: ['python', 'scripts/live/run_consumer_service.py', '--runtime', '--max-idle-polls', '2147483647']
YAML
EXPECTED_PATHS_JSON='$LOCAL_RUNTIME_PATHS_JSON' python3 - <<'PY' > logs/deploy_evidence/himmel_source_sha256_\$TS.txt
from pathlib import Path
import hashlib, json, os
paths=[Path(p) for p in json.loads(os.environ['EXPECTED_PATHS_JSON'])]
missing=[str(p) for p in paths if not p.is_file()]
if missing:
    raise SystemExit('missing synced runtime files: ' + repr(missing[:10]))
h=hashlib.sha256()
for p in paths:
    h.update(str(p).encode()+b'\\0')
    h.update(p.read_bytes()+b'\\0')
print(h.hexdigest())
PY
REMOTE_SHA=\$(cat logs/deploy_evidence/himmel_source_sha256_\$TS.txt)
test \"\$REMOTE_SHA\" = '$LOCAL_SHA'
docker compose -f '$REMOTE_COMPOSE' --env-file '$REMOTE_ENV' build cms-ingestion-api
docker compose -f '$REMOTE_COMPOSE' --env-file '$REMOTE_ENV' -f /tmp/himmel.consumer.runtime.override.yml --profile backend --profile db-writer up -d --no-deps cms-ingestion-api cms-backend-api cms-kafka-to-postgres-consumer
sleep 5
for url in http://127.0.0.1:8000/health http://127.0.0.1:8001/health; do echo URL=\$url; curl -fsS --max-time 5 \$url; echo; done
for c in cms-ingestion-api cms-backend-api cms-kafka-to-postgres-consumer; do docker inspect \$c --format '{{.Name}} image={{.Image}} config={{.Config.Image}} status={{.State.Status}} started={{.State.StartedAt}} cmd={{json .Config.Cmd}}'; done | tee logs/deploy_evidence/himmel_post_\$TS.txt
for c in cms-ingestion-api cms-backend-api cms-kafka-to-postgres-consumer; do
  docker exec \$c python - <<'PY'
import importlib, json
mods=['cms.service.api','cms.data.runtime_kafka','cms.data.runtime_postgres','cms.data.runtime_consumer_loop','scripts.live.run_consumer_service','scripts.live.run_live_stream_injector']
print(json.dumps({m:'ok' for m in mods if importlib.import_module(m)}))
PY
done | tee logs/deploy_evidence/himmel_imports_\$TS.txt
python3 - <<'PY' | tee logs/deploy_evidence/himmel_openapi_\$TS.txt
import json, urllib.request
out={}
for name, url in {'ingestion':'http://127.0.0.1:8000/openapi.json','backend':'http://127.0.0.1:8001/openapi.json'}.items():
    data=json.load(urllib.request.urlopen(url, timeout=5))
    out[name]={'title':data.get('info',{}).get('title'), 'path_count':len(data.get('paths',{})), 'paths':sorted(data.get('paths',{}))}
print(json.dumps(out, sort_keys=True))
PY
echo evidence_dir=$REMOTE_ROOT/logs/deploy_evidence
"
