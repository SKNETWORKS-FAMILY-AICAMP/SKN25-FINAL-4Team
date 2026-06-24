#!/usr/bin/env bash
# Start/recreate the local LangGraph API smoke container with PostgreSQL env injected
# from the project root .env. If DB_HOST is only reachable from the SKN PC network,
# this script opens a local SSH tunnel via PC1 and points the container at it.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
WORKSPACE_DIR="$ROOT_DIR/workspaces/won"
ENV_FILE="$ROOT_DIR/.env"
CONTAINER_NAME="${CONTAINER_NAME:-langgraph-worker-main}"
HOST_TUNNEL_BIND="${HOST_TUNNEL_BIND:-172.17.0.1}"
HOST_TUNNEL_PORT="${HOST_TUNNEL_PORT:-15432}"
API_HOST_PORT="${API_HOST_PORT:-18000}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

# Load .env without printing secrets. The project .env is KEY=VALUE style.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${DB_HOST:?DB_HOST is required in .env}"
: "${DB_PORT:=5432}"
: "${DB_NAME:=cms}"
: "${DB_USER:=cms}"
: "${DB_PASSWORD:?DB_PASSWORD is required in .env}"
: "${SKN25_SSH_USER:=skn25}"
: "${SKN25_PC1_SSH_HOST:=${SKN25_PC1_PUBLIC_SSH_HOST:-}}"
: "${SKN25_PC1_SSH_PORT:=${SKN25_PC1_PUBLIC_SSH_PORT:-22}}"

if ! command -v sshpass >/dev/null 2>&1; then
  echo "sshpass is required for password-based SSH tunnel bootstrap" >&2
  exit 1
fi

if ! ss -ltn | grep -q "${HOST_TUNNEL_BIND}:${HOST_TUNNEL_PORT}"; then
  : "${SKN25_SSH_PASSWORD:?SKN25_SSH_PASSWORD is required to create the SSH tunnel}"
  export SSHPASS="$SKN25_SSH_PASSWORD"
  sshpass -e ssh \
    -o StrictHostKeyChecking=accept-new \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    -o PreferredAuthentications=password \
    -o PubkeyAuthentication=no \
    -N -L "${HOST_TUNNEL_BIND}:${HOST_TUNNEL_PORT}:${DB_HOST}:${DB_PORT}" \
    -p "$SKN25_PC1_SSH_PORT" \
    "$SKN25_SSH_USER@$SKN25_PC1_SSH_HOST" &
  tunnel_pid=$!
  sleep 2
  if ! kill -0 "$tunnel_pid" 2>/dev/null; then
    echo "failed to start PostgreSQL SSH tunnel" >&2
    exit 1
  fi
fi

sudo -n docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
sudo -n docker run -d \
  --name "$CONTAINER_NAME" \
  --network host \
  --env-file "$ENV_FILE" \
  -e PYTHONPATH=/workspace/src \
  -e DB_HOST="$HOST_TUNNEL_BIND" \
  -e DB_PORT="$HOST_TUNNEL_PORT" \
  -e POSTGRES_HOST="$HOST_TUNNEL_BIND" \
  -e POSTGRES_PORT="$HOST_TUNNEL_PORT" \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASSWORD" \
  -e POSTGRES_SSLMODE="${POSTGRES_SSLMODE:-disable}" \
  -e LANGGRAPH_JOB_STORE=postgres \
  -e REVIEW_JOB_STORE=postgres \
  -e LANGGRAPH_WORKER_ID="${LANGGRAPH_WORKER_ID:-local-langgraph-worker-main}" \
  -e LANGGRAPH_DRY_RUN=true \
  -v "$WORKSPACE_DIR:/workspace" \
  -w /workspace \
  --health-cmd "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:${API_HOST_PORT}/health', timeout=3).read()\"" \
  --health-interval 10s \
  --health-timeout 5s \
  --health-retries 12 \
  --health-start-period 45s \
  python:3.12-slim \
  sh -lc "python -m pip install --no-cache-dir fastapi 'uvicorn[standard]' pydantic langgraph 'psycopg[binary]' >/tmp/pip-install.log 2>&1 && exec python -m uvicorn cms.service.api:create_app --factory --host 127.0.0.1 --port ${API_HOST_PORT}"

echo "$CONTAINER_NAME started: http://127.0.0.1:${API_HOST_PORT}/health"
