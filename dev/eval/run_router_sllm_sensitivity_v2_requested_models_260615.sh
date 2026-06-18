#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11436/v1}"
DATASET="${DATASET:-dev/eval/data/router_two_stage_eval_300_260617.json}"
RUN_ID_PREFIX="${RUN_ID_PREFIX:-run_260617_anomaly_mart_sensitivity}"
COMPARISON_OUT="${COMPARISON_OUT:-sensitivity_comparison_260617_anomaly_mart_requested_models_n300.json}"
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
  echo "[RUN] model=$m dataset=$DATASET rows=300 ollama_url=$OLLAMA_URL"
  python3 dev/eval/router_two_stage_metrics_260615.py \
    --mode ollama \
    --ollama-url "$OLLAMA_URL" \
    --model "$m" \
    --dataset "$DATASET" \
    --run-id "${RUN_ID_PREFIX}_${safe}_n300"
done

RUN_ID_PREFIX="$RUN_ID_PREFIX" COMPARISON_OUT="$COMPARISON_OUT" python3 - <<'PY'
import json, os
from pathlib import Path
base=Path('reports/experiments/router_two_stage_classification')
run_prefix=os.environ['RUN_ID_PREFIX']
runs=sorted(base.glob(f'{run_prefix}_*_n300/metrics.json'))
summary=[]
for p in runs:
    d=json.loads(p.read_text(encoding='utf-8'))
    s=d['summary']
    preds=d.get('details',{}).get('predictions',[])
    fallback_count=sum(1 for x in preds if x.get('predicted',{}).get('_fallback'))
    parsed_count=sum(1 for x in preds if x.get('predicted',{}).get('_parse_status')=='parsed_llm_json')
    summary.append({
        'run_id': d['run_id'],
        'model': d['model'],
        'row_count': d['dataset']['row_count'],
        'route1_accuracy': s['route1_accuracy'],
        'route1_macro_f1': s['route1_macro_f1'],
        'route2_accuracy_on_query': s['route2_accuracy_on_query'],
        'route2_macro_f1': s['route2_macro_f1'],
        'final_action_accuracy': s['final_action_accuracy'],
        'final_action_macro_f1': s['final_action_macro_f1'],
        'risk_gate_accuracy': s['risk_gate_accuracy'],
        'leakage_error_rate': s['route1_leakage_error_rate'],
        'blocking_error_rate': s['route1_blocking_error_rate'],
        'branch_dropoff_rate': s['branch_dropoff_rate'],
        'fallback_count': fallback_count,
        'parsed_llm_json_count': parsed_count,
        'latency_ms': d['phase_latency_ms']['total'],
    })
out=base/os.environ['COMPARISON_OUT']
out.write_text(json.dumps({'run_prefix': run_prefix, 'summary':summary}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'output':str(out), 'run_prefix': run_prefix, 'summary':summary}, ensure_ascii=False, indent=2))
PY
