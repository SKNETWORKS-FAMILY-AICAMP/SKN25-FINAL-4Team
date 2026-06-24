#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434/v1}"
ROUTER_DATASET="${ROUTER_DATASET:-dev/eval/data/router_two_stage_eval_300_260617.json}"
QA_DATASET="${QA_DATASET:-dev/eval/data/anomaly_qa_quality_eval_260617.json}"
ROUTER_PREFIX="${ROUTER_PREFIX:-offonly_260618_router_prompt_hardened}"
QA_PREFIX="${QA_PREFIX:-offonly_260618_qa_bert_prompt_hardened}"
ROUTER_WORKERS="${ROUTER_WORKERS:-4}"
QA_WORKERS="${QA_WORKERS:-2}"
MODEL_PARALLEL="${MODEL_PARALLEL:-2}"
BERTSCORE_MODEL="${BERTSCORE_MODEL:-distilbert-base-multilingual-cased}"
MODELS=(
  "llama3.2:3b"
  "llama3.1:8b"
  "qwen3.5:0.8b"
  "qwen3.5:2b"
  "qwen3.5:4b"
  "qwen3.5:9b"
  "exaone3.5:7.8b"
  "gemma4:12b"
  "deepseek-r1:8b"
  "phi4-mini:3.8b"
  "qwen3:8b"
)
run_model() {
  local model="$1"
  local safe
  safe=$(echo "$model" | tr ":." "__")
  safe=${safe//__/_}
  local router_id="${ROUTER_PREFIX}_${safe}_think_off_n300"
  local qa_id="${QA_PREFIX}_${safe}_think_off_n40"
  echo "[RUN] model=$model router_id=$router_id qa_id=$qa_id $(date -Is)"
  python3 dev/eval/router_two_stage_metrics_260615.py --mode ollama --ollama-url "$OLLAMA_URL" --model "$model" --dataset "$ROUTER_DATASET" --run-id "$router_id" --workers "$ROUTER_WORKERS" --think off &
  local router_pid=$!
  python3 dev/eval/answer_quality_sensitivity_260617.py --ollama-url "$OLLAMA_URL" --model "$model" --dataset "$QA_DATASET" --run-id "$qa_id" --limit 40 --workers "$QA_WORKERS" --think off --bertscore --bertscore-model "$BERTSCORE_MODEL" &
  local qa_pid=$!
  local rc=0
  wait "$router_pid" || rc=$?
  wait "$qa_pid" || rc=$?
  echo "[DONE] model=$model rc=$rc $(date -Is)"
  return "$rc"
}
active=0
for model in "${MODELS[@]}"; do
  run_model "$model" &
  active=$((active+1))
  if (( active >= MODEL_PARALLEL )); then
    wait -n || true
    active=$((active-1))
  fi
done
wait
