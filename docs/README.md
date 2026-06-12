# docs

모델 운영과 의사결정 근거를 정리하는 문서 폴더입니다.

## 하위 폴더

| 폴더 | 역할 |
|------|------|
| `04_model_strategy/` | 모델 운영 명령어, RunPod 재학습 구조, 모델 전략 노트 |

## 주요 문서

| 파일 | 역할 |
|------|------|
| `04_model_strategy/MODEL_OPERATION_COMMANDS.md` | 학습, 추론, API, 검증, 승격 명령어 흐름 |
| `04_model_strategy/MODEL_IO_SPEC.md` | 모델 입력/출력 컬럼과 운영 결과 스키마 |
| `04_model_strategy/INFERENCE_STACK.md` | 추론 실행 구조, artifact, RunPod/AWS 연동 메모 |
| `04_model_strategy/RUNPOD_SERVERLESS_RETRAINING.md` | RunPod serverless 재학습 구조와 운영 절차 |
| `04_model_strategy/MODEL_STRATEGY_NOTES.md` | 모델 선택과 threshold 관련 전략 메모 |

## 주의

- 실험 리포트 HTML이나 대용량 결과물은 이 운영 repo에 포함하지 않습니다.
- 운영 절차가 바뀌면 README보다 `MODEL_OPERATION_COMMANDS.md`를 먼저 갱신합니다.
