# Backend / Frontend API Contract

**Status:** active service-plane contract
**Scope:** CMS backend routes consumed by a future frontend/dashboard client
**Updated:** 2026-06-15

## 1. Purpose

이 문서는 frontend가 호출할 CMS Backend API surface를 고정한다. Airflow, scheduler, LangGraph, report worker의 내부 node 설계는 이 문서 범위가 아니다. Backend는 lightweight service plane이며, long-running workflow는 job/status/artifact contract로 분리한다.

## 2. Runtime boundary

```text
Frontend / dashboard
-> CMS Backend API
-> read-only plan, status, model-result summary, job ticket, approval record
-> optional worker/workflow plane
```

Backend request path에서 직접 수행하지 않는 작업은 다음과 같다.

```text
bulk ETL
model training
blocking model inference execution
canonical write
promotion
production DDL
deployment
email send
Airflow DAG execution
LangGraph side-effect execution
```

`/chat/route`는 user intent routing과 job registration만 담당한다. 일반 chat request path에 Airflow나 LangGraph 실행을 직접 붙이지 않는다.

## 3. Base URLs

현재 PC1 runtime은 ingestion API와 backend API를 분리한다. Public endpoint는 backend role만 노출한다.

| Environment | Base URL | Notes |
|---|---|---|
| PC1 local ingestion | `http://127.0.0.1:8000` | `cms-ingestion-api`, health `service=CMS Ingestion API`, ingestion role |
| PC1 local backend | `http://127.0.0.1:8001` | `cms-backend-api`, health `service=CMS Backend API`, backend role |
| Public CMS backend | `http://121.134.46.24:18000` | backend role only; ingestion remains private unless separately approved |

Frontend 기본 API client는 public CMS backend를 직접 호출한다. Reverse proxy 사용 시 `VITE_API_URL=/api`처럼 build/runtime 환경값으로 override한다.

## 4. Common response flags

Backend route는 가능한 한 다음 flags를 포함해야 한다.

| Field | Type | Meaning |
|---|---:|---|
| `writes_allowed` | boolean | Backend request path에서 write 허용 여부. 기본 `false` |
| `side_effects_executed` | boolean | 해당 request가 DB write, send, workflow execution 등 side effect를 수행했는지 |
| `dry_run` | boolean | dry-run/status/contract 응답 여부 |
| `route` | string | logical route name |
| `status` | string | route-level status |

Frontend는 `writes_allowed=false`와 `side_effects_executed=false`를 안전 상태로 표시한다.

## 5. Route contract

### 5.1 `GET /`

Service index와 route discovery를 반환한다.

Response shape:

```json
{
  "service": "CMS Backend API",
  "version": "0.1.0",
  "role": "backend",
  "status": "ok",
  "docs": "/docs",
  "health": "/health",
  "routes": [
    {"method": "GET", "path": "/health", "description": "import-safe health contract"}
  ],
  "writes_allowed": false
}
```

Frontend usage:

- boot-time route discovery
- backend role 확인
- backend가 ingestion-only service가 아닌지 확인

### 5.2 `GET /health`

Backend health와 canonical boundary를 반환한다.

Response shape:

```json
{
  "status": "ok",
  "service": "CMS Backend API",
  "role": "backend",
  "canonical_tables": [
    "canonical.measurement_1min",
    "canonical.measurement_15min",
    "canonical.measurement_1h"
  ],
  "writes_allowed": false
}
```

Frontend usage:

- health badge
- read-only/canonical boundary display

### 5.3 `GET /contracts`

Source/cache/workflow boundary를 반환한다.

Frontend usage:

- admin/debug contract panel
- canonical/reference/mart boundary 설명

### 5.4 `POST /query/plan`

Text-to-SQL 또는 evidence answer 요청을 DB 실행 없이 read-only SQL plan으로 변환한다.

Request shape:

```json
{
  "text": "최근 P-Max forecast 상태 보여줘",
  "context": {},
  "route_hint": "evidence_answer",
  "user_id": "optional",
  "limit": 100
}
```

Response는 executable result가 아니라 read-only plan이다. Frontend는 이 route 결과를 “query preview”로 표시하고, 실제 DB 실행 결과로 표시하지 않는다.

### 5.5 `POST /chat/route`

User request를 lightweight route로 분류한다.

Request shape:

```json
{
  "text": "최근 모델 결과 요약해줘",
  "context": {},
  "route_hint": "evidence_answer",
  "user_id": "frontend-user"
}
```

Possible response: inline quick answer

```json
{
  "mode": "inline",
  "route": "quick_answer",
  "reason": "short factual request",
  "response": {
    "route": "quick_answer",
    "message": "quick answer; handled in the FastAPI fast path",
    "side_effects_executed": false
  },
  "writes_allowed": false,
  "side_effects_executed": false
}
```

Possible response: job ticket

```json
{
  "mode": "job",
  "route": "needs_job",
  "reason": "long-running work requested",
  "job_id": "rev-0001",
  "status": "queued",
  "status_url": "/ops/jobs/rev-0001",
  "writes_allowed": false,
  "side_effects_executed": false
}
```

Frontend usage:

- if `mode=inline`, render response immediately
- if `mode=job`, show job card and poll `status_url`
- if `route=approval_required`, show approval card; do not assume execution occurred

### 5.6 `GET /ops/jobs/{job_id}`

Review/job status snapshot을 반환한다.

Response shape:

```json
{
  "job_id": "rev-0001",
  "route": "needs_job",
  "reason": "long-running work requested",
  "status": "queued",
  "status_url": "/ops/jobs/rev-0001",
  "awaiting_approval": false,
  "approved_by": null,
  "writes_allowed": false,
  "side_effects_executed": false,
  "job": {},
  "response": null
}
```

Frontend usage:

- job detail page
- polling endpoint
- status badges: `queued`, `running`, `succeeded`, `failed`, `cancelled`

### 5.7 `POST /ops/jobs/{job_id}/run`

Worker stub route다. 현재 implementation은 deterministic dry-run review를 실행한다. Production worker execution은 이 route에 직접 묶지 않는다.

Response includes:

```json
{
  "dry_run": true,
  "worker": "stub",
  "writes_allowed": false,
  "side_effects_executed": false
}
```

Frontend usage:

- development/admin smoke only
- production UI에서는 일반 사용자가 누르는 실행 버튼으로 노출하지 않는다

### 5.8 `POST /ops/approvals/{job_id}`

Human approval record만 남긴다. 승인 이후 실제 side-effect execution은 deferred worker가 별도 gate로 수행한다.

Request shape:

```json
{
  "approved_by": "viowlet"
}
```

Response includes:

```json
{
  "awaiting_approval": false,
  "approved_by": "viowlet",
  "writes_allowed": false,
  "side_effects_executed": false
}
```

Frontend usage:

- approval card
- approval audit display
- approval이 execution 완료를 의미하지 않는다는 안내 표시

### 5.9 `GET /model/results/summary`

Model result table contract와 runtime DB read-back summary를 read-only로 반환한다. DB 설정이 없거나 unavailable이면 `db_config_missing`/`unavailable` 계열 payload로 graceful fallback하며 normal service path를 막지 않는다.

Response shape:

```json
{
  "route": "/model/results/summary",
  "role": "backend",
  "status": "ok",
  "dry_run": true,
  "side_effects_executed": false,
  "db_read_attempted": true,
  "writes_allowed": false,
  "run_id": "e2e_ops_least_priv_write_20260614T084037Z",
  "counts": {
    "mart.pmax_forecast_15min": {"rows_by_base_ts": 16, "distinct_keys": 16, "critical_nulls": 0},
    "mart.anomaly_warning_1h": {"rows_by_run_id": 9, "distinct_keys": 9, "critical_nulls": 0}
  },
  "model_result_tables": [
    "mart.pmax_forecast_15min",
    "ops.pmax_forecast_inference_log",
    "qa.model_serving_evidence_packet"
  ]
}
```

Frontend usage:

- model serving panel의 contract/status/DB read-back summary
- summary rows/counts를 표시하되 write/execution 완료로 표시하지 않는다
- public endpoint unavailable 시 normal UI는 graceful fallback 상태를 표시한다

### 5.10 `POST /reports/email/dry-run`

Report email payload를 검증하지만 실제 email을 보내지 않는다.

Request shape:

```json
{
  "recipients": ["ops@example.com"],
  "subject": "CMS daily report",
  "body": "report body"
}
```

Response includes:

```json
{
  "status": "queued",
  "dry_run": true,
  "side_effects_executed": false,
  "send_attempted": false,
  "writes_allowed": false,
  "queue": "local-dry-run"
}
```

Frontend usage:

- report preview form validation
- email send 완료로 표시하지 않는다

## 6. Frontend page mapping

| Frontend page/panel | Backend route | UI state |
|---|---|---|
| System health | `GET /health` | green if `status=ok` and `writes_allowed=false` |
| Route discovery/admin | `GET /` | route table |
| Query preview | `POST /query/plan` | SQL preview, not DB result |
| Chat router | `POST /chat/route` | inline answer or job card |
| Job detail | `GET /ops/jobs/{job_id}` | status/progress/result |
| Approval card | `POST /ops/approvals/{job_id}` | approval recorded, execution deferred |
| Model serving summary | `GET /model/results/summary` | read-only DB read-back summary/fallback |
| Report email preview | `POST /reports/email/dry-run` | validation only |

## 7. Current runtime evidence

PC1/public backend runtime smoke on 2026-06-14:

```text
GET http://121.134.46.24:18000/                    OK, service=CMS Backend API, role=backend, routes=22
GET http://121.134.46.24:18000/health              OK, writes_allowed=false
GET http://121.134.46.24:18000/model/results/summary?run_id=e2e_ops_least_priv_write_20260614T084037Z
  OK, db_read_attempted=true, writes_allowed=false, side_effects_executed=false
```

Repo-local verification:

```text
tests/service + tests/workflow + tests/test_api_dry_run.py: 81 passed, 4 skipped
```

## 8. Known gaps

1. Frontend implementation은 `frontend/`에 존재하며 API client 기본값은 public CMS backend다.
2. `GET /model/results/summary`는 read-only summary/counts를 반환한다. 행 단위 forecast result UI가 필요하면 별도 paginated read-only endpoint가 필요하다.
3. LangGraph/API-key dependent work is optional async review/fallback only; FastAPI normal path에 blocking dependency로 두지 않는다.

## 9. Acceptance checklist

- [x] Backend service exposes route discovery and health.
- [x] Backend route contract keeps writes disabled by default.
- [x] Chat route registers jobs or returns inline response; it does not execute workflow side effects.
- [x] Job status and approval routes exist for frontend cards.
- [x] Model result summary route exposes read-only contract.
- [x] Repo-local service/workflow tests pass.
- [x] Actual frontend implementation consumes health/route discovery/model summary from public endpoint.
- [x] Public endpoint maps to CMS backend for external UI smoke.
- [ ] Read-only forecast-result data endpoint is added if frontend needs actual result rows.
