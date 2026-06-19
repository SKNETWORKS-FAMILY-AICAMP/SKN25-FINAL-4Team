# -*- coding: utf-8 -*-
"""Build QA quality dataset with anomaly answers and multi_intent clarification rows."""
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'dev/eval/data/router_two_stage_eval_300_260617.json'
OUT = ROOT / 'dev/eval/data/anomaly_qa_quality_eval_260617.json'


def api_chat(base_url: str, model: str, question: str, evidence: dict, seed_answer: str) -> str:
    endpoint = base_url.rstrip('/')
    if endpoint.endswith('/v1'):
        endpoint = endpoint[:-3]
    endpoint += '/api/chat'
    system = (
        '너는 실제 공장 에너지 관리 시스템의 챗봇이다. '
        '질문에 대해 evidence의 수치와 설비 정보만 사용해 한국어로 간결하게 답한다. '
        'Nature, 논문, table, source_url 같은 출처 표현은 절대 쓰지 않는다. '
        '숫자는 evidence와 seed_answer의 값을 보존한다.'
    )
    user = '질문:\n{}\n\nevidence:\n{}\n\nseed_answer:\n{}\n\n최종 모범 답변만 작성해.'.format(
        question, json.dumps(evidence, ensure_ascii=False, sort_keys=True), seed_answer
    )
    payload = {
        'model': model,
        'messages': [{'role':'system','content':system},{'role':'user','content':user}],
        'stream': False,
        'think': False,
        'options': {'temperature': 0, 'num_predict': 384},
    }
    req = request.Request(endpoint, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type':'application/json'})
    with request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode('utf-8', errors='replace'))
    return (data.get('message') or {}).get('content','').strip()


def polish_gpt55_style(question: str, evidence: dict, answer_text: str) -> str:
    # Deterministic operational reference from curated mart answer_text.
    text = re.sub(r'\s+', ' ', answer_text).strip()
    text = text.replace('경보 step', '경보 단계').replace('경보 event', '경보 이벤트')
    text = text.replace('계량기는 총', '계량기는 총')
    if not text.endswith('.'):
        text += '.'
    return text


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--src', default=str(SRC))
    ap.add_argument('--out', default=str(OUT))
    ap.add_argument('--ollama-url', default='http://127.0.0.1:11434/v1')
    ap.add_argument('--sqllm-model', default='gemma4:12b')
    ap.add_argument('--skip-sqllm', action='store_true')
    args=ap.parse_args()
    obj=json.loads(Path(args.src).read_text(encoding='utf-8'))
    rows=[]
    existing_out = Path(args.out)
    if existing_out.exists():
        try:
            prev = json.loads(existing_out.read_text(encoding='utf-8'))
            for old_row in prev.get('rows', []):
                # Preserve curated anomaly QA rows that are not regenerated from
                # the router-only dataset. Multi-intent rows are regenerated below
                # so prompt/policy changes are reflected deterministically.
                if old_row.get('expected_route1') == 'query' and (old_row.get('reference_answer') or old_row.get('answer_text')) and old_row.get('answer_evidence'):
                    rows.append(old_row)
        except Exception:
            pass
    for r in obj['rows']:
        if r.get('answer_text') and r.get('answer_evidence'):
            q=r['message']; ev=r['answer_evidence']; seed=r['answer_text']
            gpt_ref=polish_gpt55_style(q, ev, seed)
        elif r.get('expected_route1') == 'multi_intent':
            q=r['message']
            ev={
                'intent_policy': 'multi_intent',
                'allowed_route1': ['query','action_request','approval_required','off_topic','multi_intent'],
                'expected_final_action': 'gate:multi_intent',
                'user_message': q,
                'safe_behavior': 'ask_user_to_split_or_choose_first_task',
            }
            gpt_ref='요청에 여러 작업이 함께 포함되어 있습니다. 이상 경보 분석, 보고서 작성, 예측, 작업 등록 중 어떤 작업을 먼저 진행할지 하나만 선택해 주세요.'
        else:
            continue
        if args.skip_sqllm:
            sqllm_ref=''
        else:
            try:
                sqllm_ref=api_chat(args.ollama_url, args.sqllm_model, q, ev, gpt_ref)
            except Exception as exc:
                sqllm_ref=f'[SQ-LLM_REFERENCE_ERROR] {type(exc).__name__}: {str(exc)[:200]}'
        rows.append({
            'id': r['id'],
            'question': q,
            'message': q,
            'expected_route1': r.get('expected_route1'),
            'expected_route2': r.get('expected_route2'),
            'expected_final_action': r.get('expected_final_action'),
            'answer_evidence': ev,
            'reference_answer_gpt55': gpt_ref,
            'reference_answer_sqllm': sqllm_ref,
            'reference_answer': gpt_ref,
            'source_dataset_id': r.get('id'),
            'source_view': r.get('source_view'),
        })
    rows=list({x['id']:x for x in rows}.values())
    out={'schema_version':'anomaly_qa_quality_eval_260617.v2_multi_intent','reference_policy':'reference_answer uses GPT-5.5-style curated operational answer for anomaly rows and deterministic clarification answer for multi_intent rows; reference_answer_sqllm stores sLLM-generated alternative reference','summary':{'row_count':len(rows),'route1_distribution':dict(__import__('collections').Counter(x.get('expected_route1') for x in rows))},'rows':rows}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'out':args.out,'rows':len(rows),'sqllm_model':args.sqllm_model,'sqllm_filled':sum(bool(x['reference_answer_sqllm']) for x in rows)}, ensure_ascii=False, indent=2))
if __name__=='__main__': main()
