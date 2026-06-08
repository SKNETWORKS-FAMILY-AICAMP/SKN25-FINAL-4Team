# Data Platform Contract

**갱신일:** 2026-06-08
**상태:** 통합 data platform 기준
**범위:** 이 문서는 source archive, Kafka live ingestion buffer, PostgreSQL live/staging/candidate/canonical boundary, canonical branch, peak feature/model-serving branch, feature/mart boundary를 정의한다. MongoDB는 Phase 1 live ingestion path에서 제거되며, 과거 replay/debug 후보로만 언급한다.

## 1. 목적

Data platform의 목적은 observed source를 손상하지 않고 보존하면서, QA evidence와 approval을 거쳐서만 canonical fact로 승격하는 것이다.

```text
compressed source or live sensor event
-> source manifest or FastAPI ingestion
-> Kafka measurement_raw_v1
-> kafka_to_postgres_consumer
-> live.measurement_event ledger
-> live.measurement_1min + live.bucket_queue

Branch A: observed / canonical
-> mean_rollup_worker
-> live.measurement_15min / live.measurement_1h
-> QA/anomaly evidence
-> approval + controlled promotion
-> canonical.measurement_1min/15min/1h

Branch B: peak_feature / model-serving
-> peak_feature_worker
-> mart.peak_feature_15min / mart.peak_input_15min
-> Airflow model job + P-Max adapter/release loader
-> mart.pmax_forecast_15min
   + ops.pmax_forecast_inference_log
   + qa.pmax_forecast_evaluation
```

## 2. Data layer boundary

| Layer | 역할 | 쓰기 조건 |
|---|---|---|
| `source` | 원문 compressed archive와 source manifest | 원천 보존. 수정 금지 |
| `raw` | sparse observed event buffer | isolated run 또는 approved live buffer |
| `staging` | run별 bucket/candidate table | scratch/staging approval 범위 안에서 허용 |
| `candidate` | QA evidence와 promotion 후보 | canonical write 전 검토 대상 |
| `canonical` | 승인된 observed fact | 별도 approval과 controlled promotion 필요 |
| `reference` | corrected/resampled comparison | observed truth 대체 금지 |
| `mart` | model/service input과 forecast output | champion input/release policy 승인 후 생성 |

## 3. Source archive

- Live/replay observed input은 `*_harmonized.csv.gz`를 사용한다.
- `*_corrected_resampled_15min.csv.gz`, `*_corrected_resampled_1h.csv.gz`는 reference/comparison 전용이다.
- Source file path, meter, measurement, timestamp column, value column, row count, gzip validity를 manifest로 남긴다.

## 4. Kafka live ingestion contract

Phase 1 live ingestion buffer는 Kafka다. MongoDB `measurement_buffer`는 active live path에서 제거한다. MongoDB가 필요하면 과거 replay/debug cache 또는 isolated experiment로 별도 승인 후 사용하며, canonical store가 아니다.

| Topic | 역할 | 원칙 |
|---|---|---|
| `measurement_raw_v1` | FastAPI가 publish하는 observed raw measurement event stream | Kafka ingestion payload contract version. canonical schema version 아님 |
| `measurement_dead_letter_v1` | validation 실패, poison message 분리 | 정상 ingestion flow와 offset retry flow에서 분리 |

`measurement_raw_v1` payload는 최소한 다음 fields를 가진다.

```text
schema_version
source_system
source_event_id
meter_urn
measurement
event_ts
value_text
value_numeric
unit
received_at
raw_payload_hash
```

Kafka message key는 `(meter_urn, measurement)`를 포함한다. Business idempotency는 우선 `(source_system, source_event_id)`를 사용하고, source event id가 없으면 `(raw_payload_hash, meter_urn, measurement, event_ts)`로 fallback한다. Kafka topic/partition/offset은 consumer progress metadata이지 business idempotency key가 아니다.

`kafka_to_postgres_consumer`는 `live.measurement_event` insert transaction이 성공한 뒤 offset을 commit한다. Validation failure는 DLQ publish 성공 후 offset을 commit한다. DB transaction failure나 unexpected error는 offset을 commit하지 않는다.

## 5. PostgreSQL schema layout

| Schema | 역할 | 상태 |
|---|---|---|
| `reference` | source/reference metadata와 corrected comparison | target contract |
| `live` | 1개월 live streaming operational processing layer | approved live run 범위 안에서 허용. canonical 직접 write 금지 |
| `canonical` | 승인된 measurement facts | controlled promotion 대상 |
| `candidate` | review/promotion 후보 | target contract |
| `ops` | job, approval, audit, scheduler/inference metadata | target contract |
| `staging_<run_id>` | isolated replay/scratch schema | 테스트 실행 단위 |
| `qa` | QA issue, anomaly/evaluation evidence, promotion block record | QA worker와 review workflow의 evidence 대상 |
| `mart` | model/service input과 forecast output | champion input/release policy 후 생성 |

Target schema의 배포 상태는 환경별 read-only inventory로 확인한 뒤 별도 evidence에 기록한다.

## 6. Live processing schema contract

`live` schema는 1개월 live streaming run의 operational processing layer다. 이 layer는 canonical 직전의 serving/candidate/evidence layer이며, trigger가 canonical이나 mart에 직접 쓰지 않는다.

### 6.1 Table set

| Table | 역할 | canonical 후보 여부 |
|---|---|---|
| `live.measurement_event` | Kafka `measurement_raw_v1`에서 idempotent insert된 observed event ledger | no |
| `live.measurement_policy` | `(meter_urn, measurement)`별 cadence, aggregation, QA, queue policy | no |
| `live.measurement_1min` | 정책에 맞춰 정렬된 observed 1min bucket | yes, eligibility 후 |
| `live.bucket_queue` | 15min/1h mean rollup 및 peak feature worker용 dirty bucket queue | no |
| `live.measurement_15min` | mean observed 15min rollup. anomaly/QA/canonical 후보 | yes, eligibility 후 |
| `live.measurement_1h` | mean observed 1h rollup. anomaly/QA/canonical 후보 | yes, eligibility 후 |
| `live.promotion_check` | row-level pass/warn/block, block reason, eligibility result | no |
| `live.promotion_run` | promotion_id, target tables, counts, approval/audit summary | no |
| `qa.live_measurement_issue` | policy miss, stale state, coverage block, lineage block 등 issue record | no |
| `mart.peak_feature_15min` | peak prediction feature. `peak_value`, `peak_ts`, `max` 등 | no |
| `mart.peak_input_15min` | model input view/table. 15min feature와 rolling 1h peak features | no |
| `mart.pmax_forecast_15min` | P-Max adapter/release loader가 적재하는 15min forecast output | no |
| `ops.pmax_forecast_inference_log` | P-Max model job, input window, model/release version, run lineage log | no |
| `qa.pmax_forecast_evaluation` | P-Max forecast 품질/evaluation evidence | no |

### 6.2 `live.measurement_event` minimum fields

```text
event_id
source_event_id
meter_urn
measurement
event_ts
value_text
value_numeric
unit
source_layer
source_ref
ingested_at
received_at
raw_payload_hash
policy_lookup_status
kafka_topic
kafka_partition
kafka_offset
kafka_key
consumer_group
consumed_at
schema_version
```

`live.measurement_event`는 immutable ledger처럼 다룬다. event correction은 원 row 수정이 아니라 추가 event, issue, 또는 promotion exclusion evidence로 남긴다.

### 6.3 `live.measurement_policy` minimum fields

```text
policy_id
meter_urn
measurement
effective_from
effective_to
enabled
source_update_mode
cadence_group
source_native_interval_seconds
target_resolution_policy
value_policy
aggregation_policy
expected_points_policy
mean_rollup_enabled
peak_feature_enabled
coverage_threshold
max_state_hold_age_seconds
canonical_eligible
paper_policy_ref
policy_version
created_at
updated_at
```

Policy lookup key는 `(meter_urn, measurement, effective time)`이다. lookup이 없거나 중복되면 trigger는 `qa.live_measurement_issue`에 `policy_miss` 또는 `policy_ambiguous`를 남기고 canonical 후보 생성을 차단한다.

### 6.4 `live.measurement_1min`, `live.measurement_15min`, `live.measurement_1h` common fields

```text
bucket_ts
resolution
meter_urn
measurement
value
unit
aggregation_policy
expected_points
observed_points
gap_points  # existing column; interpreted as missing_points
coverage_ratio
mask_code
quality_code
quality_summary
provenance
source_event_ids
source_bucket_refs
source_run_id
policy_id
policy_version
lineage_key
updated_at
```

`live.measurement_15min`과 `live.measurement_1h`의 `value`는 mean observed rollup을 의미한다. Peak prediction용 `peak_value`나 `peak_ts`를 이 table의 대표값으로 저장하지 않는다.

### 6.5 `live.bucket_queue` contract

```text
queue_id
meter_urn
measurement
resolution          # 15min, 1h
bucket_ts
job_kind            # mean_rollup, peak_feature
policy_id
policy_version
source_min_ts
source_max_ts
watermark_ts
status              # pending, running, done, failed, blocked
attempt_count
last_error
locked_by
locked_at
created_at
updated_at
```

Idempotency key는 다음 조합을 사용한다.

```text
(meter_urn, measurement, resolution, bucket_ts, job_kind, policy_version)
```

`mean_rollup` job은 `resolution = 15min`과 `resolution = 1h`를 생성한다. `peak_feature` job은 기본적으로 `resolution = 15min`만 생성하고, 1h peak 정보는 `mart.peak_input_15min`의 rolling 1h feature로 관리한다. 별도 `mart.peak_feature_1h`는 reviewed model-input requirement가 생긴 뒤 추가한다.

### 6.6 Peak feature and P-Max model-serving boundary

Peak prediction branch는 canonical fact가 아니라 model feature/model-serving branch다.

```text
mart.peak_feature_15min
mart.peak_input_15min
mart.pmax_forecast_15min
ops.pmax_forecast_inference_log
qa.pmax_forecast_evaluation
```

`mart.peak_feature_15min` minimum fields는 다음과 같다.

```text
bucket_ts
meter_urn
measurement
peak_value
peak_ts
max_value
min_value
mean_value
std_value
coverage_ratio
valid_peak_window
source_bucket_refs
policy_id
policy_version
feature_version
updated_at
```

`mart.peak_input_15min`은 model input view/table이며, 최근 4개 15min bucket에서 rolling 1h peak features를 계산할 수 있다.

```text
rolling_1h_peak_value
rolling_1h_peak_ts
rolling_1h_mean_value
rolling_1h_valid_bucket_count
rolling_1h_coverage_ratio
```

Peak feature row는 `canonical.measurement_*`로 promotion하지 않는다.

P-Max inference input은 `mart.peak_input_15min`에서 15min 기준 288개 window를 확보한 뒤 96x22 input tensor로 구성한다. Window가 부족하거나 release policy가 없으면 forecast write 대신 skip/block evidence를 남긴다. P-Max는 streaming test target이 아니며, Airflow model job dry-run 또는 artifact replay로 검증한다.

`mart.pmax_forecast_15min` minimum fields는 다음과 같다.

```text
forecast_id
run_id
model_name
model_version
release_version
input_window_start_ts
input_window_end_ts
input_window_count      # expected 288
input_shape             # expected 96x22
target_ts
forecast_horizon_step
forecast_value
forecast_unit
confidence_summary
source_peak_input_refs
loaded_at
```

`ops.pmax_forecast_inference_log`는 model job과 loader lineage를 남긴다.

```text
run_id
job_id
model_name
model_version
release_version
input_window_count
input_shape
artifact_ref
status
started_at
finished_at
error_summary
```

`qa.pmax_forecast_evaluation`은 forecast 품질/evaluation evidence를 남긴다.

```text
evaluation_id
run_id
forecast_id
evaluation_window_start_ts
evaluation_window_end_ts
metric_name
metric_value
baseline_ref
quality_status
evidence_ref
created_at
```

## 7. Canonical measurement tables

활성 canonical target은 다음 세 가지다. Canonical branch는 observed 1min/15min/1h bucket을 QA/anomaly evidence와 approval/controlled promotion을 거쳐 적재하며, peak feature와 P-Max forecast output은 canonical promotion 대상이 아니다.

```text
canonical.measurement_1min
canonical.measurement_15min
canonical.measurement_1h
```

공통 column contract는 다음과 같다.

```text
bucket_ts
resolution
meter_urn
measurement
value
unit
aggregation_policy
expected_points
observed_points
gap_points  # existing column; interpreted as missing_points
coverage_ratio
mask_code
quality_code
quality_summary
provenance
source_event_ids
source_run_id
promotion_id
lineage_key
loaded_at
```

`canonical.measurement_5min`은 active canonical contract가 아니다. 5min output은 diagnostics/candidate로만 다루며, canonical화하려면 schema/contract 변경이 필요하다.

## 8. Staging and candidate boundary

Staging schema는 run별로 격리한다.

```text
staging_<run_id>.raw_events
staging_<run_id>.measurement_1min
staging_<run_id>.measurement_15min
staging_<run_id>.measurement_1h
staging_<run_id>.promotion_check
```

Staging은 전체 source를 받을 수 있지만 canonical promotion 대상은 `docs/specs/measurement_processing_policy.md`의 Option B eligibility를 통과해야 한다.

Promotion check는 다음 정보를 남긴다.

```text
bucket_ts
resolution
meter_urn
measurement
eligible
block_reason
source_update_mode
cadence_group
coverage_ratio
quality_code
paper_policy_ref
```

## 9. Reference and corrected boundary

Reference layer는 audit/comparison을 위한 것이다.

| Reference source | 허용 | 금지 |
|---|---|---|
| corrected/resampled files | QA comparison, model policy 검토 | observed canonical 대체 |
| issue-corrected values | provenance가 있는 reference record | provenance 없는 canonical promotion |
| paper-processed series | benchmark/comparison | source truth 주장 |

## 10. Feature and mart boundary

Feature는 canonical 또는 승인된 candidate/reference policy에서 생성한다.

| Feature source | 사용 조건 |
|---|---|
| `canonical.measurement_*` | observed fact 기반 feature |
| `reference.corrected_*` | reference label이 명시된 비교/실험 feature |
| `candidate` | service truth가 아닌 review/experiment feature |
| `mart.model_input_*` | champion model input policy 승인 후 생성 |
| `mart.peak_feature_15min` | peak prediction feature. canonical fact가 아니며 `peak_value`, `peak_ts`, rolling feature를 보존 |
| `mart.peak_input_15min` | peak model input view/table. 1h peak는 우선 rolling feature로 표현. P-Max 입력은 288개 15min window로 96x22 tensor 구성 가능 시에만 사용 |
| `mart.pmax_forecast_15min` | P-Max forecast output. canonical fact가 아니며 release loader 적재 대상 |
| `ops.pmax_forecast_inference_log` | P-Max Airflow model job과 release loader lineage/status log |
| `qa.pmax_forecast_evaluation` | P-Max forecast evaluation evidence. Streaming test target이 아님 |

Feature 문서는 아직 상세 feature engineering 문서가 아니라 data boundary의 일부로 관리한다.

## 11. Scratch/test safety

- Scratch run ID는 production-looking 이름을 사용하지 않는다.
- PostgreSQL scratch/staging write는 `staging_<run_id>` 또는 명시된 scratch schema에만 허용한다.
- Kafka Phase 1 scratch/local test는 실제 broker 없이 plain `KafkaEnvelope` fixture를 우선 사용한다. 실제 broker 또는 MongoDB debug cache는 별도 승인 후 사용한다.
- Test 종료 후 cleanup command와 row count evidence를 남긴다.

## 12. Verification

권장 local check는 다음과 같다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --with pytest --no-project python -m pytest -q
```

DB 관련 문서 주장은 AWS read-only inventory 또는 local scratch DB read-back evidence가 있을 때만 확정 표현을 사용한다.
