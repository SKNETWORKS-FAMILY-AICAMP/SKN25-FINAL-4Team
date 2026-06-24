#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

# Secret/env file on RunPod. Contains OPENAI_API_KEY and Ollama/DB env.
if [[ -f /root/.skn_eval.env ]]; then
  set -a
  source /root/.skn_eval.env
  set +a
fi

export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
export TEST_LLM_MODEL="${TEST_LLM_MODEL:-qwen3.5:9b}"
export OPENAI_MODEL="${OPENAI_MODEL:-gpt-5.5}"

export ROUTER_DATASET="${ROUTER_DATASET:-dev/eval/data/router_two_stage_eval_300_with_qa60_260622.json}"
export QA_DATASET="${QA_DATASET:-dev/eval/data/anomaly_qa_quality_eval_260617.json}"
export GPT55_ANSWERS_OUT="${GPT55_ANSWERS_OUT:-dev/eval/data/router_two_stage_eval_300_with_qa60_gpt55_answers_260622.json}"

RUN_ROOT="reports/experiments/service_runtime_latency/run_$(date +%Y%m%d_%H%M%S)_qwen35_9b"
mkdir -p "$RUN_ROOT"
LOG="$RUN_ROOT/run.log"
exec > >(tee -a "$LOG") 2>&1

echo "[START] $(date -Is)"
echo "[ENV] model=$TEST_LLM_MODEL openai_model=$OPENAI_MODEL dataset=$ROUTER_DATASET"
echo "[CHECK] Ollama tags"
curl -fsS "$OLLAMA_BASE_URL/api/tags" | python3 -m json.tool | head -120

echo "[STEP 1] ensure 300-router dataset contains QA60"
python3 dev/eval/build_router_dataset_with_qa60_260622.py

echo "[STEP 2] generate GPT-5.5 reference answers for 300 rows"
python3 dev/eval/generate_gpt55_answers_300_260622.py \
  --input "$ROUTER_DATASET" \
  --output "$GPT55_ANSWERS_OUT" \
  --model "$OPENAI_MODEL" \
  --resume

echo "[STEP 3] qwen3.5:9b router metrics on 300 rows"
python3 dev/eval/router_two_stage_metrics_260615.py \
  --mode ollama \
  --ollama-url "$OLLAMA_BASE_URL" \
  --model "$TEST_LLM_MODEL" \
  --dataset "$ROUTER_DATASET" \
  --run-id "qwen35_9b_260622_qa60contained_router_n300" \
  --workers "${ROUTER_WORKERS:-3}" \
  --think off

echo "[STEP 4] qwen3.5:9b QA/BERTScore metrics on 60 rows"
python3 dev/eval/answer_quality_sensitivity_260617.py \
  --ollama-url "$OLLAMA_BASE_URL" \
  --model "$TEST_LLM_MODEL" \
  --dataset "$QA_DATASET" \
  --run-id "qwen35_9b_260622_qa60contained_qa_n60" \
  --limit 60 \
  --workers "${QA_WORKERS:-1}" \
  --think off \
  --bertscore \
  --bertscore-model "${BERTSCORE_MODEL:-distilbert-base-multilingual-cased}"

echo "[STEP 5] service runtime latency, qwen3.5:9b + DB/DW/DM edges"
python3 dev/eval/measure_service_runtime_latency_260622.py \
  --model "$TEST_LLM_MODEL" \
  --ollama-url "$OLLAMA_BASE_URL" \
  --dataset "$ROUTER_DATASET" \
  --router-limit 300 \
  --db-repeats "${DB_REPEATS:-5}" \
  --out "$RUN_ROOT/service_runtime_latency_260622_qwen35_9b.json"

echo "[STEP 6] collect key outputs"
python3 - <<'PY'
import json
from pathlib import Path
paths = {
  'router_metrics': Path('reports/experiments/router_two_stage_classification/qwen35_9b_260622_qa60contained_router_n300/metrics.json'),
  'qa_metrics': Path('reports/experiments/answer_quality_sensitivity/qwen35_9b_260622_qa60contained_qa_n60/metrics.json'),
}
for name, p in paths.items():
    if p.exists():
        d=json.loads(p.read_text(encoding='utf-8'))
        print(name, p)
        print(json.dumps(d.get('summary', {}), ensure_ascii=False, indent=2)[:4000])
    else:
        print(name, 'MISSING', p)
PY

echo "[DONE] $(date -Is)"
echo "[OUTPUT_ROOT] $RUN_ROOT"
