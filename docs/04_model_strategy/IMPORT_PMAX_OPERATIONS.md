# Import P-Max Model Operations

## 목표 구조

Import P-Max는 기존 예측 모델과 전처리/추론 코드를 유지하고, 모델 운영 흐름만
다음 구조로 관리한다.

```text
LLM 또는 운영자
  -> 로컬 FastAPI
  -> RunPod Serverless GPU 재학습
  -> Cloudflare를 통해 candidate artifact를 로컬로 업로드
  -> 로컬 검증
  -> 기존 운영 artifact 백업
  -> candidate 승격
  -> 운영 경로로 4개 논리 계량기 inference smoke
  -> 성공 시 candidate 삭제 / 실패 시 기존 운영본 자동 복구
  -> 필요 시 백업본 롤백
```

추론은 계속 로컬에서 수행한다. RunPod는 재학습과 candidate 생성만 담당하며,
검증, 백업, 승격, 롤백은 로컬 백엔드가 담당한다. LLM 코드는 아직 없지만,
향후 LLM 도구가 FastAPI endpoint를 순서대로 호출할 수 있도록 작업 단계를
분리했다.

DB는 학습 데이터 조회 전용이다. 이 운영 흐름에는 DB INSERT, UPDATE, DELETE,
DDL이 없으며, Python DB 엔진도
`default_transaction_read_only=on` 세션으로 연결한다. RunPod에는 별도로
SELECT 권한만 가진 DB 계정을 설정하는 것을 원칙으로 한다.

## Artifact 디렉터리

```text
artifacts/
  import_pmax_v29_60min/        # 현재 운영 추론 artifact
  import_pmax_candidates/       # run_id별 재학습 candidate
  import_pmax_archives/         # 승격 전 운영본과 롤백 교체본
  import_pmax_incoming/         # 업로드 임시 파일
  import_pmax_training_jobs/    # 로컬 RunPod job 상태
```

재학습 기본 경로는 고정 candidate 폴더가 아니라
`artifacts/import_pmax_candidates/{run_id}`이다. 서로 다른 재학습 결과가
덮어써지지 않는다. 검증 전후에는 candidate의 `validation.json`으로 상태를
추적하고, 승격 성공 후에는 운영 경로의 `promotion.json`을 사용한다. smoke
실패 시 candidate를 보존하고 `promotion_failure.json`을 기록한다.

## 로컬 CLI 검증

API나 RunPod 연결 전에도 동일한 Python 도메인 로직을 직접 검증할 수 있다.

실제 재학습:

```bash
python -m scripts.forecasting.train_import_pmax \
  --device gpu \
  --run-id local_20260611
```

이 명령은 DB를 읽어 4개 논리 계량기 각각에 대해 LightGBM 2개, XGBoost 1개,
CatBoost 1개를 학습하고 candidate를 생성한다.

운영 경로를 변경하지 않는 candidate 검증:

```bash
python -m scripts.forecasting.promote_import_pmax \
  --run-id local_20260611
```

승격:

```bash
python -m scripts.forecasting.promote_import_pmax \
  --run-id local_20260611 \
  --execute \
  --approval-note "approved after local validation"
```

롤백할 archive를 확인한 뒤 실행:

```bash
python -m scripts.forecasting.rollback_import_pmax \
  --archive-root artifacts/import_pmax_archives/<archive_name> \
  --approval-note "rollback after production review"
```

승격은 반드시 먼저 생성된 `validation.json`을 요구한다. 검증 후 artifact가
바뀌면 SHA-256 digest가 달라져 승격이 거부된다.

## 검증 및 승격 규칙

검증 단계는 다음 항목을 확인한다.

1. 4개 논리 계량기 artifact가 정확히 존재하는지 확인한다.
2. 계량기마다 4개 ensemble 모델, manifest, weight 파일을 실제로 로드한다.
3. 현재 운영 feature 순서와 22개 feature 구성이 일치하는지 확인한다.
4. 학습 summary가 4개 계량기와 필수 평가 지표를 모두 포함하는지 확인한다.
5. 운영 `deployment_metrics.json`이 있으면 계량기별 RMSE를 비교한다.
6. candidate runtime 파일과 summary의 digest를 기록한다.

기본 RMSE 악화 허용치는 5%이다. 이를 넘으면 검증 결과는 `warn`이며,
승격 시 `--allow-warn` 또는 API의 `allow_warn=true`가 명시되어야 한다.
기존 운영 artifact에 `deployment_metrics.json`이 없는 최초 1회 승격은
구조 검증만 수행하고, 해당 승격 이후부터 이전 운영 RMSE 비교가 가능하다.

승격 시 candidate runtime 파일을 staging에 복사하고 다시 로드한다. 기존
운영본은 archive에 검증된 백업본으로 복사한 뒤, 운영 경로와 같은
파일시스템에서 staging을 교체한다. 교체 직후 운영 추론과 동일한 코드로 4개
논리 계량기, 총 16개 예측을 실행한다. 출력 파일이나 DB에는 쓰지 않는다.

inference smoke가 실패하면 새 운영본을 제거하고 교체 전 운영 폴더를 자동
복구한다. 이 경우 archive와 candidate는 모두 남는다. smoke가 성공하면
candidate를 삭제하고, 결과는 운영 `promotion.json`에 기록한다.

롤백도 archive를 staging에서 검증한 뒤 수행한다. 현재 운영본은
`*_rollback_replaced_*` archive로 보존하므로 롤백 자체도 되돌릴 수 있다.

## FastAPI 실행

필수 로컬 환경 변수:

```bash
export ARTIFACT_UPLOAD_TOKEN='<long-random-token>'
export RUNPOD_ENDPOINT_ID='<runpod-endpoint-id>'
export RUNPOD_API_KEY='<runpod-api-key>'
export RUNPOD_ARTIFACT_UPLOAD_URL='https://<cloudflare-host>/model-artifacts/upload'
```

실행:

```bash
PYTHONPATH=. uvicorn api.main:app --host 0.0.0.0 --port 8000
```

모델 운영 endpoint는 모두
`Authorization: Bearer ${ARTIFACT_UPLOAD_TOKEN}`을 요구한다.

주요 호출 순서:

```text
POST /training/start
GET  /training/{job_id}/status
GET  /model-runs/{run_id}
POST /model-runs/{run_id}/validate
POST /model-runs/{run_id}/promote?confirm=true
POST /model-runs/rollback?archive_name=...&confirm=true
```

`/training/start`는 RunPod `/run`을 실제 호출하고, status endpoint는 RunPod
상태를 조회해 로컬 job 기록을 갱신한다. RunPod 업로드가 끝나도 자동 승격하지
않는다. 검증 결과를 확인한 후 별도 승격 호출이 필요하다.

## Cloudflare 임시 연결

테스트 중 로컬 업로드 endpoint를 RunPod에서 접근할 수 있게 연결한다.

```bash
cloudflared tunnel --url http://localhost:8000
```

발급된 hostname이 `example.trycloudflare.com`이라면 다음 값을 사용한다.

```text
RUNPOD_ARTIFACT_UPLOAD_URL=https://example.trycloudflare.com/model-artifacts/upload
MODEL_ARTIFACT_UPLOAD_URL=https://example.trycloudflare.com/model-artifacts/upload
RUNPOD_ALLOWED_UPLOAD_HOSTS=example.trycloudflare.com
```

Quick Tunnel URL은 재실행 시 바뀔 수 있으므로 RunPod endpoint 환경 변수와
로컬 환경 변수를 함께 갱신해야 한다.

## RunPod Docker 및 Endpoint

이미지 생성과 Docker Hub push:

```bash
docker build -f Dockerfile.runpod \
  -t <dockerhub-user>/import-pmax-trainer:latest .
docker push <dockerhub-user>/import-pmax-trainer:latest
```

RunPod Serverless endpoint에는 해당 이미지를 지정하고 다음 환경 변수를
설정한다.

```text
DB_HOST
DB_PORT
DB_NAME
DB_USER
DB_PASS
MODEL_ARTIFACT_UPLOAD_URL
ARTIFACT_UPLOAD_TOKEN
RUNPOD_ALLOWED_UPLOAD_HOSTS
```

Dockerfile은 참조 구조와 같은 CUDA 12.1 PyTorch runtime 이미지를 digest로
고정한다. `requirements-runpod.txt`도 해당 이미지의 Python 3.10에서 Linux
wheel이 존재하는 조합으로 고정되어 있다. 재학습 artifact의 직렬화 호환성을
유지하려면 운영 중에는 버전을 임의로 올리지 않고, 별도 candidate 검증 후
갱신한다.

DB 계정은 학습 테이블 SELECT 권한만 부여한다. 전체 candidate 검증과 승격을
하려면 `meters`를 생략해 4개 논리 계량기를 모두 학습해야 한다. 일부 계량기
학습은 RunPod GPU 학습과 artifact 업로드 연결 확인에는 사용할 수 있지만
로컬 검증을 통과할 수 없다. 예를 들어 `meters=["V.Z81"]` 또는 로컬 CLI의
`--meters V.Z81`로 1개 논리 계량기만 학습할 수 있다.

## 향후 LLM 도구 연결

LLM 도구는 별도 모델 운영 로직을 구현하지 않고 다음 작업을 호출하면 된다.

```text
start_training
get_training_status
get_model_run
validate_model_run
promote_model_run
rollback_model
```

승격과 롤백은 `confirm=true`와 승인 메모를 요구하도록 유지해, LLM의 조회 및
검증 요청이 운영 artifact를 자동 변경하지 않게 한다.
