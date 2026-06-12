# api/

Import P-Max 모델 운영을 위한 로컬 FastAPI 애플리케이션이다.

API는 RunPod 재학습 요청과 상태 조회, candidate artifact 수신, 검증, 승격,
롤백을 외부 호출 가능한 형태로 제공한다. 학습·검증·승격의 핵심 로직은
`src/forecasting/import_pmax/`에 있고, 이 폴더는 HTTP 호출 계층을 담당한다.

## 파일 구조

| 파일 | 내용 |
|---|---|
| `__init__.py` | `api` Python package 표시 |
| `main.py` | FastAPI 앱 생성, CORS 설정, router 등록, health endpoint |
| `routers/__init__.py` | `routers` Python package 표시 |
| `routers/model_auth.py` | Bearer token 인증과 `run_id` 검증 |
| `routers/model_paths.py` | candidate, archive, 운영 모델, job 기록 경로 |
| `routers/model_training.py` | RunPod 작업 요청 및 상태 조회 |
| `routers/model_artifacts.py` | RunPod 학습 결과 압축 파일 업로드와 안전한 해제 |
| `routers/model_runs.py` | candidate 상태 조회, 검증, 승격, 명시적 롤백 |

## Endpoint

| Method | 경로 | 역할 |
|---|---|---|
| `GET` | `/` | API health check |
| `POST` | `/training/start` | RunPod 비동기 재학습 요청 |
| `GET` | `/training/latest` | 최근 재학습 작업 상태 |
| `GET` | `/training/{job_id}/status` | 특정 RunPod 작업 상태 갱신 |
| `POST` | `/model-artifacts/upload` | candidate artifact 업로드 |
| `POST` | `/model-artifacts/probe` | 경량 업로드 연결 확인 |
| `GET` | `/model-runs/{run_id}` | candidate 또는 승격 상태 조회 |
| `POST` | `/model-runs/{run_id}/validate` | candidate 검증 |
| `POST` | `/model-runs/{run_id}/promote` | 백업·승격·inference smoke 실행 |
| `POST` | `/model-runs/rollback` | archive를 사용한 명시적 롤백 |

모델 운영 endpoint는 `.env`의 `ARTIFACT_UPLOAD_TOKEN`을 Bearer token으로
사용한다.
