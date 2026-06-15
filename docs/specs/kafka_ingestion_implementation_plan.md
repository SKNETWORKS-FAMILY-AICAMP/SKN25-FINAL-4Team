# Kafka Ingestion Implementation Plan

> **For Hermes:** worker orchestration은 `db-pipeline-worker-orchestration` 기준으로 수행한다. 실제 Kafka/Grafana/DB 실행은 별도 승인 gate 이후에만 수행한다.

**목표:** 1개월 live run의 ingestion backbone을 `FastAPI -> Kafka -> PostgreSQL -> Grafana`로 전환하고, MongoDB buffer 역할을 제거한다.

**Architecture:** FastAPI는 얇은 ingestion gateway로서 payload validation과 Kafka publish ack까지만 담당한다. Kafka는 live event streaming buffer/replay log이며, `kafka_to_postgres_consumer`가 PostgreSQL `live.measurement_event`에 idempotent insert한 뒤 기존 common trigger/queue/worker boundary로 넘긴다. Grafana는 Phase 1부터 FastAPI, Kafka, PostgreSQL, worker latency/health를 관측한다.

**Tech Stack:** FastAPI skeleton, Kafka-compatible local broker, PostgreSQL live schema, Grafana provisioning, pytest/ruff, import-safe Python contracts.

**상태:** historical implementation plan / active runtime은 `runtime_architecture.md`와 `aws_runtime_topology.md` 기준
**갱신일:** 2026-06-04
**범위:** 과거 설계 문서와 local/import-safe implementation 준비 기록. Historical note: 현재 active runtime은 PC1~PC3 Kafka edge cluster, PC1 separated ingestion/backend APIs, PC1 3 Kafka-to-PostgreSQL consumers, PC3 model-serving workers, AWS DB plane 기준이다. Production DB write, canonical write, Kafka/Grafana container start, AWS 변경, secrets 조회/등록은 포함하지 않는다.

---

## 1. 확정 결정

```text
Phase 1 core path = FastAPI -> Kafka -> PostgreSQL -> Grafana
MongoDB role = removed from live ingestion path
S3 = deferred archive lane
Spark = deferred heavy processing/replay/feature lane
```

MongoDB 제거 이유:

```text
Kafka는 topic/partition/offset/consumer group/retention/replay/lag/DLQ를 기본 모델로 제공하는 streaming event log다.
MongoDB는 document inspection/cache에는 편하지만 consumer별 offset, lag, poison message, replay window, downstream fan-out을 별도 애플리케이션 로직으로 설계해야 한다.
현재 목표는 실시간 센서 이벤트의 운영 가능한 ingestion backbone이므로 Kafka를 live buffer로 채택한다.
```

`measurement_raw_v1` 의미:

```text
Kafka ingestion payload contract version이다.
canonical schema version이 아니다.
Breaking change는 measurement_raw_v2로 병행 전환한다.
Backward-compatible field addition은 measurement_raw_v1 안에서 허용할 수 있다.
```

---

## 2. Target architecture

```text
Sensor / simulator
-> FastAPI POST /ingest/measurements
-> Kafka topic measurement_raw_v1
-> kafka_to_postgres_consumer
-> PostgreSQL live.measurement_event
-> common trigger
   -> live.measurement_1min
   -> live.bucket_queue
   -> qa.live_measurement_issue
-> workers
   -> live.measurement_15min
   -> live.measurement_1h
   -> mart.peak_feature_15min
   -> optional helper projection mart.peak_input_15min
   -> live.promotion_check
-> Grafana dashboard + alerts
```

금지 경계:

```text
FastAPI direct PostgreSQL write 금지
FastAPI rollup/QA/promotion 금지
Kafka consumer canonical write 금지
Trigger mart/canonical/large rollup 금지
S3/Spark execution 금지
Production DB write 금지
secrets/.env read 금지
```

---

## 3. Worker graph

```text
A. Architecture/spec update              -> Orchestrator + himmel review
B. Kafka topic/payload contract          -> himmel
C. FastAPI ingestion contract/skeleton   -> stark
D. Kafka -> PostgreSQL consumer contract -> stark, himmel review
E. Grafana query/dashboard/alert update  -> himmel + frieren
F. Independent review                    -> fern
```

의존성:

```text
A -> B
B -> C
B -> D
D -> E
C/D/E -> F
```

---

## 4. Task A: Architecture/spec update

**Objective:** 기존 MongoDB buffer 기준 문서를 Kafka live ingestion 기준으로 정리한다.

**Files:**

- Modify: `docs/specs/runtime_architecture.md`
- Modify: `docs/specs/data_platform_contract.md`
- Modify: `docs/specs/measurement_processing_policy.md`
- Modify: `docs/qa/pipeline_latency_test_plan.md`
- Optional Modify: `docs/specs/diagrams/flow_00_overall_pipeline.mmd`
- Optional Modify: `docs/specs/diagrams/flow_01_database_pipeline.mmd`

**Steps:**

1. `runtime_architecture.md`의 live path를 다음으로 교체한다.

   ```text
   Sensor / FastAPI ingestion
   -> Kafka measurement_raw_v1
   -> kafka_to_postgres_consumer
   -> live.measurement_event
   -> common trigger
   -> live.measurement_1min + live.bucket_queue
   -> workers
   -> QA eligibility
   -> controlled promotion
   ```

2. `Data plane` 설명에서 `MongoDB raw buffer`를 제거하고 `Kafka streaming buffer`를 추가한다.
3. `mongo_to_postgres_ingest` worker row를 `kafka_to_postgres_consumer`로 변경한다.
4. `data_platform_contract.md`의 MongoDB contract section을 제거하거나 historical/deprecated note로 축소한다.
5. `live.measurement_event` upstream 설명을 `Kafka measurement_raw_v1`로 변경한다.
6. `measurement_processing_policy.md`에서 MongoDB raw buffer 기준을 Kafka raw event contract 기준으로 바꾼다.
7. `pipeline_latency_test_plan.md`의 MongoDB metrics를 Kafka metrics로 바꾼다.

**Acceptance criteria:**

- `measurement_buffer`, `source_to_mongo_sec`, `mongo_to_event_sec`, `mongo_to_postgres_ingest`가 active Phase 1 path에 남지 않는다.
- MongoDB가 필요하면 `deprecated` 또는 `not in Phase 1 live path`로만 언급된다.
- S3/Spark는 deferred lane으로만 언급된다.

**Verification:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python - <<'PY'
from pathlib import Path
active_docs = [
    Path('docs/specs/runtime_architecture.md'),
    Path('docs/specs/data_platform_contract.md'),
    Path('docs/qa/pipeline_latency_test_plan.md'),
]
for path in active_docs:
    text = path.read_text(encoding='utf-8')
    assert 'Kafka' in text or 'kafka' in text, path
    assert 'source_to_mongo_sec' not in text, path
    assert 'mongo_to_event_sec' not in text, path
print('kafka pivot docs ok')
PY
```

---

## 5. Task B: Kafka topic/payload contract

**Objective:** Kafka topic, key, payload, idempotency, DLQ, offset rule을 import-safe contract로 정의한다.

**Files:**

- Create: `src/cms/contracts/ingestion.py`
- Create: `tests/contracts/test_ingestion_contract.py`
- Modify: `src/cms/contracts/observability.py`
- Modify: `docs/specs/data_platform_contract.md`

**Contract constants:**

```python
MEASUREMENT_RAW_TOPIC = "measurement_raw_v1"
MEASUREMENT_DLQ_TOPIC = "measurement_dead_letter_v1"
KAFKA_CONSUMER_GROUP = "postgres-live-ingest"
KAFKA_MESSAGE_KEY_FIELDS = ("meter_urn", "measurement")
```

**Payload dataclass sketch:**

```python
@dataclass(frozen=True)
class MeasurementRawEvent:
    schema_version: str
    source_system: str
    source_event_id: str
    meter_urn: str
    measurement: str
    event_ts: str
    value_text: str | None
    value_numeric: float | None
    unit: str | None
    received_at: str
    raw_payload_hash: str
```

**Required functions:**

```python
def kafka_message_key(event: MeasurementRawEvent) -> str: ...
def idempotency_key(event: MeasurementRawEvent) -> tuple[str, ...]: ...
def validate_raw_event(event: MeasurementRawEvent) -> tuple[str, ...]: ...
def should_send_to_dlq(validation_errors: tuple[str, ...]) -> bool: ...
```

**Acceptance criteria:**

- key는 `meter_urn + measurement`를 포함한다.
- idempotency는 `source_system + source_event_id`를 우선 사용한다.
- `source_event_id`가 없거나 불안정한 경우의 fallback 기준을 문서화한다.
- invalid schema/timestamp/value/meter/oversized payload는 DLQ 후보로 분류된다.

**Verification:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q tests/contracts/test_ingestion_contract.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with ruff --no-project ruff check src/cms/contracts/ingestion.py tests/contracts/test_ingestion_contract.py
```

---

## 6. Task C: FastAPI ingestion contract/skeleton

**Objective:** FastAPI endpoint skeleton을 DB write 없이 Kafka producer injection 방식으로 추가한다.

**Files:**

- Modify: `src/cms/service/api.py`
- Create: `tests/service/test_ingest_api_contract.py`

**Route:**

```text
POST /ingest/measurements
```

**Allowed behavior:**

```text
payload validation
MeasurementRawEvent 생성
raw_payload_hash 생성 또는 확인
Kafka producer interface 호출
produce ack 확인
202 Accepted payload 반환
```

**Forbidden behavior:**

```text
PostgreSQL write
canonical write
worker execution
rollup/QA/promotion
real Kafka client import at module import time
```

**Producer protocol sketch:**

```python
class KafkaProducerLike(Protocol):
    def produce(self, *, topic: str, key: str, value: Mapping[str, object]) -> Mapping[str, object]: ...
```

**Response semantics:**

```text
valid + publish ack -> 202 accepted
validation failure -> 422-style contract payload
producer unavailable/failure -> 503-style contract payload
```

**Acceptance criteria:**

- `create_app()` import remains safe when FastAPI/Kafka library is not installed.
- Fallback `ApiSkeleton` route list includes `/ingest/measurements`.
- Tests use fake producer only.
- No DB/client side effects occur.

**Verification:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q tests/service/test_ingest_api_contract.py tests/service/test_api_contract.py
```

---

## 7. Task D: Kafka -> PostgreSQL consumer contract

**Objective:** 실제 DB 연결 전, consumer의 transaction/offset/DLQ/idempotency 규칙을 pure contract로 만든다.

**Files:**

- Create: `src/cms/data/stream_consumer.py`
- Create: `tests/data/test_stream_consumer.py`
- Modify: `src/cms/contracts/live_pipeline.py`
- Modify: `scripts/migrations/live_schema_draft.sql`

**Pure contract responsibilities:**

```text
Kafka message envelope parsing
raw event validation
PostgreSQL insert payload shape 생성
DLQ payload shape 생성
offset commit decision 생성
latency marker 생성
worker heartbeat payload 생성
```

**Offset rule:**

```text
DB transaction success -> commit offset
validation/DLQ success -> commit offset
DB transaction failure -> do not commit offset
unexpected error -> do not commit offset
```

**PostgreSQL metadata fields to add to live.measurement_event draft:**

```text
kafka_topic
kafka_partition
kafka_offset
kafka_key
consumer_group
consumed_at
schema_version
```

**Acceptance criteria:**

- duplicate idempotency key는 duplicate write가 아니라 idempotent no-op/update decision으로 분류된다.
- Kafka offset만 business idempotency key로 쓰지 않는다.
- DB success 전 offset commit decision이 나오지 않는다.
- invalid payload는 DLQ decision을 만든다.

**Verification:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q tests/data/test_stream_consumer.py tests/contracts/test_live_pipeline_contract.py
```

---

## 8. Task E: Grafana update for Kafka Phase 1

**Objective:** Grafana contract를 Kafka/FastAPI/PostgreSQL Phase 1 metrics로 갱신한다.

**Files:**

- Modify: `src/cms/contracts/observability.py`
- Modify: `docs/qa/grafana_ops_query_contract.md`
- Modify: `docs/qa/grafana_observability_plan.md`
- Modify: `docker/grafana/provisioning/dashboards/json/cms_live_pipeline_overview.json`
- Modify: `docker/grafana/provisioning/alerting/live_pipeline_alerts.yaml`
- Modify: `tests/verify/test_grafana_observability_contract.py`

**Latency metrics:**

```text
source_to_fastapi_sec
fastapi_to_kafka_sec
kafka_to_event_sec
event_to_1min_sec
event_to_queue_sec
one_min_to_15min_sec
one_min_to_1h_sec
one_min_to_peak_feature_sec
peak_feature_to_peak_input_sec
qa_eligibility_sec
promotion_ready_sec
end_to_end_sec
```

**Kafka metrics:**

```text
kafka_consumer_lag
kafka_dlq_count
kafka_produce_error_count
kafka_to_event_p95_sec
```

**FastAPI metrics:**

```text
fastapi_ingest_request_rate
fastapi_ingest_4xx_count
fastapi_ingest_5xx_count
fastapi_ingest_p95_ms
```

**Acceptance criteria:**

- MongoDB latency metric이 Phase 1 contract에서 제거된다.
- worker heartbeat expected list는 `kafka_to_postgres_consumer`를 포함하고 `mongo_to_postgres_ingest`를 제거한다.
- Grafana query는 read-only `SELECT`만 사용한다.
- Alert에 Kafka consumer lag와 DLQ count가 포함된다.

**Verification:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q tests/verify/test_grafana_observability_contract.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with ruff --no-project ruff check src/cms/contracts/observability.py tests/verify/test_grafana_observability_contract.py
python - <<'PY'
import json
from pathlib import Path
p = Path('docker/grafana/provisioning/dashboards/json/cms_live_pipeline_overview.json')
d = json.loads(p.read_text(encoding='utf-8'))
assert all(t['rawSql'].strip().upper().startswith('SELECT') for panel in d['panels'] for t in panel['targets'])
print('grafana dashboard select-only ok')
PY
```

---

## 9. Task F: Independent review

**Objective:** 구현 전환 후 경계/복잡도/운영 위험을 독립 검토한다.

**Reviewer:** `fern`

**Review checklist:**

```text
Kafka 도입 이유가 MongoDB 제거 결정과 일관되는가
measurement_raw_v1이 canonical version으로 오해되지 않는가
FastAPI가 DB write를 하지 않는가
Kafka consumer offset commit rule이 DB transaction 이후인가
idempotency key가 business key 기준인가
DLQ가 poison message를 정상 flow에서 분리하는가
Grafana monitoring이 Kafka/FastAPI/PostgreSQL stage를 모두 덮는가
canonical/promotion write가 승인 gate 없이 발생하지 않는가
S3/Spark가 Phase 1 실행 경로에 들어오지 않는가
```

Verdict:

```text
PASS
PASS_WITH_WARNINGS
BLOCK
```

---

## 10. Overall verification gate

구현 phase 완료 조건:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q tests/contracts/test_ingestion_contract.py tests/service/test_ingest_api_contract.py tests/data/test_stream_consumer.py tests/verify/test_grafana_observability_contract.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with ruff --no-project ruff check src/cms/contracts/ingestion.py src/cms/data/stream_consumer.py src/cms/service/api.py tests/contracts/test_ingestion_contract.py tests/service/test_ingest_api_contract.py tests/data/test_stream_consumer.py tests/verify/test_grafana_observability_contract.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q
```

No-go 조건:

```text
테스트 없이 Kafka/Grafana container start
.env/secrets 읽기
실제 PostgreSQL write
canonical write
AWS security group 변경
S3/Spark 추가 구현
```

---

## 11. Next execution proposal

다음 실행은 worker graph로 진행한다.

```text
1. himmel: Task A/B/E의 DB/Kafka/Grafana contract review packet
2. stark: Task B/C/D local import-safe skeleton + tests
3. frieren: Task A/E의 data QA/latency correctness review
4. fern: final independent review
```

Orchestrator는 worker 결과를 합쳐 문서와 skeleton을 검증하고, 실제 Kafka/Grafana 실행 gate는 별도로 요청한다.
