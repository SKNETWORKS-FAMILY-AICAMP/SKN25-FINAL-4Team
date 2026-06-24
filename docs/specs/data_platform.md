# Data Platform Contract

**갱신일:** 2026-06-23

**상태:** 운영 data platform 계약

**범위:** CMS의 source, Kafka live/backfill topic, PostgreSQL 상태 전이, QA evidence, canonical fact, mart output, reference data 경계 정의.

## 1. 목적

CMS data platform은 source event를 Kafka live/backfill stream으로 받아 PostgreSQL `live` 스키마의 `state ledger`에 적재하고, `worker`와 `scheduler`가 QA evidence와 승인 경계(Approval gate)를 거쳐 canonical fact와 mart output을 생성하는 파이프라인 아키텍처를 정의합니다.

```
Source / Replay Producer
-> Kafka measurement_live_v1 또는 measurement_backfill_v1
-> Kafka consumer
-> live.measurement_event
-> live 집계 후보 / bucket queue / promotion check
-> canonical.measurement_* 또는 mart.*
-> API / Dashboard / Report / RAG Consumer
```

`canonical.measurement_*` 테이블은 승인된 관측 사실(Observed fact) 데이터를 보관합니다. P-Max forecast, anomaly warning, model input feature, serving evidence는 `mart`, `ops`, `qa` 스키마 경계 내에서 독립적으로 관리합니다. Reference data는 비교·검증·warm-start 용도로 사용하며, 관측 기반의 observed canonical fact와 명확히 분리합니다.

## 2. Data layer 경계

| **Layer** | **역할** | **쓰기 기준** |
| --- | --- | --- |
| `source` | source file, replay source, live producer 등 원시 데이터 생성 | source manifest와 checksum을 기반으로 원천 보존 |
| Kafka live/backfill | event 전달과 replay 파이프라인 분리 | topic, key, offset, consumer group 기준으로 추적 |
| `live` | event ledger, aggregate 후보, queue, promotion check 수행 | consumer와 worker가 runtime 계약에 따라 write 수행 |
| `qa` | data quality issue 식별, promotion evidence 및 serving evaluation 데이터 관리 | QA worker와 서빙 작업 프로세스가 근거 자료 기록 |
| `canonical` | 승인 및 검증이 완료된 관측 측정 데이터 적재 | 승인 경계를 통과한 전용 승격 worker만 write 권한 소유 |
| `mart` | model-serving 입력/산출물 및 분석용 구체화된 산출물 관리 | 도메인별 전용 worker/scheduler가 source_refs와 실행 메타데이터를 포함하여 기록 |
| `ops` | worker heartbeat, 시스템 이벤트 로그, 추론 로그, 보고서 메타데이터 관리 | worker, scheduler, API 엔진이 시스템 운영 상태를 실시간 기록 |
| `reference` | 보정 및 재샘플링 처리된 기준 데이터와 외부 비교 소스 보관 | 데이터 비교, warm-start 및 벤치마크 컨텍스트 데이터로 제한적 활용 |

데이터 계층 간 이동 프로세스는 이벤트 ID, 버킷 키(Bucket key), `source_refs`, `run_id`, `promotion_id`, quality/근거 자료 필드를 기준으로 추적합니다. 시스템 설계 시 테이블 간 단순 복제 방식보다 상태 전이 이력과 출처 정보(Provenance)의 무결성 보존을 최우선으로 합니다.

## 3. 원천과 재생 입력

원천 데이터 입력 체계는 live 생산자(Live producer)와 재생 생산자(Replay producer)로 이원화하여 관리합니다.

| **입력 구분** | **역할** | **데이터 플랫폼 처리 기준** |
| --- | --- | --- |
| live producer | 운영 환경의 실시간 stream 이벤트 생성 | `measurement_live_v1` 토픽으로 데이터 publish |
| replay producer | 원천 archive 파일 또는 day cache 기반의 데이터 재생 | `measurement_live_v1` 또는 `measurement_backfill_v1` 토픽으로 데이터 publish |
| 보정·재샘플 기준 데이터 | 보정 및 재샘플링 처리된 표준 기준 데이터 제공 | `reference.*` 스키마 적재 및 모델 warm-start/맥락 레이어에서 참조 |
| 보고서/RAG 원천 | 보고서 text, energy document, 지식 베이스 아키텍처 원천 자료 | `ops.energy_doc`, knowledge 원천, Vector DB 계약에 따라 관리 |

원천 매니페스트(Source manifest)는 파일 경로, 원천 식별자, `meter_urn`, `measurement`, 시각 열, value 열, 시각 범위, row 수, gzip 유효성, checksum, `created_at` 수치를 포함합니다. Live producer는 `source_event_id`와 `raw_payload_hash`를 제공하여 데이터 중복 적재를 방지하고 멱등성 있는 쓰기(Idempotent write) 기준을 수립합니다.

## 4. Kafka topic 계약

운영 수집 topic은 live와 backfill 경로를 엄격히 분리하여 운용합니다.

| **토픽 명 (Topic)** | **주요 역할** | **소비자 그룹 (Consumer Group)** | **주요 소비자 (Consumer)** |
| --- | --- | --- | --- |
| `measurement_live_v1` | 실시간 및 재생 이벤트 stream 전송 | `postgres-live-ingest` | `kafka_live_consumer_pc1` |
| `measurement_backfill_v1` | 대규모 백필 전용 이벤트 stream 전송 | `postgres-backfill-ingest` | `cms-backfill-consumer-pc1` |
| `measurement_dead_letter_v1` | 유효성 검증 실패 및 Poison 메시지 격리 | 운영 policy 기준 적용 | live/backfill 전용 consumer |

Kafka 메시지 키는 `(meter_urn, measurement)` 조합으로 구성합니다. 비즈니스 멱등성은 `(source_system, source_event_id)` 기준 조합을 우선 적용하며, 고유 식별자가 누락된 경우 `(raw_payload_hash, meter_urn, measurement, event_ts)` 데이터를 조합하여 고유성을 식별합니다. Kafka topic, partition, offset 정보는 consumer 진행 상태 및 데이터 재생 이력을 추적하는 핵심 메타데이터로 기록됩니다.

이벤트 엔벨로프(Event envelope)의 공통 필드 명세는 다음과 같습니다.

```
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
producer_run_id
source_ref
```

consumer는 PostgreSQL 데이터베이스 트랜잭션 처리가 성공적으로 완료된 후 offset 커밋을 수행합니다. 유효성 검증에 실패한 이벤트는 DLQ(Dead Letter Queue) 토픽으로 write되는 동시에 장애 issue record를 기록하고 consumer 진행 상태를 갱신합니다. DB 쓰기 실패 및 예기치 못한 시스템 오류 발생 시에는 재시도가 가능한 상태(Retryable state)를 유지하도록 consumer를 제어합니다.

## 5. PostgreSQL 스키마 구성

AWS PostgreSQL `cms` DB 내의 데이터 플랫폼 스키마 가이드라인 및 배치 기준은 다음과 같습니다.

| **스키마 (Schema)** | **런타임 책임 범위** | **대표 테이블 (Table)** |
| --- | --- | --- |
| `live` | 수집 이벤트 원장 관리, 시간별 집계 후보 적재, 처리 큐 및 데이터 승격 근거 보관 | `measurement_event`, `measurement_1min`, `measurement_15min`, `measurement_1h`, `bucket_queue`, `promotion_check`, `measurement_policy` |
| `canonical` | 검증 및 승인이 완료된 정밀 관측 사실 데이터 적재 | `measurement_1min`, `measurement_15min`, `measurement_1h` |
| `mart` | `model-serving` 입출력 데이터 및 다운스트림 가공 분석 마트 보관 | `peak_feature_15min`, `pmax_forecast_15min`, `anomaly_feature_1h`, `anomaly_warning_1h` |
| `ops` | worker 상태 모니터링, 시스템 이벤트 로그, 추론 로그, 보고서/RAG 원천 메타데이터 기록 | `metric`, `worker_heartbeat`, `worker_event_log`, `pmax_log`, `anomaly_log`, `daily_report`, `weekly_report`, `monthly_report`, `energy_doc` |
| `qa` | 데이터 품질 검증 지표, 모델 평가 결과 및 서빙 검증 근거 관리 | `bad_row`, `live_issue`, `pmax_eval`, `anomaly_eval`, `serving_evidence`, `meter_tag` |
| `reference` | 오프라인 보정 가이드라인 및 재샘플링 데이터 보관 | `corrected_resampled_15min`, `corrected_resampled_1h` |

`timescaledb`, `vector`, `pg_stat_statements` extension은 운영 DB 핵심 기능으로 활성화됩니다. 하이퍼테이블 정책, 통합 데이터 흐름 감사 테이블 규칙 및 메타데이터 통합 구조 설계는 후속 `database.md` 문서의 스키마 계약 가이드라인을 엄격히 준수합니다.

## 6. `live.measurement_event` 계약

`live.measurement_event` 테이블은 Kafka consumer가 수신한 이벤트를 최초 적재하는 시스템 원장(`state ledger`)입니다.

```
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

본 테이블은 하위 차원의 시간 집계 테이블 및 품질 검증(QA evidence)의 원천 장부로 기능합니다. 원천 데이터에 대한 보정 작업이 필요할 경우 원본 row를 직접 수정하지 않고 보정 이벤트 추가, issue record 생성, 또는 데이터 승격 제외 근거 추가 방식을 적용합니다. 원본 이벤트 데이터는 데이터 계보 추적의 정합성을 위해 최초 상태를 영구히 유지합니다.

## 7. Policy와 bucket 상태

`live.measurement_policy` 테이블은 `(meter_urn, measurement, effective_from/effective_to)` 조합을 기준으로 수집 주기, 기대 포인트(Expected points), 집계 방식, 데이터 coverage 기준 및 canonical 승격 적격성 정의를 일원화하여 관리합니다.

```
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

Policy lookup 결과 데이터는 `live.measurement_event.policy_lookup_status`에 기록된 후 `qa.live_issue` 및 `live.promotion_check` 파이프라인으로 전동 연계됩니다. Policy miss, ambiguous policy, disabled policy 규칙 및 coverage block 조건 등은 승인 데이터 승격 관리 경계 제어 시 즉시 차단 및 식별됩니다.

## 8. Live 집계 테이블 계약

`live.measurement_1min`, `live.measurement_15min`, `live.measurement_1h` 테이블은 1차 집계가 수행된 Live 단계의 관측 후보 데이터입니다.

```
bucket_ts
resolution
meter_urn
measurement
value
unit
aggregation_policy
expected_points
observed_points
missing_points
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

`bucket_ts` 필드는 타겟 시간 버킷의 시작 시각(Start time)을 명시합니다. 15분 단위 버킷 T는 `[T, T + 15min)` 구간을 정의하며, 1시간 단위 버킷 T는 `[T, T + 1h)` 구간의 시간 범위를 정의합니다.

`value` 필드는 설정된 resolution과 aggregation policy에 따라 산출된 관측 집계 결과치입니다. Non-cumulative gauge 데이터의 15분/1시간 단위 기본 대표값은 mean 관측 집계 방식을 사용합니다. Peak value와 예측용 특성(Feature) 데이터는 본 레이어를 통하지 않고 `mart.peak_feature_15min` 테이블군에서 별도로 제어합니다.

## 9. Queue와 worker 상태

`live.bucket_queue` 테이블은 Rollup 작업, Peak feature 추출 작업, 데이터 승격 검증 작업을 비동기로 처리하기 위한 미처리 버킷(Dirty bucket) 제어 큐입니다.

```
queue_id
meter_urn
measurement
resolution
bucket_ts
job_kind
policy_id
policy_version
source_min_ts
source_max_ts
watermark_ts
status
attempt_count
last_error
locked_by
locked_at
created_at
updated_at
```

Queue 멱등성 제어를 위한 복합 키 구조는 다음과 같습니다.

```
(meter_urn, measurement, resolution, bucket_ts, job_kind, policy_version)
```

| **job_kind 식별자** | **입력 소스 데이터** | **최종 출력 타겟** |
| --- | --- | --- |
| `mean_rollup` | `live.measurement_1min`, policy 정보 | `live.measurement_15min`, `live.measurement_1h` |
| `peak_feature` | `live.measurement_1min`, policy 정보 | `mart.peak_feature_15min` |
| `promotion_check` | live 단계 집계 테이블 데이터, QA issue 데이터, policy 규약 데이터 | `live.promotion_check` |

worker 상태는 `pending`, `running`, `done`, `failed`, `blocked` 흐름으로 일관되게 관리합니다. 지연 수집 데이터(Late event)가 수신되면, worker는 해당 버킷 키를 가진 queue row를 다시 dirty 상태(`pending`)로 갱신하거나 policy version에 맞춘 재처리 기록을 남깁니다.

## 10. 승인 데이터 승격 계약

검증 및 승인이 완료되어 최종 영구 적재되는 canonical 스키마의 타겟 테이블군은 다음과 같습니다.

```
canonical.measurement_1min
canonical.measurement_15min
canonical.measurement_1h
```

Canonical row 공통 필드 명세는 다음과 같습니다.

```
bucket_ts
resolution
meter_urn
measurement
value
unit
aggregation_policy
expected_points
observed_points
missing_points
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

승인 데이터 승격 작업은 `live.promotion_check` 테이블의 승격 적격성(Eligibility) 검증 결과, 상위 워크플로우의 승인 로그, `promotion_id`, 그리고 데이터베이스 읽기 검증 근거 자료를 필수 요건으로 실행됩니다. Canonical 테이블군에는 최종 승인된 순수 관측 집계 데이터만 기록하며, 모델 특성, 예측, 경고, 서빙 근거 자료는 `mart`, `ops`, `qa` 타겟 스키마 영역에서 철저히 분리 보관합니다.

## 11. Mart와 model-serving 경계

`mart` 스키마는 `model-serving` 단계의 핵심 입출력 데이터 및 다운스트림 서비스 서빙을 위한 전용 구체화된 산출물을 통합 관리합니다.

| **경로** | **실행 주체** | **입력 소스 데이터** | **최종 출력 타겟** | **운영 로그 및 품질 평가 데이터** |
| --- | --- | --- | --- | --- |
| P-Max 특성 | `cms_peak_feature_worker` | `live.measurement_1min`, queue/policy 참조 데이터 | `mart.peak_feature_15min` | `source_refs`, `run_id`, `created_at` 메타데이터 기록 |
| P-Max 예측 | `pmax_scheduler` | `mart.peak_feature_15min` 피처 소스 데이터 | `mart.pmax_forecast_15min` | `ops.pmax_log`, `qa.pmax_eval`, `qa.serving_evidence` |
| 이상 감지 특성 | `cms-anomaly-feature-worker` | 관측 1h 원천 데이터 소스, policy/QA refs | `mart.anomaly_feature_1h` | `ops.worker_heartbeat`, `ops.worker_event_log`, `source_refs`, 입력 데이터 품질 지표 |
| 이상 감지 경고 | `anomaly_scheduler` | `mart.anomaly_feature_1h` 피처 소스 데이터 | `mart.anomaly_warning_1h` | `ops.anomaly_log`, `qa.anomaly_eval`, `qa.serving_evidence` |

`mart.peak_feature_15min` 테이블은 P-Max 분석 모델 구동을 위한 고정 입력 피처 명세서 역할을 수행합니다.

```
window_ts
meter_urn
measurement
mean_value
max_value
min_value
p95_value
p99_value
std_value
last_value
peak_ts
peak_value
observed_points
expected_points
coverage_ratio
source_file
run_id
created_at
```

`mart.pmax_forecast_15min` 테이블은 P-Max 모델의 예측 결과를 적재합니다. 데이터 계보 추적의 핵심 정합성 기준 데이터로 `base_ts`, `input_window_start_ts`, `input_window_end_ts`, `input_window_count`, `target_ts`, `forecast_value`, `source_peak_input_refs`, `loaded_at` 필드를 반드시 보존합니다.

`mart.anomaly_feature_1h` 및 `mart.anomaly_warning_1h` 테이블은 이상 탐지(Anomaly) 모델의 입출력 경계면을 형성합니다. `cms-anomaly-feature-worker`가 입력 특성을 구성하며, `anomaly_scheduler`가 이를 읽어 경고 산출물을 생성합니다. 경고 플래그 필드가 `warning_flag=true` 조건으로 적재된 row는 운영상의 정식 하이 레벨 경고 알람으로 해석합니다. `ops.anomaly_log` 및 `qa.anomaly_eval` 테이블을 활용하여 실행 이력 데이터와 평가 근거를 격리하여 보관합니다.

## 12. QA evidence 경계

품질 검증 데이터 아키텍처는 데이터 품질 지표, 승격 정합성 자격 요건 및 `model-serving` 품질 데이터 영역을 독립적으로 격리하여 구성합니다.

| **증적 영역 (Evidence)** | **런타임 제어 범위 및 역할 책임** |
| --- | --- |
| `qa.bad_row` | 원천 파싱 실패 레코드, 필드 포맷 유효성 검증 실패 및 페이로드 오염 데이터 격리 |
| `qa.live_issue` | 수집 policy miss, cadence mismatch 불일치 구간, coverage block 및 계보 issue 추적 |
| `live.promotion_check` | canonical 데이터 승격 단계 진입 전 row 단위 eligibility 검증 및 block reason 기록 |
| `qa.pmax_eval` | P-Max 예측치에 대한 통계 오차율 및 사후 예측 품질 정밀 평가 |
| `qa.anomaly_eval` | 이상 탐지 알람에 대한 오탐/미탐 비율 분석 및 탐지 모델 품질 평가 |
| `qa.serving_evidence` | P-Max 및 Anomaly 모델이 통합 산출한 최종 서빙 데이터의 실효성 사후 검증 증적 관리 |

계량값이 실제 0인 상태(`value=0`)와 미관측으로 인한 누락 상태(Missing observation)는 `observed_points`, `expected_points`, `missing_points`, `coverage_ratio`, `quality_code` 엔티티 필드 조합을 통해 명확하게 구분 정의합니다. 누락 구간 버킷은 1차 집계 레이어에서 `NULL` 또는 coverage 0 지표 계열로 표현하며, 보정·보간 처리가 가해진 데이터는 기준 데이터 및 corrected 출처 정보를 함께 필수 보존합니다.

## 13. Reference data 경계

Reference 레이어는 오프라인 환경에서 보정 및 재샘플링 처리가 완료된 정제 기준 시계열 데이터와 모델 벤치마크용 외부 비교 소스를 격리 적재합니다.

| **기준 데이터 소스** | **런타임 주요 활용처** | **출처 및 계보 관리 (Provenance)** |
| --- | --- | --- |
| `reference.corrected_resampled_15min` | 15분 단위 데이터 비교 검증, 모델 warm-start 가이드라인 및 피처 정합성 대조 | 원천 파일 명세 및 실행 메타데이터 연동 |
| `reference.corrected_resampled_1h` | 1시간 단위 데이터 비교 검증, 이상 탐지 warm-start 가이드라인 및 보고서 맥락 제공 | 원천 파일 명세 및 실행 메타데이터 연동 |
| paper-processed series | 도메인 표준 벤치마크 지표 비교, QA policy 유효성 검토 자료 활용 | paper 및 원천 tier 메타데이터 바인딩 |

Reference 스키마 테이블의 row는 관측 기반의 순수 사실을 다루는 canonical 데이터와 다른 계보 관리 기준을 적용하여 관리합니다. 보고서, 대시보드, RAG, 모델 warm-start 컨텍스트 단에서 기준 데이터 소스를 사용하는 경우 데이터 쿼리 범위 내에 원천_mode와 원천_테이블을 명시하는 것을 표준 제약으로 정의합니다.

## 14. Staging과 scratch 경계

스테이징(Staging) 및 샌드박스 테스트(Scratch) 쓰기 파이프라인 작업은 운영 테이블 오염 방지를 위해 run별 격리 구조를 엄격히 적용합니다.

```
staging_<run_id>.raw_events
staging_<run_id>.measurement_1min
staging_<run_id>.measurement_15min
staging_<run_id>.measurement_1h
staging_<run_id>.promotion_check
```

테스트 및 스테이징 실행 컨텍스트는 `run_id`, 작업 대상 객체 정보, 총 처리 row 수, 작업 종료 후 자원 정리 명령, 정합성 검증 조회 내역을 마스터 로그에 기록해야 합니다. Scratch 대상 식별자 명명 시에는 실제 운영 환경용 `run_id` 스타일 및 승인 데이터 명명 규칙의 오용을 철저히 금지합니다. 테스트 레이어의 가공 산출물을 실제 운영 canonical 테이블로 마이그레이션하고자 하는 경우, 반드시 표준 데이터 정책 유효성 검증, 품질 근거 자료 검사, 최종 승인 절차 및 통제된 승격 절차를 거쳐야 합니다.

## 15. 데이터 흐름 감사와 운영 관측

데이터 흐름 감사(Data-flow audit) 시스템은 아래에 명세된 파이프라인 모니터링 신호(Signal)들을 단일 트레이싱 컨텍스트로 결합하여 관리합니다.

| **감사 메트릭 지표** | **관측 메트릭 원천 소스** |
| --- | --- |
| Kafka lag | 토픽별 파티션 consumer group 오프셋 잔여량 (topic/group offset) |
| live 장부 최신성 | `live.measurement_event` 테이블의 최대 이벤트 발생 시각 및 최종 수집 시각 데이터 |
| queue 적체 | `live.bucket_queue` 테이블 내 작업 상태별 지표 및 최장 미처리 대기 시간 데이터 (oldest pending age) |
| 집계 최신성 | `live.measurement_15min`, `live.measurement_1h` 테이블의 최신 적재 시간 버킷 정보 (latest bucket) |
| 승격 최신성 | `live.promotion_check` 및 `canonical.measurement_*` 테이블의 최신 승격 시간 버킷 정보 (latest bucket) |
| mart 최신성 | `mart.peak_feature_15min`, `mart.pmax_forecast_15min`, `mart.anomaly_feature_1h`, `mart.anomaly_warning_1h` 최신 적재 상태 |
| worker 상태 | `ops.worker_heartbeat`, `ops.worker_event_log` 테이블 내 프로세스 라이브니스 지표 |

`ops.worker_heartbeat` 및 `ops.worker_event_log` 테이블 데이터는 worker 및 프로세스 상태 근거 데이터로 간주됩니다. `model-serving` 계층에서는 `pmax_scheduler`, `anomaly_scheduler`, `anomaly_feature_scheduler` 계열 worker 이름이 heartbeat와 이벤트 로그의 기준 식별자로 사용됩니다. 전체 파이프라인의 상태 스냅샷을 관리하는 통합 `ops.audit` 테이블 규약 명세는 후속 스냅샷 계약 문서에서 세부적으로 명세합니다.

## 16. 문서 연결

| **연결 스펙 명세서 경로** | **개별 규약 명세 및 범위** |
| --- | --- |
| `docs/specs/overview.md` | 전체 구조와 문서 연결 요약 및 하위 스펙 계약서 간의 종속 문서 연결 관계 정의 |
| `docs/specs/runtime.md` | PC1·PC2·PC3 및 AWS 인프라 환경 내 물리 컴포넌트 배치 구조와 worker/API 경계 명세 |
| `docs/specs/database.md` | AWS PostgreSQL의 상세 데이터 마이그레이션 스크립트 규칙, 테이블 제약 조건, 컬럼 엔티티 제약 사항 정의 |
| `docs/specs/measurement_processing_policy.md` | 수집 주기 기준(cadence), 시계열 시간 버킷 정규화 산식, 데이터 커버리지 산정, NULL 필드 처리 및 canonical 승격 기준 명세 |
| `docs/qa/qa_contract.md` | QA 품질 검증 증적 스키마 설계, 실시간 수집 장애, 부적합 로우 관리 규정 및 모델 서빙 성능 지표 명세 |
| `docs/specs/backend_frontend_api_contract.md` | Backend 및 Frontend API 레이어 전체 엔드포인트 상세 라우트 스펙 및 데이터 조회/쓰기 제어 권한 매핑 명세 |
| `docs/specs/llm_contract.md` | LLM, RAG 검색 레이어, Text-to-SQL 파이프라인의 데이터 참조 범위 설정 및 프롬프트 보안 안전 규약 정의 |
| `docs/specs/knowledge_db_contract.md` | 지식 베이스 아키텍처 구성을 위한 Vector DB 인덱싱 최적화 설계 및 비정형 외부 지식 소스 적재 기준 명세 |
| `docs/specs/ontology.md` | 도메인 온톨로지 개념 클래스 정보, 프로퍼티 관계 스키마 매핑 및 데이터 플랫폼 소스 레이어 간의 동기화 규칙 정의 |
