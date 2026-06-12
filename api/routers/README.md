# api/routers

FastAPI endpoint를 기능별로 나눈 폴더입니다.

## 파일

| 파일 | 역할 |
|------|------|
| `meters.py` | 계량기 메타데이터 조회 API |
| `model_auth.py` | bearer token 인증 helper |
| `model_paths.py` | artifact/candidate/upload/job 저장 경로 정의 |
| `model_artifacts.py` | RunPod 또는 외부에서 생성한 artifact 압축 파일 업로드/조회 API |
| `model_runs.py` | candidate run 상태 조회, validate/promote 실행 API |
| `model_training.py` | RunPod serverless 재학습 요청/상태 조회 API |
| `__init__.py` | router package 인식용 파일 |

## 주의

- 인증은 `ARTIFACT_UPLOAD_TOKEN` 기반 bearer token을 사용합니다.
- 실제 DB 쓰기 구조로 바뀌면 추론 결과 저장 API는 별도 router로 추가하는 것이 좋습니다.
