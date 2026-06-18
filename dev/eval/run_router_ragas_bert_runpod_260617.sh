#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434/v1}"
ROUTER_DATASET="${ROUTER_DATASET:-${DATASET:-dev/eval/data/router_two_stage_eval_300_260617.json}}"
RAGAS_DATASET="${RAGAS_DATASET:-dev/eval/data/anomaly_qa_quality_eval_260617.json}"
ROUTER_WORKERS="${ROUTER_WORKERS:-4}"
RAGAS_WORKERS="${RAGAS_WORKERS:-2}"
MODEL_PARALLEL="${MODEL_PARALLEL:-2}"
ROUTER_PREFIX="${ROUTER_PREFIX:-runpod_260617_router_api_chat}"
RAGAS_PREFIX="${RAGAS_PREFIX:-runpod_260617_ragas_bert}"
COMPARISON_OUT="${COMPARISON_OUT:-runpod_router_ragas_bert_comparison_260617.json}"
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

think_modes_for_model() {
  case "$1" in
    qwen3.5:*|qwen3:*|deepseek-r1:*|gemma4:*) echo "off on" ;;
    *) echo "off" ;;
  esac
}

run_model_think() {
  local m="$1"
  local think="$2"
  local safe
  safe=$(echo "$m" | tr ':.' '__')
  echo "[RUN-START] model=$m think=$think $(date -Is)"
  python3 dev/eval/router_two_stage_metrics_260615.py \
    --mode ollama \
    --ollama-url "$OLLAMA_URL" \
    --model "$m" \
    --dataset "$ROUTER_DATASET" \
    --workers "$ROUTER_WORKERS" \
    --think "$think" \
    --run-id "${ROUTER_PREFIX}_${safe}_think_${think}_n300" &
  local router_pid=$!
  python3 dev/eval/answer_quality_sensitivity_260617.py \
    --ollama-url "$OLLAMA_URL" \
    --dataset "$RAGAS_DATASET" \
    --model "$m" \
    --workers "$RAGAS_WORKERS" \
    --think "$think" \
    --bertscore \
    --bertscore-model "$BERTSCORE_MODEL" \
    --run-id "${RAGAS_PREFIX}_${safe}_think_${think}_n40" &
  local ragas_pid=$!
  local rc=0
  wait "$router_pid" || rc=$?
  wait "$ragas_pid" || rc=$?
  echo "[RUN-DONE] model=$m think=$think rc=$rc $(date -Is)"
  return "$rc"
}
export -f run_model_think
export OLLAMA_URL ROUTER_DATASET RAGAS_DATASET ROUTER_WORKERS RAGAS_WORKERS ROUTER_PREFIX RAGAS_PREFIX BERTSCORE_MODEL

tmp_list=$(mktemp)
for m in "${MODELS[@]}"; do
  for t in $(think_modes_for_model "$m"); do
    printf '%s\t%s\n' "$m" "$t" >> "$tmp_list"
  done
done
cat "$tmp_list"
cat "$tmp_list" | xargs -P "$MODEL_PARALLEL" -L 1 bash -lc 'run_model_think "$1" "$2"' _
rm -f "$tmp_list"

COMPARISON_OUT="$COMPARISON_OUT" ROUTER_PREFIX="$ROUTER_PREFIX" RAGAS_PREFIX="$RAGAS_PREFIX" python3 - <<'PY'
import json, os, math
from pathlib import Path
router_base=Path('reports/experiments/router_two_stage_classification')
ragas_base=Path('reports/experiments/answer_quality_sensitivity')
router_prefix=os.environ['ROUTER_PREFIX']; ragas_prefix=os.environ['RAGAS_PREFIX']

def load(p): return json.loads(p.read_text(encoding='utf-8'))
router=[]
for p in sorted(router_base.glob(f'{router_prefix}_*_n300/metrics.json')):
    d=load(p); s=d['summary']
    router.append({'model':d.get('model'),'think':d.get('think'),'run_id':d['run_id'],'workers':d.get('workers'),'route1_accuracy':s.get('route1_accuracy'),'route2_accuracy_on_query':s.get('route2_accuracy_on_query'),'final_action_accuracy':s.get('final_action_accuracy'),'risk_gate_accuracy':s.get('risk_gate_accuracy'),'branch_dropoff_rate':s.get('branch_dropoff_rate'),'parsed_llm_json_count':s.get('parsed_llm_json_count'),'fallback_count':s.get('fallback_count'),'parse_error_count':s.get('parse_error_count'),'latency_ms_total':d.get('phase_latency_ms',{}).get('total'),'metrics_path':str(p)})
ragas=[]
for p in sorted(ragas_base.glob(f'{ragas_prefix}_*_n40/metrics.json')):
    d=load(p); s=d['summary']
    ragas.append({'model':d.get('model'),'think':d.get('think'),'run_id':d['run_id'],'model_params_b':d.get('model_params_b'),'workers':d.get('workers'),'answer_quality_composite':s.get('answer_quality_composite'),'numeric_f1':s.get('numeric_f1'),'evidence_numeric_recall':s.get('evidence_numeric_recall'),'reference_token_f1':s.get('reference_token_f1'),'rouge_l_f1':s.get('rouge_l_f1'),'bertscore_f1':s.get('bertscore_f1'),'bertscore_precision':s.get('bertscore_precision'),'bertscore_recall':s.get('bertscore_recall'),'source_leakage_rate':s.get('source_leakage_rate'),'avg_latency_ms_per_row':s.get('avg_latency_ms_per_row'),'bertscore_status':d.get('bertscore'),'metrics_path':str(p)})

def ranks(vals):
    order=sorted((v,i) for i,v in enumerate(vals)); out=[0.0]*len(vals); i=0
    while i<len(order):
        j=i
        while j+1<len(order) and order[j+1][0]==order[i][0]: j+=1
        rank=(i+j+2)/2
        for _,idx in order[i:j+1]: out[idx]=rank
        i=j+1
    return out

def spearman(xs,ys):
    pairs=[(x,y) for x,y in zip(xs,ys) if isinstance(x,(int,float)) and isinstance(y,(int,float))]
    if len(pairs)<2: return None
    xs,ys=zip(*pairs); rx,ry=ranks(list(xs)),ranks(list(ys)); mx=sum(rx)/len(rx); my=sum(ry)/len(ry)
    num=sum((a-mx)*(b-my) for a,b in zip(rx,ry)); denx=math.sqrt(sum((a-mx)**2 for a in rx)); deny=math.sqrt(sum((b-my)**2 for b in ry))
    return round(num/(denx*deny),4) if denx and deny else None
# correlation on think=off only to avoid duplicate model sizes
base=[x for x in ragas if x.get('think')=='off']
params=[x.get('model_params_b') for x in base]
correlations={}
for metric in ['answer_quality_composite','numeric_f1','evidence_numeric_recall','reference_token_f1','rouge_l_f1','bertscore_f1','avg_latency_ms_per_row']:
    correlations[f'params_b_vs_{metric}_spearman_think_off']=spearman(params,[x.get(metric) for x in base])
payload={'router_prefix':router_prefix,'ragas_prefix':ragas_prefix,'router_summary':router,'ragas_summary':ragas,'parameter_count_correlations':correlations}
out1=router_base/os.environ['COMPARISON_OUT']; out2=ragas_base/os.environ['COMPARISON_OUT']
out1.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
out2.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'output':str(out1), 'router_runs':len(router), 'ragas_runs':len(ragas), 'parameter_count_correlations':correlations}, ensure_ascii=False, indent=2))
PY
