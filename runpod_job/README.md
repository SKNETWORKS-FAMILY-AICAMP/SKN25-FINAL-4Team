# runpod_job/

Import P-Max RunPod Serverless 재학습 worker다. RunPod에서는 학습과 candidate
생성만 수행한다. 추론, 검증, 백업, 승격과 롤백은 로컬 백엔드가 담당한다.

## 파일 구조

| 파일 | 내용 |
|---|---|
| `handler.py` | RunPod 요청 검증, 학습 subprocess 실행, artifact 압축·업로드, 실패 로그 반환 |
| `__init__.py` | Python package 표시 |
| `README.md` | worker 구조와 설정 안내 |

## 실행 흐름

```text
local FastAPI /training/start
  -> RunPod Serverless endpoint
  -> scripts.forecasting.train_import_pmax
  -> candidate tar.gz
  -> local FastAPI /model-artifacts/upload
```

## RunPod 환경변수

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

선택 설정:

```text
RUNPOD_TRAIN_TIMEOUT_SECONDS
RUNPOD_UPLOAD_TIMEOUT_SECONDS
RUNPOD_UPLOAD_RETRIES
RUNPOD_KEEP_ARCHIVES
RUNPOD_ALLOW_ANY_UPLOAD_HOST
```

## 이미지 배포

`mun/workspace` 브랜치의 관련 파일이 변경되면 GitHub Actions가
`Dockerfile.runpod`을 빌드해 private GHCR package로 push한다.

```text
ghcr.io/sknetworks-family-aicamp/import-pmax-trainer:mun-workspace
```

완전한 candidate를 만들 때는 `meters`를 생략한다. 일부 계량기 지정은 RunPod,
GPU, DB, 업로드 연결 확인용이며 로컬 검증과 승격을 통과할 수 없다.
