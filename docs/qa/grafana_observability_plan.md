# Grafana Observability Plan

**갱신일:** 2026-06-04  
**상태:** local provisioning contract  
**범위:** live pipeline 운영 감시를 Grafana-first로 구성한다. 이 문서는 Grafana 실행, DB 연결, Discord webhook 설정 승인이 아니다.

## 1. 결정

운영 UI는 Hermes dashboard가 아니라 Grafana를 우선 사용한다.

```text
Monitoring/UI = Grafana
Operational assistant = Hermes
Control plane = later FastAPI or CLI
```

Hermes는 alert 수신 후 원인 분석, runbook 실행 보조, worker orchestration에 사용한다. Grafana는 dashboard, alert rule, time range, panel refresh, threshold 표현을 담당한다.

## 2. Task 상세 설계

### Task A. Grafana datasource/provisioning skeleton

산출물:

```text
docker/grafana/provisioning/datasources/postgres_cms_live.yaml
docker/grafana/provisioning/dashboards/cms_live_dashboards.yaml
docker/grafana/provisioning/alerting/contact_points.yaml
docker/grafana/provisioning/alerting/live_pipeline_alerts.yaml
```

Acceptance criteria:

- datasource uid는 `postgres-cms-live`로 고정한다.
- DB password와 Discord webhook은 `${...}` placeholder만 사용한다.
- provisioning 파일은 repo에 저장하되 실제 Grafana 실행은 별도 승인 후 수행한다.

### Task B. PostgreSQL ops query contract

산출물:

```text
docs/qa/grafana_ops_query_contract.md
```

Acceptance criteria:

- `live.measurement_event`, `live.bucket_queue`, `qa.live_measurement_issue`, `ops.pipeline_latency_event`, `ops.worker_heartbeat`, `live.promotion_check` 기준 query를 정의한다.
- 모든 query는 read-only `SELECT`여야 한다.
- canonical write 또는 production mutation query를 포함하지 않는다.

### Task C. Grafana dashboard JSON 초안

산출물:

```text
docker/grafana/provisioning/dashboards/json/cms_live_pipeline_overview.json
```

Acceptance criteria:

- FastAPI ingest health, Kafka consumer lag, Kafka DLQ/produce errors, event freshness, queue backlog, blocking issue, stage latency, worker heartbeat, promotion readiness panel을 포함한다.
- dashboard는 PostgreSQL datasource uid `postgres-cms-live`를 참조한다.
- dashboard JSON은 `json.loads`로 검증 가능해야 한다.

### Task D. Grafana alert rule contract + Discord format

산출물:

```text
docker/grafana/provisioning/alerting/contact_points.yaml
docker/grafana/provisioning/alerting/live_pipeline_alerts.yaml
docs/qa/grafana_ops_query_contract.md
```

Acceptance criteria:

- P0/P1/P2 severity를 분리한다.
- Discord webhook URL은 `${GRAFANA_DISCORD_WEBHOOK_URL}` placeholder만 사용한다.
- no event, Kafka consumer lag, Kafka DLQ, Kafka produce error, FastAPI ingest 5xx, queue stuck, failed queue, worker heartbeat, latency p95 alert를 우선한다.
- P1 policy miss spike와 P2 non-blocking QA warning spike를 provisioning에 포함한다.

### Task E. worker heartbeat / latency table contract 보강

산출물:

```text
src/cms/contracts/observability.py
docs/qa/grafana_ops_query_contract.md
```

Acceptance criteria:

- `ops.worker_heartbeat`와 `ops.pipeline_latency_event`의 읽기 contract를 정의한다.
- latency metric은 pipeline stage별 p50/p95/max 계산이 가능해야 한다.
- worker heartbeat는 worker별 liveness와 last_error를 확인할 수 있어야 한다.

### Task F. independent review

Acceptance criteria:

- production/canonical write 없음.
- secrets 없음.
- Grafana가 monitoring UI, Hermes가 assistant, FastAPI/CLI가 future control plane이라는 boundary가 유지됨.
- provisioning과 query는 실제 DB 실행 전 review-only 상태로 남음.

## 3. Alert runbook

### P0 no live events for 5 minutes

1. FastAPI ingest 4xx/5xx와 request rate 확인.
2. Kafka `measurement_raw_v1` produce error와 consumer lag 확인.
3. `kafka_to_postgres_consumer` heartbeat 확인.
4. PostgreSQL `live.measurement_event` insert 지연 여부 확인.
5. source outage이면 ingestion 재시도 전에 Kafka retention/replay 가능성을 확인한다.

### P0 oldest pending queue age > 10 minutes

1. `live.bucket_queue`에서 oldest pending row 확인.
2. `job_kind`, `resolution`, `policy_version` 별 backlog 확인.
3. worker heartbeat와 last_error 확인.
4. retry 전에 동일 idempotency key 중복/blocked issue를 확인한다.

### P0 queue failed rows present

1. failed row의 `last_error`와 retry_count 확인.
2. 관련 `qa.live_measurement_issue` 확인.
3. 정책 오류인지 worker 오류인지 분리한다.
4. canonical/promotion 관련 side effect는 실행하지 않는다.

### P0 worker heartbeat missing

1. 해당 worker process/container 상태 확인.
2. worker logs 확인.
3. pending/running queue age 확인.
4. 재시작은 scratch/live runbook 승인 범위 안에서만 수행한다.

### P0 end-to-end p95 > 300 sec

1. stage latency p95 panel에서 병목 stage를 확인한다.
2. FastAPI, Kafka produce, Kafka consume-to-event, trigger, queue, mean rollup, peak branch, QA 중 어느 stage인지 분리한다.
3. row count가 줄었는지 latency만 증가했는지 분리한다.


### P0 Kafka consumer lag / DLQ / produce error

1. `measurement_raw_v1` topic lag와 `postgres-live-ingest` consumer group 상태를 확인한다.
2. `measurement_dead_letter_v1` 증가 원인이 validation poison message인지 producer/consumer 오류인지 분리한다.
3. FastAPI `kafka_produce_error_count`와 broker availability를 확인한다.
4. Offset commit은 DB transaction 또는 DLQ publish 성공 이후인지 확인한다.

### P0 FastAPI ingest 5xx spike

1. `/ingest/measurements` 5xx count와 p95 latency를 확인한다.
2. validation 4xx와 producer 5xx를 분리한다.
3. PostgreSQL direct write 또는 rollup/QA/promotion side effect가 route 안에서 실행되지 않는지 확인한다.

## 4. 실제 실행 gate

아직 실행하지 않는 항목:

```text
Grafana container start
PostgreSQL datasource 연결
Discord webhook 등록
AWS security group/port 변경
production DB query 실행
canonical write 또는 promotion
```

실행 전 필요한 승인:

```text
Grafana target host
PostgreSQL datasource host/port/db/user
secret 전달 방식, 대화 내 공유 금지
Discord webhook 존재 여부
network exposure 방식
```
