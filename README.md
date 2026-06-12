# SKN25 Final Project - EMS Meter AI

EMS 계량기 데이터를 기반으로 전처리, 탐색 분석, 이상탐지, 전력 피크 예측 및
모델 운영 자동화를 구성한 프로젝트다.

이 브랜치에서 담당하는 주요 모델은 **Import P-Max 예측 모델**이다. 과거
24시간의 15분 데이터를 사용해 향후 60분의 Import P-Max를 15분 단위로
예측한다.

## Import P-Max 모델

- 대상: `V.Z81`, `V.Z82`, `H2.Z35x`, `H2.Z36x` 논리 계량기 4개
- 입력: 최근 24시간, 15분 간격 96행
- 출력: 향후 60분, 15분 단위 예측 4개
- 피처: 기상 정보를 제외한 22개
- 앙상블: LightGBM 2개, XGBoost 1개, CatBoost 1개
- 데이터 접근: PostgreSQL 조회 전용

## 모델 운영 흐름

```text
운영자 또는 sLLM
  -> 로컬 FastAPI
  -> RunPod Serverless GPU 재학습
  -> 로컬 candidate 업로드
  -> candidate 검증
  -> 기존 운영 모델 archive 백업
  -> candidate 승격
  -> 운영 경로 inference smoke
       성공: candidate 삭제
       실패: 기존 운영 모델 자동 복구, candidate 보존
```

RunPod는 재학습만 담당한다. 추론, 검증, 백업, 승격, smoke test와 롤백은
로컬 백엔드에서 수행한다.

## 폴더 구조

| 경로 | 내용 |
|---|---|
| `api/` | RunPod 재학습 요청, candidate 업로드, 검증·승격·롤백 FastAPI |
| `src/` | 전처리, 이상탐지, 예측, DB, agent/RAG 도메인 코드 |
| `src/forecasting/import_pmax/` | Import P-Max 학습·추론·검증·승격 핵심 로직 |
| `scripts/` | EDA, 전처리, 예측 모델 실행용 CLI 진입점 |
| `runpod_job/` | RunPod Serverless 재학습 worker |
| `config/` | 계량기 메타데이터와 조회 유틸 |
| `data/` | 환경별 설정 YAML 등 데이터 처리 설정 |
| `docs/` | 계량기, 피처, 군집화, 모델 운영 및 DB 연동 문서 |
| `notebooks/` | 분석 및 실험 노트북 |
| `artifacts/` | 운영 모델 artifact. candidate와 archive는 로컬에서 별도 관리 |
| `outputs/` | 추론 및 EDA 생성 결과 |
| `.github/workflows/` | RunPod 이미지를 GHCR에 빌드·배포하는 GitHub Actions |

## 주요 파일

| 파일 | 내용 |
|---|---|
| `Dockerfile.runpod` | RunPod Serverless 재학습 이미지 |
| `requirements-runpod.txt` | RunPod 이미지 전용 고정 의존성 |
| `requirements.txt` | 로컬 분석 및 API 의존성 |
| `.env.example` | 로컬 API와 RunPod 환경변수 예시 |

## 실행 진입점

로컬 FastAPI:

```bash
PYTHONPATH=. uvicorn api.main:app --host 0.0.0.0 --port 8000
```

로컬 4개 계량기 추론:

```bash
python -m scripts.forecasting.predict_all_import_pmax
```

로컬 candidate 검증:

```bash
python -m scripts.forecasting.promote_import_pmax --run-id <run_id>
```

상세 운영 절차는
[`docs/04_model_strategy/IMPORT_PMAX_OPERATIONS.md`](docs/04_model_strategy/IMPORT_PMAX_OPERATIONS.md)를
참조한다.
