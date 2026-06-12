#!/usr/bin/env bash
# Run current-order test00~test09 sequentially on app/ems-agent branch.
# Requires Ollama for --llm router and harness tests. OpenAI judge is optional;
# by default local Gemma4 is used as judge through Ollama-compatible OpenAI API.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_STAMP="${RUN_ID:-run_$(date +%Y%m%d_%H%M%S)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

export LLM_PROVIDER="${LLM_PROVIDER:-ollama}"
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434/v1}"
export LLM_MODEL="${LLM_MODEL:-gemma4:12b}"
export FAST_LLM_MODEL="${FAST_LLM_MODEL:-exaone3.5:7.8b}"
export LLM_MODEL_FAST="${LLM_MODEL_FAST:-$FAST_LLM_MODEL}"
# If no external judge key is provided, use local Ollama as OpenAI-compatible judge.
export OPENAI_API_KEY="${OPENAI_API_KEY:-ollama}"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://localhost:11434/v1}"
export JUDGE_MODEL="${JUDGE_MODEL:-gemma4:12b}"

printf '== repo ==\n'
git rev-parse --abbrev-ref HEAD || true
git rev-parse HEAD || true

printf '\n== test00~test06/test08 deterministic current-order evals ==\n'
"$PYTHON_BIN" dev/eval/current_order_eval.py all --run-id "$RUN_STAMP"

printf '\n== test07 fast answer gate ==\n'
"$PYTHON_BIN" dev/eval/harness.py --fast --run-id "${RUN_STAMP}_test07_fast"

printf '\n== test07 quality answer gate ==\n'
"$PYTHON_BIN" dev/eval/harness.py --quality --run-id "${RUN_STAMP}_test07_quality"

# Override these for legacy dataset mode:
#   ROUTER_DATASET=dev/eval/data/router_5route_eval_500_260610.json
#   ROUTER_DATASET_SCHEMA=<matching schema path>
ROUTER_DATASET="${ROUTER_DATASET:-dev/eval/data/router_stage2_agent_route_500_260612.json}"
ROUTER_DATASET_SCHEMA="${ROUTER_DATASET_SCHEMA:-dev/eval/schemas/router_stage2_agent_route.schema.json}"
ROUTER_PRE_ARGS=(--dataset "$ROUTER_DATASET" --preflight)
if [[ -n "${ROUTER_DATASET_SCHEMA}" ]]; then
  ROUTER_PRE_ARGS+=(--dataset-schema "$ROUTER_DATASET_SCHEMA")
fi

printf '\n== test09 router rule baseline ==\n'
"$PYTHON_BIN" dev/eval/router_accuracy_eval.py "${ROUTER_PRE_ARGS[@]}" --run-id "${RUN_STAMP}_test09_rule"

printf '\n== test09 router LLM fallback ==\n'
"$PYTHON_BIN" dev/eval/router_accuracy_eval.py --llm "${ROUTER_PRE_ARGS[@]}" --run-id "${RUN_STAMP}_test09_llm"

printf '\n== comparison ==\n'
"$PYTHON_BIN" dev/eval/compare_results.py

printf '\n== completed current-order test00~test09 ==\n'
printf 'reports/experiments\n'
