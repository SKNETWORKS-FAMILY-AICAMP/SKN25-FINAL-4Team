# Gate 0 route inventory evidence

## 판정

`gate_00_source_drift`: PASS

## 확인 범위

- 로컬 source 기준: `src/cms/service/routers/report.py`
- route contract test: `tests/service/test_report_api.py`
- runtime smoke: public latest route만 확인
- backend deploy: 수행하지 않음

## Route parity

확인된 로컬 route:

- `GET /report/ops/{cadence}/latest`
- `POST /report/ops/{cadence}/generate`
- `GET /report/ops/{cadence}/periods`
- `GET /report/ops/{cadence}/periods/{period_key}`
- `GET /report/ops/{cadence}/periods/{period_key}/markdown`

`latest`/`generate` 호환 route는 유지하고, cadence table 기반 기간 조회 route를 로컬 source에 추가했습니다.

## Legacy compatibility

기존 일간/월간 legacy report API는 삭제하지 않았습니다. 새 저장 계약도 별도 `ops.report_document`가 아니라 기존 `ops.daily_report`, `ops.weekly_report`, `ops.monthly_report`를 유지합니다.

## Runtime boundary

수행한 runtime 작업:

- frontend active static 교체
- public frontend asset smoke
- public API latest read-only smoke
- Airflow health read-only smoke
- DB read-only catalog 확인

수행하지 않은 runtime 작업:

- AWS DB DDL 실행
- backend deployed file modification
- container restart
- Airflow trigger
- sLLM 실제 호출

## 검증 명령

```bash
PYTHONPATH=src pytest -q tests/service/test_report_api.py
```

통합 focused 검증 결과:

```text
PYTHONPATH=src pytest -q tests/workflow/test_report_rework_contract.py tests/service/test_report_api.py tests/frontend/test_report_panel_contract.py
............                                                             [100%]
12 passed in 0.77s
```

## Metric 판정

| Metric | 판정 | 근거 |
|---|---|---|
| route_parity | PASS | `/report/ops/{cadence}/latest`, `/generate`, `/periods`, `/periods/{period_key}`, `/markdown` route test 통과 |
| legacy_compat | PASS | 기존 report router 내부 legacy daily/monthly API 삭제 없음 |
| runtime_boundary | PASS/BLOCK split | approved frontend/runtime smoke는 수행, backend deploy는 미수행 |
| handoff_clarity | PASS | cadence report table 유지와 backend deploy 필요 항목 분리 |
