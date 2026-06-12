#!/usr/bin/env bash
# Run app/ems-agent evaluation runners and write shared experiment metrics.
# Intended for RunPod from the repository root.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_STAMP="${RUN_ID:-run_$(date +%Y%m%d_%H%M%S)}"

printf '== repo ==\n'
git rev-parse --abbrev-ref HEAD || true
git rev-parse HEAD || true

printf '\n== install minimal eval dependencies ==\n'
if ! python3 -m pip install -q openai python-dotenv requests; then
  python3 -m pip install --break-system-packages -q openai python-dotenv requests
fi

printf '\n== ollama state ==\n'
if command -v curl >/dev/null 2>&1; then
  curl -fsS "${OLLAMA_TAGS_URL:-http://localhost:11434/api/tags}" || true
  printf '\n'
fi
if command -v ollama >/dev/null 2>&1; then
  ollama list || true
fi

export LLM_PROVIDER="${LLM_PROVIDER:-ollama}"
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434/v1}"
export LLM_MODEL="${LLM_MODEL:-gemma4:12b}"
export LLM_MODEL_FAST="${LLM_MODEL_FAST:-${FAST_LLM_MODEL:-exaone3.5:7.8b}}"
export JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.5}"

# Router preflight defaults (test09 router): run a strict stage-2 dataset check by default.
# To use legacy dataset, override both dataset+schema explicitly.
ROUTER_DATASET="${ROUTER_DATASET:-dev/eval/data/router_stage2_agent_route_500_260612.json}"
ROUTER_DATASET_SCHEMA="${ROUTER_DATASET_SCHEMA:-dev/eval/schemas/router_stage2_agent_route.schema.json}"
ROUTER_PRE_ARGS=(--dataset "$ROUTER_DATASET" --preflight)
if [[ -n "${ROUTER_DATASET_SCHEMA}" ]]; then
  ROUTER_PRE_ARGS+=(--dataset-schema "$ROUTER_DATASET_SCHEMA")
fi

printf '\n== test09 router benchmark, rule baseline ==\n'
python3 dev/eval/router_accuracy_eval.py "${ROUTER_PRE_ARGS[@]}" --run-id "${RUN_STAMP}_test09_rule"

if [[ "${RUN_LLM_ROUTER:-0}" == "1" ]]; then
  printf '\n== test09 router benchmark, LLM fallback ==\n'
  python3 dev/eval/router_accuracy_eval.py --llm "${ROUTER_PRE_ARGS[@]}" --run-id "${RUN_STAMP}_test09_llm"
fi

if [[ "${RUN_HARNESS:-1}" == "1" ]]; then
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "[skip] OPENAI_API_KEY is not set; harness judge requires it."
  else
    printf '\n== test07 harness quality model ==\n'
    python3 dev/eval/harness.py --quality --run-id "${RUN_STAMP}_test07_quality"
    printf '\n== test07 harness fast model ==\n'
    python3 dev/eval/harness.py --fast --run-id "${RUN_STAMP}_test07_fast"
  fi
fi

if [[ "${RUN_SYSTEM_EVAL:-0}" == "1" ]]; then
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "[skip] OPENAI_API_KEY is not set; system_eval judge requires it."
  else
    printf '\n== test07 system eval ==\n'
    python3 dev/eval/system_eval.py --n "${SYSTEM_EVAL_N:-20}" --run-id "${RUN_STAMP}_test07_system"
  fi
fi

printf '\n== comparison ==\n'
python3 dev/eval/compare_results.py

printf '\n== done ==\n'
printf 'reports/experiments\n'
