#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11436/v1}"
DATASET="${DATASET:-dev/eval/data/router_two_stage_eval_300_260617.json}"
RUN_ID_PREFIX="${RUN_ID_PREFIX:-answer_quality_260617}"
COMPARISON_OUT="${COMPARISON_OUT:-answer_quality_sensitivity_comparison_260617_n40.json}"
LIMIT_ARG=()
if [[ "${LIMIT:-0}" != "0" ]]; then
  LIMIT_ARG=(--limit "$LIMIT")
fi

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

for m in "${MODELS[@]}"; do
  safe=$(echo "$m" | tr ':.' '__')
  echo "[ANSWER-QUALITY-RUN] model=$m dataset=$DATASET ollama_url=$OLLAMA_URL limit=${LIMIT:-40}"
  python3 dev/eval/answer_quality_sensitivity_260617.py \
    --ollama-url "$OLLAMA_URL" \
    --dataset "$DATASET" \
    --model "$m" \
    --run-id "${RUN_ID_PREFIX}_${safe}_n40" \
    "${LIMIT_ARG[@]}"
done

RUN_ID_PREFIX="$RUN_ID_PREFIX" COMPARISON_OUT="$COMPARISON_OUT" python3 - <<'PY'
import json, os, math
from pathlib import Path
base=Path('reports/experiments/answer_quality_sensitivity')
run_prefix=os.environ['RUN_ID_PREFIX']
runs=sorted(base.glob(f'{run_prefix}_*_n40/metrics.json'))
summary=[]
for p in runs:
    d=json.loads(p.read_text(encoding='utf-8'))
    s=d['summary']
    summary.append({
        'run_id': d['run_id'],
        'model': d['model'],
        'model_params_b': d.get('model_params_b'),
        'row_count': d['dataset']['row_count'],
        'answer_quality_composite': s.get('answer_quality_composite'),
        'numeric_f1': s.get('numeric_f1'),
        'evidence_numeric_recall': s.get('evidence_numeric_recall'),
        'reference_token_f1': s.get('reference_token_f1'),
        'rouge_l_f1': s.get('rouge_l_f1'),
        'source_leakage_rate': s.get('source_leakage_rate'),
        'error_rate': s.get('error_rate'),
        'avg_latency_ms_per_row': s.get('avg_latency_ms_per_row'),
        'metrics_path': str(p),
    })

def ranks(vals):
    order=sorted((v,i) for i,v in enumerate(vals))
    out=[0.0]*len(vals); i=0
    while i < len(order):
        j=i
        while j+1 < len(order) and order[j+1][0] == order[i][0]:
            j+=1
        rank=(i+j+2)/2
        for _,idx in order[i:j+1]: out[idx]=rank
        i=j+1
    return out

def spearman(xs, ys):
    pairs=[(x,y) for x,y in zip(xs,ys) if isinstance(x,(int,float)) and isinstance(y,(int,float))]
    if len(pairs)<2: return None
    xs,ys=zip(*pairs); rx,ry=ranks(list(xs)),ranks(list(ys))
    mx,my=sum(rx)/len(rx),sum(ry)/len(ry)
    num=sum((a-mx)*(b-my) for a,b in zip(rx,ry))
    denx=math.sqrt(sum((a-mx)**2 for a in rx)); deny=math.sqrt(sum((b-my)**2 for b in ry))
    return round(num/(denx*deny),4) if denx and deny else None

params=[x.get('model_params_b') for x in summary]
correlations={}
for metric in ['answer_quality_composite','numeric_f1','evidence_numeric_recall','reference_token_f1','rouge_l_f1','avg_latency_ms_per_row']:
    correlations[f'params_b_vs_{metric}_spearman']=spearman(params,[x.get(metric) for x in summary])

out=base/os.environ['COMPARISON_OUT']
payload={'run_prefix': run_prefix, 'summary': summary, 'parameter_count_correlations': correlations}
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'output':str(out), **payload}, ensure_ascii=False, indent=2))
PY
