# app/ems-agent 평가 산출물 표준화 — 260612

## 방향

`app/ems-agent` branch의 실제 backend/sLLM/RunPod 구조는 유지하고, 기존 `dev/eval/*.py`가 직접 공통 metrics schema를 출력하도록 수정했다. 별도 `adapters/` 폴더를 만들지 않고 `uy/workspace`의 main idea만 흡수했다.

## 공통 산출물

모든 신규 평가 결과는 아래 위치를 기본값으로 사용한다.

```text
reports/experiments/<test_id>/run_*/
├── metrics.json
└── report.md
```

`metrics.json` schema:

```text
schema_version = experiment-metrics.v1
```

공통 helper:

```text
dev/eval/common_metrics.py
```

## 수정된 runner

| 파일 | 현재 매핑 | 비고 |
|---|---|---|
| `dev/eval/router_accuracy_eval.py` | `test09_router_5route_eval` | 500문항 router benchmark, LLM fallback 옵션 유지 |
| `dev/eval/harness.py` | `test07_llm_answer_gate` | sLLM 직접 호출 + judge 평가 |
| `dev/eval/system_eval.py` | `test07_llm_answer_gate`, `metric_family=system_answer_quality` | backend `/chat` end-to-end 평가 |
| `dev/eval/test06_router_5route_eval.py` | legacy wrapper | 현재 번호 체계상 test09 runner로 위임 |
| `dev/eval/compare_results.py` | comparison | `reports/experiments/*/run_*/metrics.json` 집계 |
| `scripts/runpod_eval_test00_to_test09.sh` | RunPod 통합 실행 | router/harness/system subset 실행 |

## 실행 예시

```bash
# router rule baseline: LLM/API key 불필요
python3 dev/eval/router_accuracy_eval.py

# router LLM fallback: Ollama 필요
python3 dev/eval/router_accuracy_eval.py --llm

# sLLM answer gate: Ollama + OPENAI_API_KEY 필요
export LLM_PROVIDER=ollama
export OLLAMA_URL=http://localhost:11434/v1
export LLM_MODEL=gemma4:12b
export LLM_MODEL_FAST=exaone3.5:7.8b
export OPENAI_API_KEY=...
python3 dev/eval/harness.py --quality

# 비교표 생성
python3 dev/eval/compare_results.py
```

RunPod one-shot:

```bash
bash scripts/runpod_eval_test00_to_test09.sh
```

## 원칙

- 평가 계산 로직은 기존 파일에 남긴다.
- 저장 구조만 `experiment-metrics.v1` envelope로 통일한다.
- raw/per-row 결과는 `metrics.json.details` 아래에 보존한다.
- 기존 `dev/docs/sllm/*`, `backend/src/agents/*` 구조는 변경하지 않는다.
