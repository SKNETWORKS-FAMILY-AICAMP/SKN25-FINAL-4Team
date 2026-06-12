# RunPod Serverless 재학습 연동 절차

이 문서는 `energy_v84` 잔차 예측 기반 v84 모델을 RunPod Serverless에서 재학습하고, 생성된 candidate artifact를 API 서버로 업로드하는 절차를 정리한다.

실제 운영 명령어 순서는 `MODEL_OPERATION_COMMANDS.md`를 함께 확인한다.

## 목적

서비스 운영에서는 AWS/백엔드 또는 LLM 에이전트가 재학습을 요청하고, RunPod에서 GPU 학습을 수행한 뒤 결과 artifact를 운영 API로 되돌려 받아야 한다.

목표 흐름:

```text
사용자/LLM/백엔드
  -> RunPod Serverless /run 요청
  -> RunPod worker에서 train.py 실행
  -> candidate artifact 압축
  -> API 서버 /model-artifacts/upload 업로드
  -> validate_candidate.py 검증
  -> 사용자 승인 후 promote_candidate.py 승격
```

## 현재 성공 확인 결과

2026-06-11 기준으로 RunPod Serverless에서 단일 계량기 및 3개 계량기 smoke 재학습이 성공했다.

첫 번째 확인 요청:

```json
{
  "input": {
    "horizon": 3,
    "meters": ["H1.Z10"],
    "epochs": 1
  }
}
```

성공 결과:

```text
RunPod status: COMPLETED
handler output.status: uploaded
run_id: run_20260611T070003Z_87b1f1cd
meter_count: 1
```

추가 확인 요청:

```json
{
  "input": {
    "horizon": 3,
    "meters": ["H1.Z10", "H2.Z63", "H1.K11"],
    "epochs": 1
  }
}
```

성공 결과:

```text
endpoint: energy-v84-train
RunPod status: COMPLETED
handler output.status: uploaded
run_id: run_20260611T071309Z_ec013a19
meter_count: 3
```

확인된 범위:

- RunPod Serverless endpoint 호출 성공
- Docker Hub trainer image pull 성공
- `runpod_job.handler` 실행 성공
- `energy_v84.train` 3h/H1.Z10/H2.Z63/H1.K11/1 epoch 학습 성공
- candidate tar 생성 성공
- cloudflared를 통한 로컬 FastAPI 업로드 성공
- 로컬 `artifacts/candidate/<run_id>/` 저장 성공

## Docker Images

현재 테스트에 사용한 이미지:

```text
anstn3375/energy-v84-trainer:latest
```

원인 분리용 smoke 이미지:

```text
anstn3375/runpod-smoke:latest
```

운영에서는 `latest` 대신 날짜나 버전 기반 고정 태그를 권장한다.

예:

```text
anstn3375/energy-v84-trainer:20260611
```

## RunPod Endpoint 설정

성공한 테스트에서는 낮은 GPU pool에서 image pull/provisioning이 오래 멈췄고, GPU/worker pool을 변경한 뒤 smoke image와 trainer image가 정상 실행됐다.

권장 설정:

```text
Container image: anstn3375/energy-v84-trainer:latest
Registry credential: None
Container disk: 100GB
Execution timeout: 3600 이상
Max workers: 1
Active workers: 테스트 중 1, 테스트 후 0
Expose HTTP ports: 비움
Expose TCP ports: 비움
```

전체 51개 재학습을 돌릴 때는 `Execution timeout`을 `86400`으로 늘리는 것을 권장한다.

주의:

- `Active workers`는 worker 생성 가능 여부가 아니라 항상 running 상태로 유지할 warm worker 수에 가깝다.
- `Active workers=0`이어도 endpoint가 ready 상태이고 필요 시 worker 준비/생성이 가능하다.
- `Active workers=1`이면 요청이 없어도 worker 1개를 계속 running 상태로 유지할 수 있어 비용이 발생할 수 있다.
- UI 상단이 `$0.00000/s`, `0 running workers`, `0 jobs in progress`, `0 jobs waiting in queue`이면 비용이 나가지 않는 상태로 판단한다.
- 테스트 종료 후 반드시 `Active workers=0`으로 되돌린다.
- 낮은 GPU/worker pool에서는 `image pull ... pending` 또는 `Waiting` 상태가 오래 지속될 수 있다.

## Environment Variables

RunPod trainer endpoint에는 아래 환경변수가 필요하다.

```env
MODEL_ARTIFACT_UPLOAD_URL=https://<api-host>/model-artifacts/upload
ARTIFACT_UPLOAD_TOKEN=<upload-token>
RUNPOD_ALLOWED_UPLOAD_HOSTS=<api-host>
RUNPOD_TRAIN_TIMEOUT_SECONDS=86400
RUNPOD_UPLOAD_RETRIES=3
DB_HOST=...
DB_PORT=...
DB_NAME=...
DB_USER=...
DB_PASS=...
```

로컬 cloudflared 테스트 예시:

```env
MODEL_ARTIFACT_UPLOAD_URL=https://brand-prices-flavor-fix.trycloudflare.com/model-artifacts/upload
ARTIFACT_UPLOAD_TOKEN=test-skn25
RUNPOD_ALLOWED_UPLOAD_HOSTS=brand-prices-flavor-fix.trycloudflare.com
```

FastAPI 수신 서버는 업로드 인증에 `ARTIFACT_UPLOAD_TOKEN`을 사용한다.

```bash
export ARTIFACT_UPLOAD_TOKEN="test-skn25"
PYTHONPATH=src:. uvicorn api.main:app --host 127.0.0.1 --port 8000
```

`MODEL_ARTIFACT_UPLOAD_URL`은 보내는 쪽인 RunPod worker에 필요한 값이고, FastAPI 서버 자체에는 필요하지 않다.

## API 호출 절차

로컬 또는 AWS 백엔드에서 RunPod API key와 endpoint ID를 준비한다.

```bash
export RUNPOD_API_KEY="..."
export ENDPOINT_ID="..."
```

주의: API key 값에 줄바꿈이나 공백이 섞이면 `Authorization` 헤더가 깨져 `400 Bad Request`가 발생할 수 있다.

정리:

```bash
export RUNPOD_API_KEY="$(printf '%s' "$RUNPOD_API_KEY" | tr -d '\r\n ')"
```

재학습 요청:

```bash
curl --http1.1 -X POST "https://api.runpod.ai/v2/${ENDPOINT_ID}/run" \
  -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
  -H "Content-Type: application/json" \
  --data-raw '{"input":{"horizon":3,"meters":["H1.Z10"],"epochs":1}}'
```

상태 확인:

```bash
curl --http1.1 "https://api.runpod.ai/v2/${ENDPOINT_ID}/status/<job_id>" \
  -H "Authorization: Bearer ${RUNPOD_API_KEY}"
```

성공 조건:

```text
RunPod status = COMPLETED
output.status = uploaded
output.upload.status = uploaded
```

## 문제 해결 기록

이번 연동 중 확인한 주요 문제:

| 증상 | 원인 | 조치 |
|---|---|---|
| `image pull ... pending` 장시간 반복 | 낮은 GPU/worker pool에서 worker provisioning 또는 image pull 지연 | GPU/worker pool 변경 |
| `manifest unknown` | Docker Hub repo/tag 불일치 | 실제 namespace/tag로 다시 push |
| `pull access denied` | image 주소 또는 registry 권한 오류 | Docker Hub public image로 원인 분리 |
| `400 unexpected end of JSON input` | `RUNPOD_API_KEY`에 줄바꿈이 섞여 Authorization header가 깨짐 | API key에서 `\\r\\n` 제거 |
| `ARTIFACT_UPLOAD_TOKEN is not configured` | FastAPI 수신 서버가 `ARTIFACT_UPLOAD_TOKEN` 없이 실행됨 | uvicorn 실행 전 `ARTIFACT_UPLOAD_TOKEN` export |

## Endpoint 선택

현재는 기존 `energy-v84-train` endpoint를 성공한 GPU/worker 설정으로 맞춘 뒤 운영 후보로 유지하는 것이 낫다.

이유:

- `energy-v84-train` endpoint에서 trainer image와 3개 계량기 재학습이 성공했다.
- endpoint 이름이 재학습 목적과 맞아 AWS/백엔드 연동 시 의미가 명확하다.
- 실패 원인은 endpoint 자체보다 낮은 GPU/worker pool의 image pull/provisioning 문제로 좁혀졌다.

성공한 GPU/worker 설정은 유지한다. 낮은 GPU/worker pool로 되돌리면 `image pull ... pending` 문제가 재발할 수 있다.

## 다음 작업

- AWS 백엔드에서 `/run` 호출과 `/status` polling 구현
- 완료 후 `validate_candidate.py` 실행 API 연결
- 사용자 승인 후 `promote_candidate.py` 실행 API 연결
- 전체 3h 재학습 테스트 시 timeout과 비용 관리 확인
- Docker image tag를 `latest`에서 고정 버전 태그로 전환
