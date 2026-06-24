# report_rework Gate Score Ledger

## 전체 판정

`gate_00`부터 `gate_07`까지 로컬 source/test 기준 PASS입니다. `gate_08`은 fern final review-only 재검토에서 PASS 판정을 받았습니다. 승인 경계 밖 runtime 작업은 별도 승인 필요 항목으로 남겨 두었습니다.

## 공통 승인 경계와 실행 결과

이번 실행에서 수행한 승인된 작업:

- frontend active static 교체: `/home/skn25/cms-agent-frontend-current` 갱신
- frontend backup 생성: `/home/skn25/cms-agent-frontend-current.20260624T014634Z.bak`, `/home/skn25/cms-agent-frontend-current.20260624T022108Z.bak`
- public frontend smoke: active `report_panel-Dx9pyNWx.js` 확인
- runtime smoke: public API latest, Airflow health, API health, DB read-only catalog 확인

이번 실행에서 수행하지 않은 작업:

- AWS DB DDL 실행
- backend/API deployed file modification
- container restart
- Airflow trigger
- sLLM/API 실제 호출

남은 승인 필요 항목:

1. cadence report table column migration 적용 여부 승인
   - 현재 runtime DB에는 `ops.daily_report`, `ops.weekly_report`, `ops.monthly_report`가 존재합니다.
   - `ops.report_document`는 runtime DB에 없습니다.
   - 새 보고서 필드(`title`, `executive_summary`, `markdown`, `report_json`, `operator_actions`)는 candidate DDL 상태입니다.
2. backend route 배포 승인
   - public runtime `/api/report/ops/{cadence}/latest`는 동작합니다.
   - 새 `/periods`, `/periods/{period_key}`, `/markdown` route는 로컬 source/test에는 있지만 backend deploy 전입니다.
3. Airflow DAG trigger는 별도 승인 전 금지입니다.
4. sLLM/API 실제 호출 smoke는 별도 승인 전 금지입니다.
5. fern final review-only 재검토 완료: HTTP 429 해소 후 재시도에서 `REQUEST_CHANGES`, 보완 후 최종 `PASS` 판정을 받았습니다.

## 저장 계약 판정

`ops.report_document`는 현재 필요하지 않은 것으로 판정했습니다. 이유는 다음과 같습니다.

- runtime DB read-only catalog에서 기존 cadence table 3개가 확인됨:
  - `ops.daily_report`
  - `ops.weekly_report`
  - `ops.monthly_report`
- `ops.report_document`는 runtime DB에 없음.
- 현재 report API도 cadence별 latest를 이미 반환함.
- 단순한 변경은 새 통합 테이블 추가가 아니라 기존 cadence table에 사용자용 보고서 필드를 additive column으로 확장하는 것입니다.

## Gate별 metric ledger

| Gate | Metric | 판정 | Evidence |
|---|---|---|---|
| gate_00 source_drift | route_parity | PASS | `tests/service/test_report_api.py`; local ops latest/generate/period route 확인 |
| gate_00 source_drift | legacy_compat | PASS | 기존 legacy daily/monthly route 삭제 없음 |
| gate_00 source_drift | runtime_boundary | PASS | backend deploy 없이 frontend/static만 승인 범위에서 교체 |
| gate_01 storage_contract | ddl_safety | PASS | `scripts/database/migrations/report_generation_tables.sql` candidate only, AWS DB 미실행 |
| gate_01 storage_contract | schema_fit | PASS | cadence table additive columns로 `title`, `executive_summary`, `markdown`, `report_json`, `operator_actions`, `updated_at` 정의 |
| gate_01 storage_contract | compat_path | PASS | `ops.daily_report`, `ops.weekly_report`, `ops.monthly_report` 유지 |
| gate_01 storage_contract | idempotency | PASS | cadence table primary key conflict + `idempotency_key` unique index 후보 |
| gate_02 context_pack | observed_first | PASS | `report_context_pack.v2`, observed summary test |
| gate_02 context_pack | inspection_candidates | PASS | 실제 이상 경고 후보만 구조화 test |
| gate_02 context_pack | operator_actions | PASS | context pack 및 renderer output에 operator_actions 포함 |
| gate_03 deterministic_renderer | fallback_complete | PASS | deterministic JSON/Markdown renderer test |
| gate_03 deterministic_renderer | user_language | PASS | 핵심 요약/사용 패턴/주요 계량기/점검 후보/운영자 조치 source marker |
| gate_03 deterministic_renderer | developer_terms_hidden | PASS | frontend/public asset scan 및 renderer contract test에서 내부 운영 지표/구현용어 미검출 |
| gate_04 sllm_renderer | adapter_only | PASS | `api_report_generator()` thin API adapter, env-gated, 기본 disabled |
| gate_04 sllm_renderer | json_only | PASS | `validate_sllm_report_json()` structured output test |
| gate_04 sllm_renderer | fallback_safe | PASS | disabled path deterministic fallback test |
| gate_05 airflow | schedule_kept | PASS | daily/weekly/monthly wrapper import-safe 유지 |
| gate_05 airflow | watermark_period | PASS | `resolve_report_period_from_watermark()` unit test |
| gate_05 airflow | runtime_smoke | PASS | public `/airflow/health` 200 healthy |
| gate_06 api | local_routes | PASS | local route contract test |
| gate_06 api | runtime_latest | PASS | public `/api/report/ops/{daily,weekly,monthly}/latest` 200 |
| gate_06 api | runtime_new_routes | BLOCK | `/api/report/ops/daily/periods` 404; backend deploy not approved/performed |
| gate_07 frontend | local_build | PASS | Vite build 성공 |
| gate_07 frontend | active_static | PASS | active static copied to PC1 with backup |
| gate_07 frontend | public_asset | PASS | public `report_panel-Dx9pyNWx.js` marker scan PASS |
| gate_08 final_review | fern_initial_retry | REQUEST_CHANGES | 사용자 노출 내부 구현 용어, candidate DDL `updated_at` self-consistency, 무점검 후보 경로 내부 용어 지적 |
| gate_08 final_review | remediation | PASS | `warning_flag=true`, generation 방식, `Guard`, `deterministic_fallback`, `Wiki/RAG` 사용자 노출 제거; daily/monthly `updated_at` DDL 추가; 무점검 후보 기본 조치 문구 보완 |
| gate_08 final_review | fern_pass | PASS | 보완 후 fern final review-only PASS; read-only compile, no-candidate render, focused pytest `14 passed` |
| gate_08 final_review | approval_clear | PASS | remaining approval gates separated |

## 검증 명령과 결과

```bash
python -m py_compile \
  src/cms/workflow/reports/generation.py \
  src/cms/workflow/reports/context_pack.py \
  src/cms/workflow/reports/renderer.py \
  src/cms/workflow/report_readiness_airflow.py \
  src/cms/service/routers/report.py
```

결과: exit code 0.

```bash
PYTHONPATH=src pytest -q tests/workflow/test_report_rework_contract.py tests/service/test_report_api.py tests/frontend/test_report_panel_contract.py
```

결과:

```text
..............                                                           [100%]
14 passed in 0.54s
```

```bash
cd src/frontend && npm install && npm run build
```

결과: Vite build 성공, active deployment asset `report_panel-Dx9pyNWx.js`.

Runtime smoke 결과:

```text
/api/report/ops/daily/latest   -> 200, period=2023-12-04
/api/report/ops/weekly/latest  -> 200, period=2023-11-27_2023-12-03
/api/report/ops/monthly/latest -> 200, period=2023-12
/api/health                    -> 200, {"status":"ok"}
/airflow/health                -> 200, healthy
```

DB read-only catalog 결과:

```text
to_regclass('ops.daily_report')   = ops.daily_report
to_regclass('ops.weekly_report')  = ops.weekly_report
to_regclass('ops.monthly_report') = ops.monthly_report
to_regclass('ops.report_document') = NULL
```
