#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434/v1}"
ROUTER_DATASET="${ROUTER_DATASET:-dev/eval/data/router_two_stage_eval_300_260617.json}"
RAGAS_DATASET="${RAGAS_DATASET:-dev/eval/data/anomaly_qa_quality_eval_260617.json}"
ROUTER_WORKERS="${ROUTER_WORKERS:-4}"
RAGAS_WORKERS="${RAGAS_WORKERS:-2}"
MODEL_PARALLEL="${MODEL_PARALLEL:-2}"
ROUTER_PREFIX="${ROUTER_PREFIX:-gated_260618_router_api_chat_fixed}"
RAGAS_PREFIX="${RAGAS_PREFIX:-gated_260618_ragas_bert_fixed}"
COMPARISON_OUT="${COMPARISON_OUT:-gated_router_ragas_bert_fixed_comparison_260618.json}"
BERTSCORE_MODEL="${BERTSCORE_MODEL:-distilbert-base-multilingual-cased}"
PREFLIGHT_LIMIT="${PREFLIGHT_LIMIT:-20}"
ROUTER_MAX_FALLBACK_RATE="${ROUTER_MAX_FALLBACK_RATE:-0.20}"
QA_MAX_ERROR_RATE="${QA_MAX_ERROR_RATE:-0.20}"

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

json_get() { python3 - "$1" "$2" <<'PY'
import json,sys
p,k=sys.argv[1:3]
d=json.load(open(p,encoding='utf-8'))
cur=d
for part in k.split('.'):
    cur=cur.get(part,{}) if isinstance(cur,dict) else None
print(cur if cur is not None else '')
PY
}

write_skip() {
  local family="$1" outdir="$2" run_id="$3" model="$4" think="$5" reason="$6" preflight="$7"
  mkdir -p "$outdir/$run_id"
  python3 - "$outdir/$run_id/metrics.json" "$family" "$run_id" "$model" "$think" "$reason" "$preflight" <<'PY'
import json,sys,datetime
path,family,run_id,model,think,reason,preflight=sys.argv[1:]
payload={
  'schema_version':'experiment-metrics.v1', 'test_id':family, 'run_id':run_id,
  'mode':'skipped_after_preflight', 'model':model, 'think':think,
  'summary':{'skipped':True,'skip_reason':reason}, 'gates':{'preflight_passed':False},
  'errors':[{'reason':reason,'preflight_metrics':preflight}], 'details':{}
}
open(path,'w',encoding='utf-8').write(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'skipped':path,'reason':reason},ensure_ascii=False))
PY
}

run_pair() {
  local m="$1" think="$2" safe
  safe=$(echo "$m" | tr ':.' '__')
  local router_run="${ROUTER_PREFIX}_${safe}_think_${think}_n300"
  local qa_run="${RAGAS_PREFIX}_${safe}_think_${think}_n40"
  local router_pre="preflight_${router_run}_limit${PREFLIGHT_LIMIT}"
  local qa_pre="preflight_${qa_run}_limit${PREFLIGHT_LIMIT}"
  echo "[PREFLIGHT-START] model=$m think=$think $(date -Is)"

  python3 dev/eval/router_two_stage_metrics_260615.py --mode ollama --ollama-url "$OLLAMA_URL" --model "$m" --dataset "$ROUTER_DATASET" --limit "$PREFLIGHT_LIMIT" --workers 1 --think "$think" --run-id "$router_pre" >/tmp/${router_pre}.log 2>&1 || true
  python3 dev/eval/answer_quality_sensitivity_260617.py --ollama-url "$OLLAMA_URL" --dataset "$RAGAS_DATASET" --model "$m" --limit 5 --workers 1 --think "$think" --run-id "$qa_pre" >/tmp/${qa_pre}.log 2>&1 || true

  local rmet="reports/experiments/router_two_stage_classification/${router_pre}/metrics.json"
  local qmet="reports/experiments/answer_quality_sensitivity/${qa_pre}/metrics.json"
  local rfallback rparsed rerr qerr
  rfallback=$(json_get "$rmet" summary.fallback_count || echo 999)
  rparsed=$(json_get "$rmet" summary.parsed_llm_json_count || echo 0)
  rerr=$(json_get "$rmet" summary.parse_error_count || echo 999)
  qerr=$(json_get "$qmet" summary.error_rate || echo 1)
  local gate_file="/tmp/gate_${safe}_${think}.txt"
  python3 - "$rfallback" "$rerr" "$rparsed" "$PREFLIGHT_LIMIT" "$qerr" "$ROUTER_MAX_FALLBACK_RATE" "$QA_MAX_ERROR_RATE" <<'PY' >"$gate_file"
import sys
rf,re,rp,limit,qerr,rfmax,qmax=map(float,sys.argv[1:])
router_bad=(rf/limit)>rfmax or (re/limit)>rfmax or rp<1
qa_bad=qerr>qmax
print('router_bad' if router_bad else 'router_ok')
print('qa_bad' if qa_bad else 'qa_ok')
PY
  local router_gate qa_gate
  router_gate=$(sed -n '1p' "$gate_file")
  qa_gate=$(sed -n '2p' "$gate_file")
  echo "[PREFLIGHT] model=$m think=$think router=$router_gate fallback=$rfallback parse_err=$rerr parsed=$rparsed qa=$qa_gate qerr=$qerr"

  local rc=0 pids=()
  if [[ "$router_gate" == "router_ok" ]]; then
    python3 dev/eval/router_two_stage_metrics_260615.py --mode ollama --ollama-url "$OLLAMA_URL" --model "$m" --dataset "$ROUTER_DATASET" --workers "$ROUTER_WORKERS" --think "$think" --run-id "$router_run" & pids+=("$!")
  else
    write_skip router_two_stage_classification reports/experiments/router_two_stage_classification "$router_run" "$m" "$think" "router_preflight_failed:fallback=$rfallback parse_err=$rerr parsed=$rparsed limit=$PREFLIGHT_LIMIT" "$rmet"
  fi
  if [[ "$qa_gate" == "qa_ok" ]]; then
    python3 dev/eval/answer_quality_sensitivity_260617.py --ollama-url "$OLLAMA_URL" --dataset "$RAGAS_DATASET" --model "$m" --workers "$RAGAS_WORKERS" --think "$think" --bertscore --bertscore-model "$BERTSCORE_MODEL" --run-id "$qa_run" & pids+=("$!")
  else
    write_skip answer_quality_sensitivity reports/experiments/answer_quality_sensitivity "$qa_run" "$m" "$think" "qa_preflight_failed:error_rate=$qerr" "$qmet"
  fi
  for pid in "${pids[@]}"; do wait "$pid" || rc=$?; done
  echo "[RUN-DONE] model=$m think=$think rc=$rc $(date -Is)"
  return "$rc"
}
export -f run_pair json_get write_skip
export OLLAMA_URL ROUTER_DATASET RAGAS_DATASET ROUTER_WORKERS RAGAS_WORKERS ROUTER_PREFIX RAGAS_PREFIX BERTSCORE_MODEL PREFLIGHT_LIMIT ROUTER_MAX_FALLBACK_RATE QA_MAX_ERROR_RATE

tmp=$(mktemp)
for m in "${MODELS[@]}"; do for t in $(think_modes_for_model "$m"); do printf '%s\t%s\n' "$m" "$t" >> "$tmp"; done; done
cat "$tmp" | xargs -P "$MODEL_PARALLEL" -L 1 bash -lc 'run_pair "$1" "$2"' _
rm -f "$tmp"

COMPARISON_OUT="$COMPARISON_OUT" ROUTER_PREFIX="$ROUTER_PREFIX" RAGAS_PREFIX="$RAGAS_PREFIX" python3 - <<'PY'
import json, os
from pathlib import Path
router_base=Path('reports/experiments/router_two_stage_classification'); qa_base=Path('reports/experiments/answer_quality_sensitivity')
rp=os.environ['ROUTER_PREFIX']; qp=os.environ['RAGAS_PREFIX']
def load(p): return json.loads(p.read_text(encoding='utf-8'))
router=[]
for p in sorted(router_base.glob(f'{rp}_*_n300/metrics.json')):
 d=load(p); s=d.get('summary',{}); router.append({'model':d.get('model'),'think':d.get('think'),'run_id':d.get('run_id'),'skipped':s.get('skipped',False),'skip_reason':s.get('skip_reason'),'route1_accuracy':s.get('route1_accuracy'),'route2_accuracy_on_query':s.get('route2_accuracy_on_query'),'final_action_accuracy':s.get('final_action_accuracy'),'parsed_llm_json_count':s.get('parsed_llm_json_count'),'fallback_count':s.get('fallback_count'),'parse_error_count':s.get('parse_error_count'),'metrics_path':str(p)})
qa=[]
for p in sorted(qa_base.glob(f'{qp}_*_n40/metrics.json')):
 d=load(p); s=d.get('summary',{}); qa.append({'model':d.get('model'),'think':d.get('think'),'run_id':d.get('run_id'),'skipped':s.get('skipped',False),'skip_reason':s.get('skip_reason'),'answer_quality_composite':s.get('answer_quality_composite'),'numeric_f1':s.get('numeric_f1'),'bertscore_f1':s.get('bertscore_f1'),'error_rate':s.get('error_rate'),'metrics_path':str(p)})
payload={'router_prefix':rp,'ragas_prefix':qp,'router_summary':router,'ragas_summary':qa}
for base in [router_base, qa_base]:
 (base/os.environ['COMPARISON_OUT']).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'output':str(router_base/os.environ['COMPARISON_OUT']),'router_runs':len(router),'qa_runs':len(qa),'router_skipped':sum(x.get('skipped',False) for x in router),'qa_skipped':sum(x.get('skipped',False) for x in qa)},ensure_ascii=False,indent=2))
PY
