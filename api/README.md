# API Folder Guide

이 폴더는 서비스 백엔드 API 진입점입니다. 현재는 계량기 메타데이터 조회, RunPod 재학습 실행, 모델 artifact 업로드, candidate 검증, 운영 승격 흐름을 제공합니다.

## 현재 역할

- RunPod 또는 외부 학습 환경에서 생성한 candidate artifact를 업로드합니다.
- RunPod Serverless 재학습 job을 시작하고 상태를 조회합니다.
- 업로드된 candidate를 검증 스크립트로 확인합니다.
- 검증된 candidate를 active artifact로 승격합니다.
- 기존 구형 `anomaly`, `predict`, `upload/csv` API는 현재 모델 운영 흐름과 맞지 않아 제거했습니다.

## 실행

```bash
PYTHONPATH=src:. uvicorn api.main:app --host 127.0.0.1 --port 8000
```

필수 환경변수:

```bash
ARTIFACT_UPLOAD_TOKEN=your-token
```

선택 환경변수:

```bash
MODEL_ARTIFACTS_DIR=/path/to/artifacts
MODEL_SCRIPT_PYTHON=/path/to/python
MODEL_VALIDATE_TIMEOUT_SECONDS=300
MODEL_PROMOTE_TIMEOUT_SECONDS=1800
RUNPOD_ENDPOINT_ID=your-runpod-endpoint-id
RUNPOD_API_KEY=your-runpod-api-key
RUNPOD_API_TIMEOUT_SECONDS=60
RUNPOD_ARTIFACT_UPLOAD_URL=https://your-api-host/model-artifacts/upload
CORS_ALLOWED_ORIGINS=*
MAX_ARTIFACT_UPLOAD_MB=2048
PROBE_MAX_UPLOAD_MB=1
KEEP_UPLOADED_ARCHIVES=0
```

`MODEL_SCRIPT_PYTHON`을 지정하지 않으면 FastAPI 서버를 실행한 Python(`sys.executable`)으로 `validate_candidate.py`, `promote_candidate.py`를 실행합니다. conda 환경을 명시하려면 아래처럼 둘 수 있습니다.

```bash
MODEL_SCRIPT_PYTHON="conda run -n skn25 python"
```

`CORS_ALLOWED_ORIGINS`는 쉼표로 여러 origin을 지정할 수 있습니다.

```bash
CORS_ALLOWED_ORIGINS=https://example.com,https://another.example.com
```

## 파일 구조

```text
api/
  main.py
  routers/
    meters.py
    model_artifacts.py
    model_auth.py
    model_paths.py
    model_runs.py
    model_training.py
```

## 파일별 역할

### `main.py`

FastAPI 앱 생성 파일입니다.

- CORS 설정
- 라우터 등록
- `/` health check 제공

현재 등록된 주요 라우터:

- `/meters`
- `/model-artifacts`
- `/model-runs`
- `/training`

### `routers/meters.py`

계량기 메타데이터 조회 API입니다.

주요 endpoint:

- `GET /meters`
- `GET /meters/types`
- `GET /meters/{meter_urn}`

`config.meter_metadata`의 계량기 정의를 읽어 반환합니다.

### `routers/model_auth.py`

모델 운영 API용 인증/입력 검증 공통 함수입니다.

역할:

- `.env` 로드
- `ARTIFACT_UPLOAD_TOKEN` 기반 Bearer token 검증
- `run_id` 형식 검증

인증 방식:

```http
Authorization: Bearer <ARTIFACT_UPLOAD_TOKEN>
```

### `routers/model_paths.py`

모델 artifact 저장 위치를 한 곳에서 관리합니다.

기본 위치:

```text
artifacts
```

환경변수 `MODEL_ARTIFACTS_DIR`를 지정하면 다른 위치를 사용할 수 있습니다.

내부 경로:

- `ARTIFACTS_DIR`: 전체 artifact root
- `CANDIDATE_DIR`: 업로드된 candidate 저장 위치
- `INCOMING_DIR`: 업로드 중 임시 archive 저장 위치
- `TRAINING_JOBS_DIR`: RunPod 재학습 job 상태 JSON 저장 위치

### `routers/model_artifacts.py`

RunPod 또는 외부 학습 환경에서 생성한 candidate artifact archive를 업로드하는 API입니다.

주요 endpoint:

- `POST /model-artifacts/probe`
- `POST /model-artifacts/upload`

`/probe`:

- cloudflared/RunPod/EC2 연결 확인용 작은 파일 업로드 테스트입니다.
- 기본 최대 크기는 1MB입니다.
- 업로드된 probe 파일은 응답 후 삭제됩니다.

`/upload`:

- `.tar`, `.tar.gz`, `.tgz`, `.zip` archive를 받습니다.
- archive 내부에서 candidate 구조를 찾아 `candidate/{run_id}`로 복사합니다.
- path traversal, symlink, hardlink, special file을 차단합니다.
- 같은 `run_id`가 있으면 기본적으로 거부하고, `overwrite=true`일 때만 교체합니다.

지원 archive 구조:

```text
{run_id}/{horizon}h/...
{run_id}/train_summary_{horizon}h.csv
```

또는:

```text
candidate/{run_id}/{horizon}h/...
candidate/{run_id}/train_summary_{horizon}h.csv
```

또는 archive root에 바로:

```text
{horizon}h/...
train_summary_{horizon}h.csv
```

### `routers/model_runs.py`

업로드된 candidate run의 상태 조회, 검증, 승격 API입니다.

주요 endpoint:

- `GET /model-runs/{run_id}`
- `POST /model-runs/{run_id}/validate`
- `POST /model-runs/{run_id}/promote`

`GET /model-runs/{run_id}`:

- candidate 존재 여부
- horizon 폴더 존재 여부
- 계량기 폴더 수
- train summary 미리보기
- `validated.marker` 정보

`POST /model-runs/{run_id}/validate`:

- `scripts/validate_candidate.py`를 실행합니다.
- exit code `0`: 통과
- exit code `2`: 경고, 수동 확인 후 승격 가능
- exit code `1`: 실패

`POST /model-runs/{run_id}/promote`:

- `validated.marker`가 있어야 실행됩니다.
- `confirm=true`가 필요합니다.
- marker 결과가 `warn`이면 `allow_warn=true`도 필요합니다.
- 내부적으로 `scripts/promote_candidate.py --yes`를 실행합니다.

### `routers/model_training.py`

백엔드가 RunPod Serverless API를 호출해 재학습 job을 시작하고 상태를 조회하는 API입니다.

주요 endpoint:

- `POST /training/start`
- `GET /training/{job_id}/status`
- `GET /training/latest`

필수 환경변수:

```bash
RUNPOD_ENDPOINT_ID=...
RUNPOD_API_KEY=...
```

선택 환경변수:

```bash
RUNPOD_API_TIMEOUT_SECONDS=60
RUNPOD_ARTIFACT_UPLOAD_URL=https://your-api-host/model-artifacts/upload
```

`RUNPOD_ARTIFACT_UPLOAD_URL`이 없으면 기존 RunPod worker 환경변수와 같은 이름인 `MODEL_ARTIFACT_UPLOAD_URL`을 fallback으로 사용합니다.

`POST /training/start`:

- 백엔드가 RunPod API `/run`을 호출합니다.
- `run_id`를 생략하면 백엔드가 `run_YYYYMMDDTHHMMSSZ_xxxxxxxx` 형식으로 생성합니다.
- RunPod job id와 run id를 `training_jobs/{job_id}.json`에 저장합니다.
- `meters`, `groups`, `epochs`, `batch_size`, `seed`를 선택적으로 전달할 수 있습니다.

예시:

```bash
curl -X POST "http://127.0.0.1:8000/training/start" \
  -H "Authorization: Bearer <ARTIFACT_UPLOAD_TOKEN>" \
  -H "Content-Type: application/json" \
  --data '{"horizon":3,"meters":["H1.Z10"],"epochs":1}'
```

전체 3h 재학습은 smoke 옵션을 빼고 실행합니다.

```bash
curl -X POST "http://127.0.0.1:8000/training/start" \
  -H "Authorization: Bearer <ARTIFACT_UPLOAD_TOKEN>" \
  -H "Content-Type: application/json" \
  --data '{"horizon":3}'
```

`GET /training/{job_id}/status`:

- 백엔드가 RunPod API `/status/{job_id}`를 호출합니다.
- 응답을 `training_jobs/{job_id}.json`에 갱신합니다.
- RunPod output에 upload 결과가 있으면 함께 저장합니다.
- artifact 업로드가 완료되면 `next_action`이 `validate`로 표시됩니다.

`GET /training/latest`:

- 가장 최근에 시작한 training job 기록을 반환합니다.
- LLM 또는 운영자가 "방금 재학습 상태"를 확인할 때 사용합니다.

## 운영 흐름

1. `/training/start`로 RunPod 재학습을 시작합니다.
2. RunPod worker가 재학습 결과를 archive로 묶습니다.
3. RunPod worker가 `/model-artifacts/upload`로 candidate를 업로드합니다.
4. `/training/{job_id}/status` 또는 `/training/latest`로 완료 상태를 확인합니다.
5. `next_action=validate`이면 `/model-runs/{run_id}`로 candidate 존재와 계량기 수를 확인합니다.
6. `/model-runs/{run_id}/validate`로 검증합니다.
7. 검증 결과를 사람이 확인합니다.
8. 문제가 없으면 `/model-runs/{run_id}/promote`로 운영 artifact에 승격합니다.

## 주의사항

- 현재 validate/promote API는 subprocess를 동기 실행합니다. 로컬 테스트와 단일 운영자 사용에는 충분하지만, 다중 사용자 운영에서는 background job 구조가 더 적합합니다.
- `stdout`, `stderr`는 디버깅을 위해 응답에 포함됩니다. 외부 서비스 공개 전에는 서버 로그로만 남기고 응답에서는 줄이는 것이 안전합니다.
- 현재 API는 서버 노트북/EC2에서 직접 GPU 학습을 수행하지 않습니다. `/training/start`는 RunPod API를 호출하는 오케스트레이션 역할이고, 실제 학습은 RunPod worker에서 수행합니다.
