# scripts/forecasting/

Import P-Max 도메인 함수를 명령행에서 실행하는 CLI 진입점이다. 실제 로직은
`src/forecasting/import_pmax/`에 있다.

| 파일 | 내용 |
|---|---|
| `__init__.py` | 모듈 실행을 위한 Python package 표시 |
| `train_import_pmax.py` | 전체 또는 지정 논리 계량기 재학습 |
| `predict_import_pmax.py` | 단일 논리 계량기 추론 |
| `predict_all_import_pmax.py` | 4개 논리 계량기 일괄 추론 |
| `promote_import_pmax.py` | candidate 검증 및 승인 후 승격 |
| `rollback_import_pmax.py` | 지정 archive를 운영 경로로 롤백 |

프로젝트 루트에서 모듈 방식으로 실행한다.

```bash
python -m scripts.forecasting.train_import_pmax --run-id <run_id>
python -m scripts.forecasting.predict_all_import_pmax
python -m scripts.forecasting.promote_import_pmax --run-id <run_id>
```

`--meters V.Z81`처럼 일부 계량기만 학습할 수 있지만, 검증과 승격에는 4개
논리 계량기의 완전한 candidate가 필요하다.
