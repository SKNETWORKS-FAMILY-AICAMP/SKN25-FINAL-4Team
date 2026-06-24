#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

if [[ -f /root/.skn_eval.env ]]; then
  set -a
  source /root/.skn_eval.env
  set +a
fi

export LLM_PROVIDER="${LLM_PROVIDER:-ollama}"
export LLM_MODEL="${LLM_MODEL:-qwen3.5:9b}"
export LLM_MODEL_FAST="${LLM_MODEL_FAST:-qwen3.5:9b}"
export OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434/v1}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export OLLAMA_NUM_CTX="${OLLAMA_NUM_CTX:-8192}"
export PYTHONPATH="${PWD}/backend/src:${PWD}/backend/src/agents:${PYTHONPATH:-}"
export E2E_BASE_URL="${E2E_BASE_URL:-http://127.0.0.1:18080}"
export E2E_HOST="${E2E_HOST:-127.0.0.1}"
export E2E_PORT="${E2E_PORT:-18080}"
export E2E_DATASET="${E2E_DATASET:-dev/eval/data/router_two_stage_eval_300_with_qa60_260622.json}"
export E2E_LIMIT="${E2E_LIMIT:-300}"
export E2E_WORKERS="${E2E_WORKERS:-1}"
export E2E_STREAM_SAMPLE="${E2E_STREAM_SAMPLE:-20}"

RUN_ID="${RUN_ID:-service_e2e_flow_$(date +%Y%m%d_%H%M%S)_qwen35_9b}"
RUN_ROOT="reports/experiments/service_e2e_flow_latency/${RUN_ID}"
mkdir -p "$RUN_ROOT" /workspace/logs
LOG="$RUN_ROOT/run.log"
exec > >(tee -a "$LOG") 2>&1

echo "[START] $(date -Is)"
echo "[ENV] RUN_ID=$RUN_ID base=$E2E_BASE_URL model=$LLM_MODEL fast=$LLM_MODEL_FAST provider=$LLM_PROVIDER dataset=$E2E_DATASET limit=$E2E_LIMIT workers=$E2E_WORKERS"

echo "[CHECK] Ollama API"
curl -fsS "$OLLAMA_BASE_URL/api/tags" | python3 -m json.tool | head -80

echo "[CHECK] Python import preflight"
python3 - <<'PY'
mods = ['fastapi','uvicorn','psycopg2','langgraph','langchain_core','dotenv','httpx']
missing=[]
for m in mods:
    try: __import__(m)
    except Exception as e: missing.append((m, type(e).__name__, str(e)[:120]))
if missing:
    print('missing_imports=', missing)
    raise SystemExit(2)
print('imports_ok')
PY

echo "[STEP 1] start FastAPI backend if needed"
if curl -fsS --max-time 2 "$E2E_BASE_URL/health" >/dev/null 2>&1; then
  echo "FastAPI already healthy at $E2E_BASE_URL"
else
  pkill -f "uvicorn .*api.main:app.*${E2E_PORT}" >/dev/null 2>&1 || true
  ( cd backend && nohup python3 -m uvicorn src.api.main:app --host "$E2E_HOST" --port "$E2E_PORT" --workers 1 > /workspace/logs/${RUN_ID}_uvicorn.log 2>&1 & echo $! > /workspace/logs/${RUN_ID}_uvicorn.pid )
  ok=0
  for i in $(seq 1 90); do
    if curl -fsS --max-time 3 "$E2E_BASE_URL/health" >/dev/null 2>&1; then ok=1; break; fi
    sleep 2
  done
  if [[ "$ok" != 1 ]]; then
    echo "[ERROR] FastAPI failed to become healthy"
    tail -200 /workspace/logs/${RUN_ID}_uvicorn.log || true
    exit 1
  fi
fi
curl -fsS "$E2E_BASE_URL/health"
echo

echo "[STEP 2] service E2E /chat + /chat/stream benchmark"
python3 dev/eval/e2e_service_flow_benchmark_260622.py \
  --base-url "$E2E_BASE_URL" \
  --dataset "$E2E_DATASET" \
  --limit "$E2E_LIMIT" \
  --workers "$E2E_WORKERS" \
  --stream-sample "$E2E_STREAM_SAMPLE" \
  --timeout "${E2E_TIMEOUT:-300}" \
  --out "$RUN_ROOT/e2e_service_flow_latency_260622_qwen35_9b.json"

echo "[STEP 3] copy server log snapshot"
if [[ -f /workspace/logs/${RUN_ID}_uvicorn.log ]]; then
  cp /workspace/logs/${RUN_ID}_uvicorn.log "$RUN_ROOT/uvicorn.log" || true
fi

echo "[DONE] $(date -Is)"
echo "[OUTPUT_ROOT] $RUN_ROOT"
