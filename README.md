# SKN Final — Router/QA multi_intent Sensitivity Workspace

이 브랜치는 SKN Final EMS Agent의 **two-stage router 평가 및 sLLM 모델별 sensitivity analysis**를 위한 정리된 작업 공간입니다.

현재 기준 source of truth는 `260619`에 재정리한 **multi_intent 포함 300개 router dataset**과 **60개 QA/BERTScore dataset**입니다.

```text
dev/eval/data/router_two_stage_eval_300_260617.json
dev/eval/data/anomaly_qa_quality_eval_260617.json
```

---

## 1. 현재 작업 목적

기존 router 평가 데이터와 legacy 실험 산출물을 정리하고, 아래 기준으로 모델별 라우팅 성능을 비교합니다.

| 구분 | 내용 |
|---|---|
| 평가 대상 | two-stage router classification |
| 기준 dataset | `router_two_stage_eval_300_260617.json` |
| row 수 | 300 |
| Stage 1 | `query`, `action_request`, `approval_required`, `off_topic`, `multi_intent` |
| Stage 2 | `anomaly`, `cms`, `report`, `forecast`, `rag` |
| 출력 위치 | `reports/experiments/router_two_stage_classification/`, `reports/experiments/answer_quality_sensitivity/` |

---

## 2. 최근 정리 내용

### 완료한 작업

1. 기존 `gemma4:12b` / `exaone3.5:7.8b` 결과 동일 여부 확인
2. LLM raw response 저장 기능 추가
3. `kimi-k2` 표준 Ollama pull 실패 확인
4. `qwen3.5` 계열 포함 요청 모델 기준 sensitivity 실행 스크립트 작성
5. 기존 300개 dataset의 중복 query 문제 확인
6. 중복 없는 v2 dataset 생성 및 검증
7. legacy dataset, legacy eval script, generated result, cache 정리
8. 현재 v2 router sensitivity에 필요한 `dev/` 최소 구조만 유지

### 현재 남긴 핵심 파일

```text
dev/eval/build_router_two_stage_dataset_v2_260615.py
dev/eval/data/router_two_stage_eval_300_260617.json
dev/eval/router_two_stage_metrics_260615.py
dev/eval/run_router_sllm_sensitivity_v2_requested_models_260615.sh
```

---

## 3. Dataset 검증 결과

`router_two_stage_eval_300_260617.json` 기준 검증 결과입니다.

| 항목 | 결과 |
|---|---:|
| 전체 row 수 | 300 |
| 중복 message | 0 |
| Stage 1 `query` | 180 |
| Stage 1 `action_request` | 40 |
| Stage 1 `approval_required` | 30 |
| Stage 1 `off_topic` | 30 |
| Stage 1 `multi_intent` | 20 |
| Stage 2 `anomaly` | 36 |
| Stage 2 `cms` | 36 |
| Stage 2 `report` | 36 |
| Stage 2 `forecast` | 36 |
| Stage 2 `rag` | 36 |

---

## 4. 평가 대상 모델

요청 기준 최신 평가 모델은 아래 11개입니다.

```text
llama3.2:3b
llama3.1:8b
qwen3.5:0.8b
qwen3.5:2b
qwen3.5:4b
qwen3.5:9b
exaone3.5:7.8b
gemma4:12b
deepseek-r1:8b
phi4-mini:3.8b
qwen3:8b
```

> 참고: `qwen3.5:9b`는 이전 RunPod/Ollama 환경에서 저장소 quota 문제로 재다운로드가 필요했던 상태입니다. 실행 전 `ollama list`로 설치 여부를 확인하세요.

---

## 5. 실행 방법

### 5.1 Dataset만 재생성

필요 시 v2 dataset을 다시 생성합니다.

```bash
python3 dev/eval/build_router_two_stage_dataset_v2_260615.py
```

생성 파일:

```text
dev/eval/data/router_two_stage_eval_300_260617.json
dev/eval/data/anomaly_qa_quality_eval_260617.json
```

### 5.2 Rule baseline 또는 단일 모델 평가

평가 스크립트는 기본 dataset으로 v2 파일을 사용합니다.

```bash
python3 dev/eval/router_two_stage_metrics_260615.py \
  --mode rule \
  --run-id run_current_260615_v2_rule_baseline_n300
```

Ollama/OpenAI-compatible endpoint를 사용할 경우:

```bash
python3 dev/eval/router_two_stage_metrics_260615.py \
  --mode ollama \
  --ollama-url http://127.0.0.1:11434/v1 \
  --model llama3.2:3b \
  --run-id run_current_260615_v2_sensitivity_llama3_2_3b_n300
```

RunPod 등 원격 Ollama endpoint를 쓰는 경우:

```bash
python3 dev/eval/router_two_stage_metrics_260615.py \
  --mode ollama \
  --ollama-url https://<runpod-endpoint>/v1 \
  --model llama3.2:3b \
  --run-id run_current_260615_v2_sensitivity_llama3_2_3b_n300
```

### 5.3 요청 모델 전체 실행

```bash
OLLAMA_URL=http://127.0.0.1:11434/v1 \
  bash dev/eval/run_router_sllm_sensitivity_v2_requested_models_260615.sh
```

원격 RunPod endpoint 사용 시:

```bash
OLLAMA_URL=https://<runpod-endpoint>/v1 \
  bash dev/eval/run_router_sllm_sensitivity_v2_requested_models_260615.sh
```

---

## 6. 결과 저장 위치

모델별 결과는 아래 경로에 생성됩니다.

```text
reports/experiments/router_two_stage_classification/
```

예상 구조:

```text
reports/
└── experiments/
    └── router_two_stage_classification/
        ├── run_current_260615_v2_sensitivity_llama3_2_3b_n300/
        │   └── metrics.json
        ├── run_current_260615_v2_sensitivity_llama3_1_8b_n300/
        │   └── metrics.json
        ├── run_current_260615_v2_sensitivity_qwen3_5_0_8b_n300/
        │   └── metrics.json
        ├── run_current_260615_v2_sensitivity_qwen3_5_2b_n300/
        │   └── metrics.json
        ├── run_current_260615_v2_sensitivity_qwen3_5_4b_n300/
        │   └── metrics.json
        ├── run_current_260615_v2_sensitivity_qwen3_5_9b_n300/
        │   └── metrics.json
        ├── run_current_260615_v2_sensitivity_exaone3_5_7_8b_n300/
        │   └── metrics.json
        ├── run_current_260615_v2_sensitivity_gemma4_12b_n300/
        │   └── metrics.json
        └── sensitivity_comparison_260615_v2_requested_models_n300.json
```

`reports/`는 생성 산출물이므로 `.gitignore`에 포함되어 있습니다.

---

## 7. Metrics

`metrics.json`에는 아래 항목이 포함됩니다.

| 영역 | 주요 지표 |
|---|---|
| Stage 1 / Route1 | accuracy, macro F1, per-label precision/recall/F1, confusion matrix |
| Stage 2 / Route2 | query row 기준 accuracy, macro F1, confusion matrix |
| Final action | `route:*` / `gate:*` 최종 action accuracy, macro F1 |
| Safety | gate safety recall, leakage error rate, blocking error rate |
| Branch quality | branch dropoff rate |
| LLM trace | raw response, parse status, parse error, fallback 여부 |

Raw response 저장 필드가 추가되어 모델별 응답 원문과 parsing 상태까지 추적할 수 있습니다.

### 7.1 Fallback / metric 산정 기준

- `fallback_count`는 LLM 호출 실패(OOM/timeout/API error), JSON parse 실패, 또는 canonical label이 아닌 값이 들어온 row 수입니다.
- 기존 구현은 fallback 시 rule router 결과를 prediction으로 넣어 scoring했기 때문에 fallback이어도 rule 결과가 gold label과 맞으면 accuracy/F1이 0보다 크게 나올 수 있었습니다.
- 현재 기준은 fallback row를 `__fallback__` sentinel로 채점합니다. `__fallback__`은 gold label에 존재하지 않으므로 300개 전부 fallback이면 route1/route2/final accuracy와 F1은 모두 `0.0`이 됩니다.
- rule router 결과는 `fallback_rule_prediction`에 디버그용으로만 저장하고, metric 계산에는 사용하지 않습니다.
- BERTScore는 생성 실패/빈 답변 row를 평균에 섞지 않고 `bertscore_evaluated_count`, `bertscore_evaluation_unavailable_count`로 분리합니다. 예: 정상 평가 250개, 평가불가 0개면 `bertscore_f1=0.xxx`, `bertscore_evaluated_count=250`, `bertscore_evaluation_unavailable_count=0` 형태로 표기합니다.

---

## 8. 현재 정리 원칙

현재 branch는 v2 router sensitivity에 집중하기 위해 다음을 제거했습니다.

| 제거 대상 | 이유 |
|---|---|
| legacy 260610 / 260612 router dataset | 현재 공식 기준이 v2 300-query dataset이기 때문 |
| 기존 QA/evidence golden dataset | 현재 router sensitivity 단계에서 사용하지 않음 |
| `dev/eval/results/` | 과거 생성 산출물 |
| `dev/docs/`, `dev/scripts/` | 현재 평가 실행에 불필요한 legacy 자료 |
| legacy `test00~test09` runner | 삭제된 legacy eval 파일을 참조하여 broken reference가 되기 때문 |
| `__pycache__`, `*.pyc` | 캐시 |

현재 `dev/`에는 v2 router sensitivity 실행에 필요한 최소 파일만 남깁니다.

---

## 9. Push 전 확인 명령

```bash
git status --short
```

문법 검증:

```bash
python3 -m py_compile \
  dev/eval/build_router_two_stage_dataset_v2_260615.py \
  dev/eval/router_two_stage_metrics_260615.py \
  dev/eval/answer_quality_sensitivity_260617.py

bash -n dev/eval/run_router_sllm_sensitivity_v2_requested_models_260615.sh
```

Dataset row 수 확인:

```bash
python3 - <<'PY'
import json
from collections import Counter
from pathlib import Path

p = Path('dev/eval/data/router_two_stage_eval_300_260617.json')
data = json.loads(p.read_text(encoding='utf-8'))
rows = data['rows'] if isinstance(data, dict) and 'rows' in data else data

print('rows =', len(rows))
print('route1 =', Counter(r['expected_route1'] for r in rows))
print('route2_on_query =', Counter(r['expected_route2'] for r in rows if r['expected_route1'] == 'query'))
print('duplicate_messages =', len(rows) - len({r['message'] for r in rows}))
PY
```

---

## 10. Notes

- 입력 dataset은 `dev/eval/data/`에 둡니다.
- 실행 코드는 `dev/eval/`에 둡니다.
- 모델별 실행 결과는 `reports/experiments/`에 생성합니다.
- `reports/`는 commit 대상이 아니라 재생성 가능한 실험 산출물입니다.
- RunPod는 모델 serving 용도로 사용하고, 코드/데이터셋/결과 관리는 이 workspace에서 수행합니다.

---

## 6. 최신 260619 결과 요약

최신 HTML 리포트:

```text
docs/experiment_metrics_260619.html
```

최신 comparison JSON:

```text
reports/experiments/router_two_stage_classification/multi_intent_260619_router_schema_hardened_qa_bert_comparison.json
```

핵심 결과:

| 구분 | 1순위 | 핵심 지표 |
|---|---|---:|
| Router Final Accuracy | `gemma4:12b` | 0.6167 |
| QA Composite | `gemma4:12b` | 0.6357 |
| QA Numeric F1 | `qwen3.5:9b` | 0.7754 |
| BERTScore F1 | `gemma4:12b` | 0.8865 |

주의:

- `deepseek-r1:8b`는 Router 300건 모두 fallback/parse error이므로 Router 후보에서 제외합니다.
- BERTScore는 정상 답변 row만 평균 계산하고, 평가불가는 `bertscore_evaluation_unavailable_count`로 분리합니다.
- `multi_intent`는 복합 작업 요청을 바로 실행하지 않고 `gate:multi_intent`로 분기해 사용자에게 작업 분리를 요청하는 안전 gate입니다.
