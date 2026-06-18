#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11436/v1}"
DATASET="${DATASET:-dev/eval/data/router_two_stage_eval_300_260617.json}"
ROUTER_WORKERS="${ROUTER_WORKERS:-4}"
RAGAS_WORKERS="${RAGAS_WORKERS:-2}"
MODEL_PARALLEL="${MODEL_PARALLEL:-2}"
ROUTER_PREFIX="${ROUTER_PREFIX:-optimized_260617_router}"
RAGAS_PREFIX="${RAGAS_PREFIX:-optimized_260617_ragas}"
COMPARISON_OUT="${COMPARISON_OUT:-optimized_router_ragas_sensitivity_comparison_260617.json}"

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
  local m="$1"
  local safe
  safe=$(echo "$m" | tr ':.' '__')
  echo "[MODEL-START] $m router_workers=$ROUTER_WORKERS ragas_workers=$RAGAS_WORKERS $(date -Is)"
  python3 dev/eval/router_two_stage_metrics_260615.py \
    --mode ollama \
    --ollama-url "$OLLAMA_URL" \
    --model "$m" \
    --dataset "$DATASET" \
    --workers "$ROUTER_WORKERS" \
    --run-id "${ROUTER_PREFIX}_${safe}_n300" &
  local router_pid=$!
  python3 dev/eval/answer_quality_sensitivity_260617.py \
    --ollama-url "$OLLAMA_URL" \
    --dataset "$DATASET" \
    --model "$m" \
    --workers "$RAGAS_WORKERS" \
    --run-id "${RAGAS_PREFIX}_${safe}_n40" &
  local ragas_pid=$!
  local rc=0
  wait "$router_pid" || rc=$?
  wait "$ragas_pid" || rc=$?
  echo "[MODEL-DONE] $m rc=$rc $(date -Is)"
  return "$rc"
}
export -f run_model
export OLLAMA_URL DATASET ROUTER_WORKERS RAGAS_WORKERS ROUTER_PREFIX RAGAS_PREFIX

printf "%s\n" "${MODELS[@]}" | xargs -P "$MODEL_PARALLEL" -I{} bash -lc 'run_model "$1"' _ {}

COMPARISON_OUT="$COMPARISON_OUT" ROUTER_PREFIX="$ROUTER_PREFIX" RAGAS_PREFIX="$RAGAS_PREFIX" python3 - <<'PY'
import json, os, math
from pathlib import Path
root=Path('reports/experiments')
router_base=root/'router_two_stage_classification'
ragas_base=root/'answer_quality_sensitivity'
router_prefix=os.environ['ROUTER_PREFIX']
ragas_prefix=os.environ['RAGAS_PREFIX']

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

def safe_model_from_run(run_id,prefix,suffix):
    x=run_id[len(prefix)+1:]
    if x.endswith(suffix): x=x[:-len(suffix)]
    return x

def ranks(vals):
    order=sorted((v,i) for i,v in enumerate(vals))
    out=[0.0]*len(vals); i=0
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
    xs,ys=zip(*pairs); rx,ry=ranks(list(xs)),ranks(list(ys))
    mx,my=sum(rx)/len(rx),sum(ry)/len(ry)
    num=sum((a-mx)*(b-my) for a,b in zip(rx,ry))
    denx=math.sqrt(sum((a-mx)**2 for a in rx)); deny=math.sqrt(sum((b-my)**2 for b in ry))
    return round(num/(denx*deny),4) if denx and deny else None

router=[]
for p in sorted(router_base.glob(f'{router_prefix}_*_n300/metrics.json')):
    d=load(p); s=d['summary']
    router.append({'model':d.get('model'), 'run_id':d['run_id'], 'workers':d.get('workers'), 'row_count':d['dataset']['row_count'], 'route1_accuracy':s.get('route1_accuracy'), 'route2_accuracy_on_query':s.get('route2_accuracy_on_query'), 'final_action_accuracy':s.get('final_action_accuracy'), 'risk_gate_accuracy':s.get('risk_gate_accuracy'), 'branch_dropoff_rate':s.get('branch_dropoff_rate'), 'latency_ms_total':d.get('phase_latency_ms',{}).get('total'), 'metrics_path':str(p)})
ragas=[]
for p in sorted(ragas_base.glob(f'{ragas_prefix}_*_n40/metrics.json')):
    d=load(p); s=d['summary']
    ragas.append({'model':d.get('model'), 'run_id':d['run_id'], 'model_params_b':d.get('model_params_b'), 'workers':d.get('workers'), 'row_count':d['dataset']['row_count'], 'answer_quality_composite':s.get('answer_quality_composite'), 'numeric_f1':s.get('numeric_f1'), 'evidence_numeric_recall':s.get('evidence_numeric_recall'), 'reference_token_f1':s.get('reference_token_f1'), 'rouge_l_f1':s.get('rouge_l_f1'), 'source_leakage_rate':s.get('source_leakage_rate'), 'avg_latency_ms_per_row':s.get('avg_latency_ms_per_row'), 'metrics_path':str(p)})
params=[x.get('model_params_b') for x in ragas]
correlations={}
for metric in ['answer_quality_composite','numeric_f1','evidence_numeric_recall','reference_token_f1','rouge_l_f1','avg_latency_ms_per_row']:
    correlations[f'params_b_vs_{metric}_spearman']=spearman(params,[x.get(metric) for x in ragas])
out=router_base/os.environ['COMPARISON_OUT']
payload={'router_prefix':router_prefix,'ragas_prefix':ragas_prefix,'router_summary':router,'ragas_summary':ragas,'parameter_count_correlations':correlations}
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
# mirror comparison into ragas directory too for discoverability
(ragas_base/os.environ['COMPARISON_OUT']).write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'output':str(out), **payload}, ensure_ascii=False, indent=2))
PY
