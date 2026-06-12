# scripts

운영 보조 스크립트 폴더입니다. 모델 package 내부 로직을 호출하되, candidate 검증/승격이나 장기 배치 실행처럼 CLI로 실행하는 작업을 둡니다.

## 파일

| 파일 | 역할 |
|------|------|
| `validate_candidate.py` | candidate artifact가 완전한지, 학습 실패가 없는지, active 대비 MAE가 과도하게 악화되지 않았는지 검증하고 `validated.marker` 생성 |
| `promote_candidate.py` | 검증된 candidate를 active로 승격. 기존 active는 archive로 백업하고 smoke test 실패 시 rollback 시도 |
| `run_full_year_inference.py` | test 기간 전체를 운영 시뮬레이션처럼 돌리는 배치 추론 스크립트 |
| `generate_full_year_report.py` | full-year 추론 결과를 HTML 리포트로 변환 |

## 일반 흐름

```bash
PYTHONPATH=src:. python scripts/validate_candidate.py --run <run_id> --horizon 3
PYTHONPATH=src:. python scripts/promote_candidate.py --run <run_id> --horizon 3 --yes
```

자세한 명령어는 `docs/04_model_strategy/MODEL_OPERATION_COMMANDS.md`를 확인합니다.
