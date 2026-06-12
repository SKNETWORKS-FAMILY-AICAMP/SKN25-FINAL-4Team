# 모델 운영 명령어 순서

이 문서는 `energy_v84` v84 residual 모델을 학습, 추론, 재학습, 검증, 승격할 때 사용하는 명령어 흐름을 정리한다.

기본 위치:

```bash
cd /home/sms/openclaw_file/skn25_final
conda activate skn25
```

## 1. 로컬 API 실행

RunPod 재학습 결과를 받거나, 백엔드 API로 재학습을 시작하려면 먼저 API 서버가 떠 있어야 한다.

```bash
set -a
source .env
set +a
PYTHONPATH=src:. uvicorn api.main:app --host 127.0.0.1 --port 8000
```

확인:

```bash
curl "http://127.0.0.1:8000/"
```

정상 응답:

```json
{"status":"ok","message":"Energy Platform API is running"}
```

로컬 테스트에서 RunPod이 이 API로 artifact를 업로드해야 하면 별도 터미널에서 Cloudflare tunnel을 연다.

```bash
docker run --rm --network host cloudflare/cloudflared:latest tunnel \
  --url http://127.0.0.1:8000 \
  --protocol http2 \
  --no-autoupdate
```

Cloudflare quick tunnel 주소가 바뀌면 아래 값을 같이 바꿔야 한다.

- 로컬 `.env`: `RUNPOD_ARTIFACT_UPLOAD_URL`
- RunPod endpoint 환경변수: `MODEL_ARTIFACT_UPLOAD_URL`, `RUNPOD_ALLOWED_UPLOAD_HOSTS`

## 2. 추론 실행

운영 기준 서비스 horizon은 `3h`다.

현재 시각 기준으로 추론:

```bash
PYTHONPATH=src:. python -m energy_v84.inference --horizon 3
```

특정 timestamp 기준으로 재현 테스트:

```bash
PYTHONPATH=src:. python -m energy_v84.inference \
  --horizon 3 \
  --timestamp "2023-06-01T09:00:00+00:00"
```

출력 위치를 지정:

```bash
PYTHONPATH=src:. python -m energy_v84.inference \
  --horizon 3 \
  --timestamp "2023-06-01T09:00:00+00:00" \
  --output-dir /tmp/inference_results
```

기본 출력 파일:

```text
artifacts/inference_results/predictions_3h_YYYYMMDDTHHMM.csv
```

주의:

- `--timestamp`를 생략하면 현재 UTC 시각을 기준으로 실행한다.
- Airflow나 스케줄러 운영에서는 timestamp를 생략하거나 스케줄 기준 시각을 명시적으로 넘기는 방식 중 하나로 통일해야 한다.
- 현재 CSV 저장은 timestamp별 파일 생성 구조다. 운영 DB 적재 시에는 같은 스키마를 테이블에 append/upsert하는 방식으로 바꾸면 된다.

## 3. 로컬 직접 학습

로컬에서 candidate artifact를 직접 만들 때 사용한다. 기본적으로 `--output-dir`을 생략하면 candidate run 디렉터리가 생성된다.

전체 3h 학습:

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

기본 epoch:

```text
EPOCHS = 12
```

근거:

- `src/energy_v84/common/config.py`
- `src/energy_v84/train.py`

## 4. 백엔드 API로 RunPod 재학습 시작

이 방식이 최종 운영 목표에 가까운 구조다.

흐름:

```text
사용자/LLM
  -> 백엔드 API /training/start
  -> RunPod Serverless
  -> runpod_job.handler
  -> train.py
  -> candidate artifact 생성
  -> /model-artifacts/upload
  -> 백엔드 candidate 저장
```

연결 확인용 단일 계량기:

```bash
curl -X POST "http://127.0.0.1:8000/training/start" \
  -H "Authorization: Bearer ${ARTIFACT_UPLOAD_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{"horizon":3,"meters":["H1.Z10"],"epochs":1}'
```

전체 3h 재학습:

```bash
curl -X POST "http://127.0.0.1:8000/training/start" \
  -H "Authorization: Bearer ${ARTIFACT_UPLOAD_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{"horizon":3}'
```

`run_id`를 생략하면 백엔드가 자동 생성한다.

```text
run_YYYYMMDDTHHMMSSZ_xxxxxxxx
```

상태 확인:

```bash
curl "http://127.0.0.1:8000/training/latest" \
  -H "Authorization: Bearer ${ARTIFACT_UPLOAD_TOKEN}"
```

특정 job 확인:

```bash
curl "http://127.0.0.1:8000/training/<job_id>/status" \
  -H "Authorization: Bearer ${ARTIFACT_UPLOAD_TOKEN}"
```

성공 기준:

```text
status = COMPLETED
output.status = uploaded
next_action = validate
```

RunPod worker가 DB timeout으로 실패하면 학습 코드 흐름 문제가 아니라 RunPod worker에서 DB에 접근하지 못한 것이다.

## 5. Candidate 확인

RunPod이 업로드한 candidate가 백엔드에 들어왔는지 확인한다.

```bash
curl "http://127.0.0.1:8000/model-runs/<run_id>?horizon=3" \
  -H "Authorization: Bearer ${ARTIFACT_UPLOAD_TOKEN}"
```

확인 포인트:

- `candidate_exists=true`
- `horizon_dir_exists=true`
- `meter_dir_count`
- `summary.rows`

단일 계량기 smoke candidate는 전체 51개를 포함하지 않기 때문에 validate에서 실패하는 것이 정상이다.

## 6. Candidate 검증

CLI:

```bash
PYTHONPATH=src:. python scripts/validate_candidate.py --run <run_id> --horizon 3
```

API:

```bash
curl -X POST "http://127.0.0.1:8000/model-runs/<run_id>/validate?horizon=3" \
  -H "Authorization: Bearer ${ARTIFACT_UPLOAD_TOKEN}"
```

검증 내용:

- 기대 계량기 artifact 디렉터리 존재 여부
- routing 기반 필수 모델 파일 존재 여부
- `train_summary_3h.csv` 존재 여부
- 필수 컬럼 존재 여부
- 학습 실패 계량기 여부
- 기존 active 대비 MAE 악화 여부

검증 결과:

| 결과 | 의미 |
|---|---|
| exit 0 / `result=pass` | 승격 가능 |
| exit 2 / `result=warn` | MAE 악화 경고. 사람이 확인 후 조건부 승격 가능 |
| exit 1 | 실패. 승격 불가 |

검증 통과 또는 경고 통과 시 `validated.marker`가 생성된다.

## 7. 운영 승격

CLI:

```bash
PYTHONPATH=src:. python scripts/promote_candidate.py --run <run_id> --horizon 3 --yes
```

경고 candidate를 승인하고 승격:

```bash
PYTHONPATH=src:. python scripts/promote_candidate.py --run <run_id> --horizon 3 --allow-warn --yes
```

API:

```bash
curl -X POST "http://127.0.0.1:8000/model-runs/<run_id>/promote?horizon=3&confirm=true" \
  -H "Authorization: Bearer ${ARTIFACT_UPLOAD_TOKEN}"
```

경고 candidate 승격:

```bash
curl -X POST "http://127.0.0.1:8000/model-runs/<run_id>/promote?horizon=3&confirm=true&allow_warn=true" \
  -H "Authorization: Bearer ${ARTIFACT_UPLOAD_TOKEN}"
```

승격 순서:

```text
active 백업
candidate -> active 승격
smoke test 실행
smoke test 성공 시 candidate 삭제
smoke test 실패 시 archive에서 active 롤백
```

주의:

- `validated.marker`가 없으면 승격하지 않는다.
- 승격 성공 후 candidate는 삭제된다.
- smoke test 실패 시 candidate는 보존되어야 하며, active는 이전 archive로 롤백되어야 한다.

## 8. RunPod Endpoint 환경변수

RunPod endpoint에는 최소 아래 값이 필요하다.

```env
DB_HOST=...
DB_PORT=...
DB_NAME=...
DB_USER=...
DB_PASS=...
ARTIFACT_UPLOAD_TOKEN=...
MODEL_ARTIFACT_UPLOAD_URL=https://<api-host>/model-artifacts/upload
RUNPOD_ALLOWED_UPLOAD_HOSTS=<api-host>
```

선택:

```env
RUNPOD_TRAIN_TIMEOUT_SECONDS=86400
RUNPOD_UPLOAD_RETRIES=3
```

`MODEL_ARTIFACT_UPLOAD_URL`과 `RUNPOD_ALLOWED_UPLOAD_HOSTS`는 Cloudflare quick tunnel 주소가 바뀔 때마다 수정해야 한다.

## 9. 로컬 API 환경변수

로컬 또는 서버 노트북 `.env`에는 최소 아래 값이 필요하다.

```env
DB_HOST=...
DB_PORT=...
DB_NAME=...
DB_USER=...
DB_PASS=...

RUNPOD_API_KEY=...
RUNPOD_ENDPOINT_ID=...
RUNPOD_API_TIMEOUT_SECONDS=60

ARTIFACT_UPLOAD_TOKEN=...
RUNPOD_ARTIFACT_UPLOAD_URL=https://<api-host>/model-artifacts/upload
```

`RUNPOD_API_KEY`는 RunPod API key다. `ARTIFACT_UPLOAD_TOKEN`은 백엔드 API와 RunPod worker가 공유하는 업로드 인증 토큰이다.

## 10. 전체 운영 순서 요약

```text
1. API 서버 실행
2. 필요 시 Cloudflare tunnel 실행
3. RunPod endpoint 환경변수 확인
4. /training/start 호출
5. /training/latest 또는 /training/{job_id}/status로 완료 확인
6. /model-runs/{run_id}로 candidate 확인
7. /model-runs/{run_id}/validate 실행
8. 검증 결과 사람이 확인
9. /model-runs/{run_id}/promote 실행
10. 추론 smoke test 또는 실제 추론으로 active 확인
```

