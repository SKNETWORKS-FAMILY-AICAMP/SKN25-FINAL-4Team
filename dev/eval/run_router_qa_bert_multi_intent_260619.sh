#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434/v1}"
export ROUTER_DATASET="${ROUTER_DATASET:-dev/eval/data/router_two_stage_eval_300_260617.json}"
export QA_DATASET="${QA_DATASET:-dev/eval/data/anomaly_qa_quality_eval_260617.json}"
export ROUTER_PREFIX="${ROUTER_PREFIX:-multi_intent_260619_router_schema_hardened}"
export QA_PREFIX="${QA_PREFIX:-multi_intent_260619_qa_bert_contract_hardened}"
export ROUTER_WORKERS="${ROUTER_WORKERS:-3}"
export QA_WORKERS="${QA_WORKERS:-1}"
export MODEL_PARALLEL="${MODEL_PARALLEL:-2}"
export BERTSCORE_MODEL="${BERTSCORE_MODEL:-distilbert-base-multilingual-cased}"
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
  local model="$1" safe router_id qa_id
  safe=$(echo "$model" | tr ":." "__"); safe=${safe//__/_}
  router_id="${ROUTER_PREFIX}_${safe}_think_off_n300"
  qa_id="${QA_PREFIX}_${safe}_think_off_n60"
  echo "[RUN] model=$model router_id=$router_id qa_id=$qa_id $(date -Is)"
  python3 dev/eval/router_two_stage_metrics_260615.py --mode ollama --ollama-url "$OLLAMA_URL" --model "$model" --dataset "$ROUTER_DATASET" --run-id "$router_id" --workers "$ROUTER_WORKERS" --think off
  python3 dev/eval/answer_quality_sensitivity_260617.py --ollama-url "$OLLAMA_URL" --model "$model" --dataset "$QA_DATASET" --run-id "$qa_id" --limit 60 --workers "$QA_WORKERS" --think off --bertscore --bertscore-model "$BERTSCORE_MODEL"
  echo "[DONE] model=$model $(date -Is)"
}
active=0
for model in "${MODELS[@]}"; do
  run_model "$model" &
  active=$((active+1))
  if (( active >= MODEL_PARALLEL )); then
    wait -n
    active=$((active-1))
  fi
done
wait
python3 - <<"PY"
import json, os, math
from pathlib import Path
router_base=Path("reports/experiments/router_two_stage_classification")
qa_base=Path("reports/experiments/answer_quality_sensitivity")
rp=os.environ["ROUTER_PREFIX"]; qp=os.environ["QA_PREFIX"]
def load(p):
    with open(p, encoding="utf-8") as f: return json.load(f)
router=[]; qa=[]
for p in sorted(router_base.glob(f"{rp}_*_think_off_n300/metrics.json")):
    d=load(p); s=d["summary"]
    router.append({"model":d.get("model"),"run_id":d["run_id"],"row_count":d["dataset"]["row_count"],"route1_accuracy":s.get("route1_accuracy"),"route1_macro_f1":s.get("route1_macro_f1"),"route2_accuracy_on_query":s.get("route2_accuracy_on_query"),"final_action_accuracy":s.get("final_action_accuracy"),"final_action_macro_f1":s.get("final_action_macro_f1"),"parsed_llm_json_count":s.get("parsed_llm_json_count"),"fallback_count":s.get("fallback_count"),"parse_error_count":s.get("parse_error_count"),"invalid_prediction_count":s.get("invalid_prediction_count"),"fallback_reason_counts":s.get("fallback_reason_counts"),"multi_intent_prf":s.get("route1_per_label_precision_recall_f1",{}).get("multi_intent")})
for p in sorted(qa_base.glob(f"{qp}_*_think_off_n60/metrics.json")):
    d=load(p); s=d["summary"]
    qa.append({"model":d.get("model"),"run_id":d["run_id"],"row_count":d["dataset"]["row_count"],"answer_quality_composite":s.get("answer_quality_composite"),"numeric_f1":s.get("numeric_f1"),"evidence_numeric_recall":s.get("evidence_numeric_recall"),"rouge_l_f1":s.get("rouge_l_f1"),"reference_token_f1":s.get("reference_token_f1"),"bertscore_f1":s.get("bertscore_f1"),"bertscore_evaluated_count":s.get("bertscore_evaluated_count"),"bertscore_evaluation_unavailable_count":s.get("bertscore_evaluation_unavailable_count"),"error_rate":s.get("error_rate"),"source_leakage_rate":s.get("source_leakage_rate"),"avg_latency_ms_per_row":s.get("avg_latency_ms_per_row")})
payload={"router_prefix":rp,"qa_prefix":qp,"router_summary":router,"qa_summary":qa,"notes":"multi_intent dataset; router JSON schema-hardened; QA does not score reasoning/thinking as answer; BERTScore averages eligible answers only and reports unavailable count separately."}
out=router_base/f"{rp}_qa_bert_comparison.json"
out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"comparison":str(out),"router_runs":len(router),"qa_runs":len(qa)},ensure_ascii=False,indent=2))
PY
