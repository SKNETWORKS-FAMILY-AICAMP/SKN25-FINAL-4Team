# SKN25 Energy Anomaly Model Service

전기/열량 계량기의 1시간 단위 시계열 데이터를 기반으로, 미래 3시간의 P 값을 예측하고 정상 운영 범위를 벗어날 가능성을 사전에 경고하는 모델 운영 패키지입니다.

이 저장소는 실험 코드 전체가 아니라 서비스 운영에 필요한 코드와 문서만 정리한 배포용 구조입니다. EDA, 오래된 실험 코드, 학습 산출물, full-year 테스트 결과, candidate/archive 백업 산출물은 포함하지 않습니다.

## 프로젝트 목적

현장 계량기 데이터는 단순히 현재값이 비정상인지 보는 것만으로는 이상 징후를 빠르게 잡기 어렵습니다. 이 프로젝트는 과거 입력 데이터를 바탕으로 `t+1`, `t+2`, `t+3` 시점의 P 값을 예측하고, 예측값이 validation 기간의 시간대별 정상 P 범위를 벗어나는지 판단합니다.

운영 목표는 다음과 같습니다.

- 설비 이상 가능성을 실제 발생 전에 사전 경고합니다.
- 물리적으로 불가능한 입력값은 먼저 태그하고 보정해 모델 입력 품질을 안정화합니다.
- 모델 경보와 입력 품질 문제를 함께 남겨 운영자가 경보 원인을 빠르게 확인할 수 있게 합니다.
- 재학습 결과를 바로 운영에 반영하지 않고 candidate 검증 후 active로 승격합니다.

## 기대 효과

- 단순 threshold 감시보다 시간대별 운영 패턴을 반영한 경보가 가능합니다.
- `t+1~t+3` trajectory를 함께 저장해 급격히 커지는 이상 징후를 확인할 수 있습니다.
- 입력 물리 이상, known meter issue, 모델 경보 reason code가 함께 남아 사후 분석이 쉬워집니다.
- RunPod serverless를 통해 무거운 재학습은 GPU 환경에서 실행하고, 운영 서버는 추론/API 중심으로 가볍게 유지할 수 있습니다.

## 차별성

- P 직접 예측이 아니라 residual target 기반으로 학습해 persistence 흉내를 줄이고 패턴 학습 신호를 강화했습니다.
- 사전 경보 threshold는 validation actual P의 시간대별 2~98 percentile 기반이며, near-zero lower bound 계량기의 음수 예측 소음을 줄이기 위해 floor=-50W를 적용했습니다.
- 모델 산출물은 `candidate -> validate -> promote -> active` 흐름으로 관리해 재학습 실패가 운영 모델을 바로 덮어쓰지 않게 했습니다.
- RunPod 재학습 결과를 API로 업로드받아 로컬/서버 환경에서도 동일한 승격 흐름을 사용할 수 있습니다.

## 폴더 구조

```text
api/                 FastAPI 운영 API
config/              계량기 메타데이터
src/energy_v84/      v84 residual 모델 학습/추론 패키지
scripts/             candidate 검증/승격 및 배치 실행 스크립트
runpod_job/          RunPod Serverless 학습 handler
runpod_smoke/        RunPod 인프라 원인분리용 smoke handler
artifacts/           모델 산출물과 운영 결과 저장 위치. git에는 README/.gitkeep만 포함
outputs/             기타 리포트/임시 산출물 저장 위치
docs/                운영/모델 전략 문서
```

각 폴더의 상세 설명은 해당 폴더의 `README.md`를 확인합니다.

## 기본 실행 환경

```bash
conda activate skn25
cd /home/sms/openclaw_file/skn25_final
set -a
source .env
set +a
```

## API 실행

```bash
PYTHONPATH=src:. uvicorn api.main:app --host 127.0.0.1 --port 8000
```

상태 확인:

```bash
curl "http://127.0.0.1:8000/"
```

## 추론

현재 UTC 기준 3h 추론:

```bash
PYTHONPATH=src:. python -m energy_v84.inference --horizon 3
```

특정 timestamp 기준 추론:

```bash
PYTHONPATH=src:. python -m energy_v84.inference \
  --horizon 3 \
  --timestamp "2023-06-01T09:00:00+00:00"
```

## 로컬 학습

전체 3h candidate 학습:

```bash
PYTHONPATH=src:. python -m energy_v84.train --horizon 3
```

연결 확인용 단일 계량기 학습:

```bash
PYTHONPATH=src:. python -m energy_v84.train \
  --horizon 3 \
  --meters H1.Z10 \
  --epochs 1
```

## RunPod 재학습 시작

```bash
curl -X POST "http://127.0.0.1:8000/training/start" \
  -H "Authorization: Bearer ${ARTIFACT_UPLOAD_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{"horizon":3}'
```

상태 확인:

```bash
curl "http://127.0.0.1:8000/training/latest" \
  -H "Authorization: Bearer ${ARTIFACT_UPLOAD_TOKEN}"
```

## Candidate 검증/승격

```bash
PYTHONPATH=src:. python scripts/validate_candidate.py --run <run_id> --horizon 3
PYTHONPATH=src:. python scripts/promote_candidate.py --run <run_id> --horizon 3 --yes
```

자세한 명령어 순서는 `docs/04_model_strategy/MODEL_OPERATION_COMMANDS.md`를 확인합니다. 모델 입출력 명세는 `docs/04_model_strategy/MODEL_IO_SPEC.md`, 추론 스택 메모는 `docs/04_model_strategy/INFERENCE_STACK.md`를 확인합니다.

## 주의

- `.env`는 커밋하지 않습니다.
- git에는 모델 weight/scaler/routing 같은 학습 산출물을 포함하지 않습니다. 학습 없이 바로 추론하려면 `artifacts/3h`, `artifacts/thresholds`, `train_summary_3h.csv`를 별도 전달받아 배치해야 합니다.
- `artifacts/candidate`, `artifacts/archive`, `artifacts/inference_results`는 운영 중 생성되는 산출물 영역입니다.
- RunPod에서 학습하려면 RunPod endpoint 환경변수에 DB 접속 정보와 artifact upload URL/token이 필요합니다.
