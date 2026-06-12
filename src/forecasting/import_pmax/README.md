# src/forecasting/import_pmax/

Import P-Max 예측 모델의 핵심 도메인 모듈이다. API 또는 CLI에 종속되지 않고
Python 함수로 학습, 추론, 검증, 승격과 롤백을 수행한다.

## 파일 구조

| 파일 | 내용 |
|---|---|
| `__init__.py` | `import_pmax` Python package 표시 |
| `training.py` | DB 조회, 전처리, window 생성, 4개 모델 학습, 앙상블 가중치 계산, candidate 저장 |
| `inference.py` | 단일 논리 계량기의 artifact 로드, 입력 window 생성, 앙상블 추론 |
| `batch_inference.py` | 4개 논리 계량기 일괄 추론과 JSON/CSV 결과 생성 |
| `csv_store.py` | 추론 CSV를 계량기·기준시각·대상시각 키로 upsert |
| `operations.py` | 운영, candidate, archive, incoming, job 디렉터리와 `run_id` 관리 |
| `validation.py` | candidate 완전성, 모델 역직렬화, feature, 지표, digest 검증 |
| `promotion.py` | 운영본 백업, 원자적 승격, inference smoke, 자동 롤백, 명시적 롤백 |

## Artifact 경로

```text
artifacts/
  import_pmax_v29_60min/       현재 운영 모델
  import_pmax_candidates/      run_id별 재학습 결과
  import_pmax_archives/        승격 전 운영본과 롤백 교체본
  import_pmax_incoming/        업로드 임시 파일
  import_pmax_training_jobs/   RunPod 작업 상태
```

승격 성공 시 candidate는 삭제된다. inference smoke 실패 시 새 운영본을
제거하고 기존 운영본을 복구하며 candidate와 archive는 보존한다.

DB 연결은 `default_transaction_read_only=on`으로 강제한다.
