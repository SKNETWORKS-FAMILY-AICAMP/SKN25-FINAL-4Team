# docs/04_model_strategy

모델 학습, 추론, 재학습, 검증/승격 운영 전략을 정리하는 문서 폴더입니다.

## 문서 목록

| 파일 | 내용 |
|------|------|
| `MODEL_OPERATION_COMMANDS.md` | 학습, 추론, API 재학습, candidate 검증, active 승격 명령어 순서 |
| `MODEL_IO_SPEC.md` | DB/백엔드/프론트 연동에 필요한 모델 입력/출력 컬럼 명세 |
| `INFERENCE_STACK.md` | 추론 실행 구조, artifact 구성, 사양 측정, RunPod/AWS 연동 메모 |
| `RUNPOD_SERVERLESS_RETRAINING.md` | RunPod Serverless 기반 재학습, artifact 업로드, 검증/승격 연동 절차 |
| `MODEL_STRATEGY_NOTES.md` | 모델 설계 방향, residual 예측, threshold 전략 메모 |

## 주의

- 이 운영 repo에는 실험 HTML 리포트와 EDA 결과물을 포함하지 않습니다.
- 명령어가 바뀌면 `MODEL_OPERATION_COMMANDS.md`를 우선 갱신합니다.
- API/DB/프론트 연동 컬럼이 바뀌면 `MODEL_IO_SPEC.md`를 같이 갱신합니다.
