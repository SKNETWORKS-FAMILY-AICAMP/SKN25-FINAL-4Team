# Grafana Observability Plan

**갱신일:** 2026-06-10
**상태:** live stream readiness gate 문서 최신화 완료
**범위:** CMS 실시간 계측 pipeline을 실제 live stream으로 전환하기 전에 Grafana 운영 화면을 개량기 중심으로 재구성하는 계획이다. 이 문서는 dashboard/query/metadata 설계 계획이며, production DDL, canonical write, 외부 포트 개방, secret 변경 승인이 아니다.

## 1. 결정

운영 UI는 Grafana를 우선 사용한다.

```text
Monitoring/UI = Grafana
Operational assistant = Hermes
Control plane = later FastAPI or CLI
```

Grafana는 운영자가 현재 상태를 10초 안에 판단하는 화면이다. Hermes는 알림 이후 원인 분석, runbook 보조, worker orchestration을 담당한다. 실제 제어, 재시작, 삭제, promotion은 별도 approval/audit이 있는 CLI 또는 future control plane에서 수행한다.

## 2. 외부 dashboard 참고 결과

GitHub 공개 repository와 dashboard JSON을 확인해 다음 패턴을 반영한다.

| 참고 | 확인한 패턴 | CMS 적용 방향 |
|---|---|---|
| `chpro/fronius-grafana-dashboard` | `Power flow`, `Live Monitoring`, `Energy Usage`, `Autonomy`, `Self consumption`, `Expected yield`; stat, gauge, barchart, pie-style 요약 | 최상단에 live health와 power/energy summary를 분리하고, 생산/소비/외부망 흐름을 한눈에 보이게 한다. |
| `vavallee/ha-energy-dashboard` | `Whole-Home Power`, `Circuit Snapshot`, `EV Charging`, `Power Breakdown`; stat, gauge, bargauge, state-timeline, timeseries | 개별 설비·회로를 표 하나에 몰지 않고, `equipment_group`별 snapshot과 상세 drill-down을 분리한다. |
| `mujuzo/energyhub-grafana-dashboards` | Overview, decision analysis, devices, economy dashboard 분리; 대부분 timeseries 중심 | Runtime operations와 Test evidence를 계속 분리하되, live 운영 화면 안에서도 overview/device/detail row를 구분한다. |
| `PawelSpoon/EnergyMeter` | `Actual values`, `Phase A/B/C`, `All Phases`, `PV`; row section과 gauge/barchart 사용 | 전기 계량기는 전체 전력, 상별 값, PV/CHP/소비 계통을 별도 row로 나눈다. |
| IoT/MQTT/Grafana examples | MQTT/InfluxDB/Grafana 흐름에서 broker, ingest rate, device freshness를 함께 표시 | CMS는 FastAPI/Kafka/PostgreSQL 단계별 lag, DLQ, consumer heartbeat, device freshness를 같은 운영 콘솔에 둔다. |

채택하지 않을 패턴:

- 모든 meter-series를 한 panel에 텍스트로 펼치는 방식. 81 meter × 다수 measurement에서는 글자가 겹쳐 운영 화면이 망가진다.
- dashboard 수를 많이 늘려 탐색하게 만드는 방식. 운영 중에는 `CMS Runtime Operations`와 `CMS Test Gates / Evidence`의 작은 dashboard set을 유지한다.
- screenshot/vision 기반 검증. Grafana JSON, datasource query, Grafana API/log, PostgreSQL/Prometheus query로 검증한다.

## 3. 현재 상태와 문제

현재 active dashboard는 두 개다.

```text
docker/grafana/provisioning/dashboards/json/cms_runtime_operations.json
docker/grafana/provisioning/dashboards/json/cms_test_gates.json
```

`CMS Runtime Operations`는 live stream 준비 전 operator dashboard로 재구성되었고, 현재 34개 panel을 가진다.

```text
At-a-glance health
Ingest and consumer flow
Server and database capacity
Meter freshness drill-down
AWS run evidence
81-meter fleet overview
Equipment group snapshot
Selected meter drill-down
Kafka and consumer correctness
```

2026-06-10 readiness delta:

```text
24h test cleanup = completed before real live stream readiness review
operator dashboard = meter_urn first, measurement drill-down second
active table contract = AWS에 실제 존재하는 table만 active dashboard에서 직접 조회
target table contract = 미배포 table은 DDL review 이후 target/future section으로 격리
next gates = read-only AWS query validation, Grafana provision/health validation, operator UX approval
```

현재 AWS에 실제 존재하는 관측 table은 다음이다.

```text
live.measurement_event
ops.pipeline_metric
qa.bad_row
qa.meter_tag
mart.peak_feature_15min
mart.pmax_forecast_15min
ops.pmax_forecast_inference_log
qa.pmax_forecast_evaluation
qa.model_serving_evidence_packet
```

현재 AWS에 없는 table은 dashboard query에서 직접 참조하지 않는다.

```text
live.measurement_policy
qa.live_measurement_issue
ops.worker_heartbeat
ops.kafka_consumer_lag
ops.fastapi_ingest_metric
live.bucket_queue
live.promotion_check
```

남은 제한 / 주의점:

1. 개량기 fleet 상태를 `meter_urn / measurement` 단위로 상단에 바로 펼치면 너무 조밀하므로, active dashboard는 `meter_urn` 우선 요약 후 선택 meter의 measurement drill-down으로 이동한다.
2. 운영자는 먼저 81개 개량기 중 어느 설비군이 죽었는지 알아야 하므로, `equipment_group`, `meter_domain`, `anomaly_priority` aggregation을 우선 표시한다.
3. 개별 meter drill-down에는 metadata가 필요하지만 현재 DB에는 static meter registry table이 없다.
4. AWS에 없는 `qa.live_measurement_issue`, `live.measurement_policy`, `live.bucket_queue`, `ops.worker_heartbeat`, `ops.kafka_consumer_lag`, `ops.fastapi_ingest_metric`, `live.promotion_check`는 active dashboard query에서 제외하고 target contract로 분리한다.
5. 실제 live stream 전에는 `processed = inserted + duplicate + dlq`, `committed = processed`가 dashboard에서 0-delta로 보이는지 확인해야 한다.

## 4. 개량기 정보 모델

기준 source는 `docs/specs/meter_metadata.md`다.

Grafana에서 보여야 할 static metadata:

```text
meter_urn
meter_domain          electricity / thermal / weather
meter_role            consumption / production / thermal_flow / weather
equipment_group       grid_transformer / pv / chp / server_power / central_cooling ...
equipment_name
building_code         H1 / H2 / H3 / H4 / V / WeatherStation
sign_convention
anomaly_priority      1 / 2 / 3 / 4
redundancy_group
primary_meter_urn
redundant_meter_urn
source_basis
note
```

실제 live 상태는 `live.measurement_event`에서 계산한다.

```text
latest_event_ts
latest_consumed_at
consumed_age_sec
source_to_consume_sec
active_measurement_count
measurement_count_15m
rows_5m / rows_15m / rows_1h
policy_lookup_status
kafka_partition / kafka_offset
```

QA·tag 상태는 현재 AWS에 있는 table만 사용한다.

```text
qa.meter_tag.tag
qa.meter_tag.detail
qa.bad_row.reason
ops.pipeline_metric.run_id / stage / metric_name / metric_value
```

권장 저장 방식:

1. 즉시 개선 단계에서는 dashboard SQL 내부 `VALUES` CTE나 repo-generated JSON query fragment로 81개 meter registry를 주입한다.
2. 실제 운영 전에는 `reference.meter_registry` 또는 `ops.meter_registry` 같은 작은 static registry table을 DDL review 후 생성한다.
3. Grafana query는 registry를 `live.measurement_event`와 left join해서 "등록된 meter인데 최근 event 없음"을 표시한다.

DDL이나 registry load는 별도 승인 전에는 실행하지 않는다.

## 5. Dashboard 재구성안

### Row A. Command strip

목적: 10초 안에 live stream 전체 상태를 판단한다.

Panel:

```text
Live ingest status            stat       green/yellow/red
Events last 5m                stat       sparkline
Active meters last 5m         stat
Active series last 5m         stat
Critical stale meters         stat       anomaly_priority <= 2 기준
Kafka lag                     gauge
DLQ delta                     gauge
DB write freshness            gauge
Stream node CPU / DB disk     mini gauge
```

원칙:

- 숫자는 8개 이하로 유지한다.
- 빨간색은 운영자가 즉시 봐야 할 상태에만 사용한다.
- `active_meter_count`는 series 수가 아니라 `meter_urn` 수다.

### Row B. 81-meter fleet overview

목적: 글자 겹침 없이 전체 개량기 상태를 보여준다.

Panel:

```text
Meter fleet freshness by equipment group     bar gauge or grouped table
Meter fleet status timeline                  state-timeline or status-history
Priority 1/2 stale meters                    bar gauge
Domain split                                 stat/bar gauge: electricity, thermal, weather
```

표시 단위:

```text
meter_urn first
measurement second
```

이전처럼 `meter_urn / measurement` 200개 이상을 상단에 바로 펼치지 않는다. 상단은 81개 meter 또는 equipment_group aggregation만 보여준다.

Freshness class:

```text
active = latest consumed_at <= 5m
warm   = 5m < latest consumed_at <= 15m
stale  = latest consumed_at > 15m or NULL
silent = registered meter with no event in selected window
```

### Row C. Equipment group snapshot

목적: 개량기를 설비 맥락으로 이해한다.

Panel:

```text
Grid transformer status
Central cooling status
Server power status
PV / CHP production status
Thermal and weather status
```

각 group panel은 다음을 함께 표시한다.

```text
registered_meters
active_meters_5m
stale_meters
latest_consumed_age_max
events_15m
priority_max
```

전기 계량기는 production/consumption 부호 규약을 적용해 해석 label을 붙인다. 단, raw value 자체는 Grafana에서 수정하지 않는다.

### Row D. Selected meter drill-down

목적: 운영자가 특정 meter를 선택했을 때 필요한 모든 정보를 한 화면에 모은다.

Variables:

```text
$meter_domain
$equipment_group
$meter_urn
$measurement
$priority
```

Panel:

```text
Meter metadata card             table/stat
Latest values by measurement    table
Measurement trend               timeseries
Source-to-consume delay         timeseries/gauge
Kafka offset trail              table
Recent QA tags                  table from qa.meter_tag
Recent bad rows                 table from qa.bad_row
Redundancy pair comparison      timeseries/table
```

Metadata card fields:

```text
meter_urn
equipment_group / equipment_name
meter_role / meter_domain
building_code
sign_convention
anomaly_priority
redundant_pair
latest_event_ts
latest_consumed_at
active_measurements
```

### Row E. Data quality and coverage

현재 AWS에는 `live.measurement_1min/15min/1h`와 `live.measurement_policy`가 없으므로, 첫 단계는 event-level quality만 표시한다.

Phase 1 event-level panels:

```text
policy_lookup_status count
meter_tag by tag
bad_row reason count
source_to_consume p95
missing registered meters by event absence
```

Phase 2 rollup/policy table 생성 후 panels:

```text
coverage_ratio by meter/measurement
expected_points vs observed_points
missing_points by native cadence
low coverage meter list
15min/1h rollup freshness
```

중요 규칙:

- observed zero와 missing/NULL을 섞지 않는다.
- native cadence 없이 expected points를 hard-code하지 않는다.
- corrected/reference 값은 observed canonical처럼 표시하지 않는다.

### Row F. Kafka and consumer correctness

목적: 실제 live stream에서 중복, DLQ, lag, commit 상태를 바로 확인한다.

Panel:

```text
Kafka lag by partition                  bar gauge
Raw topic offset by partition           timeseries
Consumer processed / inserted / duplicate / dlq / committed    stat or table
Invariant: processed = inserted + duplicate + dlq              stat
Invariant: committed = processed                               stat
DLQ reason table                                                 table
Retry count                                                      stat/timeseries
```

24h test cleanup 완료 후 확인한 중복 기준을 dashboard에도 반영한다.

```text
distinct(kafka_topic, kafka_partition, kafka_offset)
distinct(business_idempotency_key)
distinct(raw_payload_hash)
distinct(meter_urn, measurement, event_ts, value_numeric)
```

중복은 "DB duplicate row"와 "consumer duplicate no-op"를 분리해 표현한다.

### Row G. Infrastructure and DB capacity

Panel:

```text
Stream node CPU / memory / disk / network
DB node CPU / memory / disk / network
PostgreSQL active connections
PostgreSQL deadlocks
PostgreSQL cache hit ratio
PostgreSQL temp files/bytes
Kafka broker up
Kafka under-replicated partitions, if available
```

목적은 ingest rate와 server load를 같은 시간축에서 비교하는 것이다.

## 6. Query contract 정리 계획

### Task 1. AWS 실존 table 기준으로 query contract 정리

Status: 문서와 local contract test 기준 완료. AWS read-only query 실행 검증은 실제 live stream 직전 gate로 남긴다.

Files:

```text
docs/qa/grafana_ops_query_contract.md
tests/verify/test_grafana_observability_contract.py
```

작업:

- `qa.live_measurement_issue` 직접 참조를 제거하거나 future-only section으로 이동한다.
- `qa.meter_tag`, `qa.bad_row`, `ops.pipeline_metric` 기준 query를 primary로 둔다.
- `live.measurement_policy`, `ops.worker_heartbeat` 등 미배포 table은 target contract로 구분한다.

Verification:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q tests/verify/test_grafana_observability_contract.py
```

### Task 2. Meter registry source 생성 방식 결정

Files:

```text
docs/specs/meter_metadata.md
scripts/verify/ 또는 scripts/migrations/ 신규 draft
```

선택지:

| 방식 | 장점 | 단점 | 권장도 |
|---|---|---|---|
| SQL `VALUES` CTE | 즉시 적용 가능, DDL 없음 | query가 길고 유지보수 어려움 | 임시 |
| `reference.meter_registry` | Grafana join이 단순, 운영 친화적 | DDL/load 승인 필요 | 권장 |
| `ops.meter_registry` | ops dashboard와 가까움 | metadata와 ops metric의 의미가 섞임 | 보통 |
| JSON datasource | DB DDL 불필요 | 현재 provisioning/권한 복잡도 증가 | 낮음 |

권장: `reference.meter_registry`를 migration draft로 만들고, 적용은 별도 승인 후 진행한다.

### Task 3. Runtime dashboard layout 재작성

Status: active dashboard JSON은 이미 `meter_urn` 우선 fleet overview, 선택 meter measurement drill-down, Kafka/consumer invariant row를 포함한다. 이 문서 최신화 작업에서는 dashboard JSON을 추가 수정하지 않는다.

File:

```text
docker/grafana/provisioning/dashboards/json/cms_runtime_operations.json
```

작업:

- 상단 `Meter-series latest ingest status`를 meter-level fleet overview로 교체한다.
- 상세 series 표는 `$meter_urn` 선택 이후 drill-down row로 내린다.
- `equipment_group`, `meter_domain`, `anomaly_priority` 기반 grouping을 추가한다.
- Kafka/consumer invariant row를 추가한다.

Verification:

```bash
python -m json.tool docker/grafana/provisioning/dashboards/json/cms_runtime_operations.json >/dev/null
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q tests/verify/test_grafana_observability_contract.py
```

### Task 4. AWS query validation

대상:

```text
PostgreSQL datasource queries
Prometheus queries
Grafana provisioning logs
```

검증:

```text
PostgreSQL EXPLAIN with Grafana macro substitution
Prometheus query HTTP API non-empty check
Grafana health = 200
finished to provision dashboards
no duplicate UID
no /api/ds/query status=400 after dashboard access
```

### Task 5. 실제 live stream 전 acceptance gate

실제 live stream 시작 전 필요한 Grafana gate:

```text
DONE(local contract): active dashboard가 AWS 실존 table 위주 query와 Prometheus query만 사용
DONE(local contract): meter_urn-first fleet overview와 selected meter measurement drill-down panel 존재
DONE(local contract): consumer invariant panel이 processed/inserted/duplicate/dlq/committed 지표를 사용
PENDING(AWS read-only): active_meter_count panel 정상
PENDING(AWS read-only): critical_stale_meter_count panel 정상
PENDING(AWS read-only): Kafka lag by partition 정상
PENDING(AWS read-only): DLQ delta 정상
PENDING(AWS read-only): DB write freshness 정상
PENDING(AWS/Grafana): meter drill-down variables 정상
PENDING(AWS read-only): 최근 5분 event query 정상
PENDING(Grafana): Grafana status=400 없음
PENDING(operator): Viowlet 화면 확인/UX 승인
```

## 7. 실제 live stream 전 적용 순서

1. DONE: `live.measurement_event` 24h test cleanup 완료 상태를 readiness 문서에 반영한다.
2. DONE: dashboard/query contract를 AWS 실존 table 기준으로 정리한다.
3. DONE(local): meter registry 임시 방식은 dashboard SQL 내부 `meter_registry` CTE로 둔다.
4. DONE(local): DDL 없이 가능한 dashboard 개선을 local JSON에 반영한다.
5. DONE(local): local contract test를 통과시킨다.
6. NEXT: AWS PostgreSQL/Prometheus query를 read-only로 검증한다.
7. DONE: AWS Grafana provisioning path에 backup 후 적용했다. Backup: `grafana/provisioning/dashboards/archive/applied_backups/20260610T010309Z`.
8. NEXT: Grafana health/log/query status를 확인한다.
9. NEXT: Viowlet가 화면을 확인해 operator UX를 승인한다.
10. 그 뒤 실제 live stream을 시작한다.

## 8. Alert runbook

### P0 no live events for 5 minutes

1. FastAPI ingest 5xx와 4xx를 먼저 분리한다.
2. Kafka consumer lag와 broker 상태를 확인한다.
3. PostgreSQL `live.measurement_event`의 latest consumed age를 확인한다.
4. source outage인지, API/Kafka/consumer/DB 병목인지 분리한다.

### P0 Kafka consumer lag / DLQ

1. `measurement_raw_v1` partition별 lag를 확인한다.
2. DLQ delta와 retry count를 확인한다.
3. `processed = inserted + duplicate + dlq`, `committed = processed` invariant를 확인한다.
4. duplicate는 DB 중복 row와 consumer idempotent no-op을 분리해 해석한다.

### P0 FastAPI ingest 5xx

1. `/ingest/measurements` 5xx 발생 시 Kafka produce error와 API log를 함께 본다.
2. validation 4xx는 source payload 문제로 분리한다.
3. FastAPI route 안에서 PostgreSQL direct write, rollup, QA, promotion side effect가 실행되지 않는지 확인한다.

## 9. 금지 사항

```text
canonical.* write
mart/model table mutation without approval
production DDL without approval
secret 출력
Kafka topic purge without explicit approval
Grafana dashboard 수 무분별 증가
meter-series 전체 텍스트를 상단 panel에 직접 표시
```

## 10. 성공 기준

Grafana 개선은 다음 조건을 만족해야 완료로 본다.

```text
1. 운영자가 첫 화면에서 live stream 정상/비정상을 판단할 수 있다.
2. 81개 meter 상태가 글자 겹침 없이 보인다.
3. equipment_group과 anomaly_priority 기준으로 어느 설비가 문제인지 보인다.
4. 선택한 meter의 metadata, latest event, latest consumed, measurement trend, QA tag를 확인할 수 있다.
5. Kafka lag, DLQ, retry, duplicate, commit invariant가 같은 dashboard에서 보인다.
6. AWS에 없는 table을 active dashboard query가 참조하지 않는다.
7. Grafana contract test와 AWS read-only query validation 완료이 통과한다.
8. Viowlet가 화면을 보고 live stream 전 운영 콘솔로 충분하다고 승인한다.
```

## 13. 2026-06-10 AWS apply evidence

- PostgreSQL dashboard SQL: 26 queries executed in `BEGIN READ ONLY` and ended with `ROLLBACK`.
- Prometheus dashboard expressions: 10 expressions returned `status=success`.
- Grafana remote JSON: `cms_runtime_operations` 34 panels, `cms_test_gates` 11 panels.
- Grafana health: database `ok`, version `11.5.2`.
- Recent Grafana logs after empty `plugins/` and `alerting/` directory creation: no `status=400`, `level=error`, duplicate UID, or provisioning error.
- Remaining gate: operator UX approval in browser.

## 14. 2026-06-14 service-start connectivity evidence

- Grafana health endpoint: `/api/health` returned HTTP 200, database `ok`, version `11.5.2`.
- Grafana API auth blocker: `.env` had `SKN25_PC2_GRAFANA_URL` but no Grafana token/API key or admin auth key; unauthenticated `/api/datasources` returned HTTP 401. Use Prometheus HTTP, provisioning JSON, and PostgreSQL report query evidence until auth is supplied.
- Prometheus on PC2: `/-/ready` returned ready; `max(pg_up)` returned `status=success`, `series=1`, `value=1`; active target check showed one postgres-exporter target `up`.
- PC3 postgres-exporter: `cms-postgres-exporter` container running and local `/metrics` exposed `pg_up 1`.
- PostgreSQL report query: active service-start tables were present for `live.measurement_event`, `ops.pipeline_metric`, `qa.meter_tag`, `qa.bad_row`, `mart.peak_feature_15min`, `mart.pmax_forecast_15min`, `ops.pmax_forecast_inference_log`, and `qa.model_serving_evidence_packet`; query ran in `BEGIN READ ONLY`/`ROLLBACK`.
- Airflow runtime: local `cms-airflow-standalone` registered `daily_report` unpaused plus manual paused `model_serving_pipeline`; `model_serving_pipeline` had 7 expected tasks. Local metadata had no independent model-serving DAG run to list, so prior orchestrator-only success remains a channel blocker until a shared Airflow run id is provided or a new manual no-write fixture run is triggered.
