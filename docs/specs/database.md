# Database Contract

**갱신일:** 2026-06-23

**상태:** 운영 PostgreSQL 카탈로그 기반 계약

**범위:** AWS PostgreSQL `cms` DB의 schema, table, key column, write 주체, provenance, 운영 규모 정의. 데이터 흐름과 실행 배치는 `data_platform.md`, `runtime.md`에서 기술하며, bucket 산정과 coverage 규칙은 `measurement_processing_policy.md`, QA evidence 명세는 `qa_contract.md`에서 상세 정의.

## 1. 목적

CMS 데이터베이스 계약은 Kafka live/backfill 이벤트가 PostgreSQL `live` 스키마의 `state ledger`에 적재된 후, `worker`와 `scheduler`가 `canonical`, `mart`, `ops`, `qa`, `reference` 경계로 상태를 전이하는 정합성 기준을 정의합니다.

데이터베이스 구조는 관측 사실 데이터(Observed fact), `model-serving` 산출물(`mart output`), 시스템 운영 상태, QA evidence, reference data 영역으로 상호 격리됩니다. `canonical.measurement_*`는 승인 및 검증이 완료된 관측 사실 데이터를 보관하며, P-Max 및 Anomaly 분석 모델의 입력 feature와 forecast/warning 데이터는 `mart`, `ops`, `qa` 스키마 경계 내에서 독립적으로 관리합니다.

## 2. 운영 Catalog 요약

| **항목** | **설정 및 스펙 제약 값** |
| --- | --- |
| 대상 Database | `cms` DB |
| DBMS 엔진 버전 | PostgreSQL 16.14 |
| 활성 실행 스키마 | `live`, `canonical`, `mart`, `ops`, `qa`, `reference` |
| 영구 보관 스키마 | `archive` |
| 가용 데이터베이스 익스텐션 | `timescaledb` 2.27.1, `vector` 0.8.1, `pg_stat_statements` 1.10 |
| 하이퍼테이블 정책 | `timescaledb` 익스텐션을 활용하는 구조이며 세부 `hypertable` 분할 가이드라인은 독자적인 migration 관리 경계에서 제어 |

각 테이블의 로우(Row) 수와 용량 수치는 `pg_stat_user_tables.n_live_tup`, `pg_class.reltuples`, `pg_total_relation_size` 통계를 기반으로 계착한 운영 규모입니다. 데이터베이스 성능 보장을 위해 대용량 table 조회 시 전체 count scan 연산을 배제하고 시스템 catalog 통계를 기준으로 관리합니다.

## 3. 스키마 경계

| **스키마 (Schema)** | **핵심 역할 및 책임 범위** | **주요 쓰기 주체 (Writer)** | **대표 테이블 (Table)** |
| --- | --- | --- | --- |
| `live` | Kafka 수집 이후 event ledger 기록, 시간별 집계 후보 적재, 처리 큐 및 데이터 승격 근거 보관 | Kafka consumer, rollup worker, promotion checker | `measurement_event`, `measurement_1min`, `measurement_15min`, `measurement_1h`, `bucket_queue`, `promotion_check`, `measurement_policy` |
| `canonical` | 승인 경계를 통과하여 신뢰성이 입증된 정밀 관측 사실 데이터 적재 | 제어 권한을 소유한 전용 승격 worker | `measurement_1min`, `measurement_15min`, `measurement_1h` |
| `mart` | `model-serving` 단계의 핵심 입력 feature 및 forecast/warning 최종 산출물 격리 적재 | feature worker, `pmax_scheduler`, `anomaly_scheduler` | `peak_feature_15min`, `pmax_forecast_15min`, `anomaly_feature_1h`, `anomaly_warning_1h` |
| `ops` | runtime 상태 모니터링, worker heartbeat/event 이력, model log, 리포트 및 RAG 원천 메타데이터 기록 | worker, scheduler, API 엔진, Airflow DAG | `worker_heartbeat`, `worker_event_log`, `pmax_log`, `anomaly_log`, `daily_report`, `weekly_report`, `monthly_report`, `energy_doc` |
| `qa` | data quality issue 식별, 서빙 파이프라인 검증 근거 및 분석 모델 평가 지표 관리 | QA worker, 서빙 파이프라인 프로세스, evaluation worker | `bad_row`, `live_issue`, `pmax_eval`, `anomaly_eval`, `serving_evidence`, `meter_tag` |
| `reference` | 오프라인 환경에서 보정 및 재샘플링 처리된 기준 데이터와 외부 비교 소스 보관 | reference data loader | `corrected_resampled_15min`, `corrected_resampled_1h` |

## 4. 상태 전이 계약

```
Kafka measurement_live_v1 또는 measurement_backfill_v1
-> live.measurement_event
-> live.measurement_1min / live.measurement_15min / live.measurement_1h
-> live.bucket_queue / live.promotion_check
-> canonical.measurement_1min / canonical.measurement_15min / canonical.measurement_1h

mart.peak_feature_15min -> mart.pmax_forecast_15min -> ops.pmax_log / qa.pmax_eval / qa.serving_evidence
mart.anomaly_feature_1h -> mart.anomaly_warning_1h -> ops.anomaly_log / qa.anomaly_eval / qa.serving_evidence
```

파이프라인 상태 전이 프로세스는 source event ID, 비즈니스 멱등성 key, bucket key, policy ID/version, `source_refs`, `run_id`, `promotion_id`, QA evidence JSON 스냅샷 필드를 기준으로 명확하게 추적합니다. 쓰기 트랜잭션 성공 여부는 target table read-back 유효성 검증, 최종 처리 row 수, timestamp horizon 및 provenance 필드를 유기적으로 결합하여 최종 검증합니다.

## 5. 공통 키와 필드 규칙

| **구분 항목** | **매핑 엔티티 열 / 객체** | **세부 제약 규약 규칙** |
| --- | --- | --- |
| 이벤트 식별 | `event_id`, `business_idempotency_key`, `source_event_id`, `raw_payload_hash` | Kafka event 데이터와 데이터베이스 row 간의 고유 멱등성 write 제어 기준 |
| Measurement 키 | `meter_urn`, `measurement` | 데이터 원천, live bucket, canonical fact, mart feature 레이어를 상호 맵핑하는 마스터 join key |
| 시각 키 | `event_ts`, `bucket_ts`, `window_ts`, `target_ts`, `forecast_origin_ts` | source event 발생 시각, bucket 시작 시점, feature window 구간, 예측 대상 타겟 및 추론 origin 시각의 분리 관리 |
| 정책 키 | `policy_id`, `policy_version`, `aggregation_policy`, `expected_points_policy` | 시간 버킷 산정 산식 및 canonical 승격 적격성 판정을 위한 versioned 제어 기준 |
| 품질 키 | `expected_points`, `observed_points`, `missing_points`, `coverage_ratio`, `mask_code`, `quality_code`, `quality_summary` | 순수 gauge value=0 지표 데이터와 미관측 누락 구간 버킷을 식별하기 위한 품질 정량 지표 규약 |
| 출처 정보 | `provenance`, `source_refs`, `source_event_ids`, `source_bucket_refs`, `source_run_id`, `run_id` | 데이터 source lineage tracking, worker runtime execution 이력, 모델 입력셋 구조, 레포트/증적 아티팩트 연계 추적 |
| 운영 상태 | `status`, `heartbeat_at`, `event_at`, `started_at`, `completed_at`, `finished_at` | worker 생존 상태, scheduler 루프 주기, 비동기 태스크 status 및 최종 `model-serving` 결과 기록 |

## 6. live 스키마 계약

`live` schema는 Kafka event ledger와 실시간 관측 집계 후보 데이터를 보관합니다. Worker 프로세스는 `bucket_queue` 테이블을 활용하여 처리 대상 bucket을 선점 및 격리하며, `promotion_check` 테이블은 canonical 스키마 승격 프로세스 진입 전의 최종 QA evidence를 보관합니다.

| **테이블 명 (Table)** | **역할 범위** | **쓰기 주체** | **핵심 엔티티 열 (Column)** | **행 수 (Tup)** | **용량 (Size)** | **비고 및 실행 제약** |
| --- | --- | --- | --- | --- | --- | --- |
| `live.measurement_event` | Kafka 이벤트 실시간 원장 | `kafka_live_consumer_pc1`, `cms-backfill-consumer-pc1` | `event_id`, `business_idempotency_key`, `source_event_id`, `meter_urn`, `measurement`, `event_ts`, `value_text`, `value_numeric`, `source_layer`, `kafka_topic`, `kafka_partition`, `kafka_offset`, `consumer_group`, `policy_lookup_status` | 10,972,952 | 14 GB | consumer 트랜잭션 커밋 성공 후 카프카 offset 커밋 수행의 정합성 기준 |
| `live.measurement_1min` | 1분 단위 관측 버킷 후보 | 집계 및 equalization worker | `bucket_ts`, `resolution`, `meter_urn`, `measurement`, `value`, `expected_points`, `observed_points`, `missing_points`, `coverage_ratio`, `policy_id`, `policy_version`, `lineage_key` | 6,445,357 | 4242 MB | 관측 파이프라인 전반을 관통하는 기초 단의 최소 단위 bucket |
| `live.measurement_15min` | 15분 단위 관측 집계 후보 | `cms_live_mean_rollup_worker` | `bucket_ts`, `resolution`, `meter_urn`, `measurement`, `value`, `expected_points`, `observed_points`, `missing_points`, `coverage_ratio`, `source_bucket_refs`, `policy_id`, `policy_version` | 494,585 | 722 MB | Canonical 15분 마스터 테이블 후보 데이터 및 mart 입력 피처 생성의 소스 |
| `live.measurement_1h` | 1시간 단위 관측 집계 후보 | `cms_live_mean_rollup_worker` | `bucket_ts`, `resolution`, `meter_urn`, `measurement`, `value`, `expected_points`, `observed_points`, `missing_points`, `coverage_ratio`, `source_bucket_refs`, `policy_id`, `policy_version` | 124,907 | 240 MB | Anomaly 입력 피처 테이블 및 canonical 1시간 마스터 테이블 후보 데이터의 소스 |
| `live.bucket_queue` | 비동기 워커 태스크 제어 큐 | 수집 consumer, 집계 scheduler, 특성 worker | `queue_id`, `meter_urn`, `measurement`, `resolution`, `bucket_ts`, `job_kind`, `status`, `attempt_count`, `last_error`, `locked_by`, `locked_at`, `created_at`, `updated_at` | 707,785 | 267 MB | pending, running, done, failed 상태 트래킹 및 오류 백오프 재시도 로직 제어 |
| `live.measurement_policy` | 계량기별 비즈니스 가이드라인 | 정책 마스터 시드 로더, 시스템 migration 스크립트 | `policy_id`, `meter_urn`, `measurement`, `effective_from`, `effective_to`, `enabled`, `source_update_mode`, `cadence_group`, `source_native_interval_seconds`, `target_resolution_policy`, `value_policy`, `aggregation_policy`, `expected_points_policy`, `coverage_threshold`, `canonical_eligible`, `policy_version` | 1,605 | 960 kB | 데이터 coverage threshold 산정 및 canonical 승격 자격 요건 판정의 마스터 데이터 |
| `live.promotion_check` | 데이터 승격 자격 증적 관리 | 데이터 승격용 verification checker | `check_id`, `source_table`, `meter_urn`, `measurement`, `resolution`, `bucket_ts`, `policy_id`, `policy_version`, `eligibility_status`, `block_reasons`, `evidence`, `checked_at` | 6,310,623 | 2231 MB | canonical 스키마 승격 실행 전 row 단위 품질 유효성 사후 검증 QA evidence |
| `live.promotion_run` | 승격 파이프라인 실행 배치 원장 | 제어 권한을 소유한 전용 승격 worker | `promotion_id`, `approval_id`, `target_tables`, `source_window`, `row_counts`, `status`, `requested_by`, `created_at`, `completed_at` | 0 | 16 kB | 상위 워크플로우 승인 게이트 상태 체킹 및 실제 데이터 처리 row 수 영구 보관 |

## 7. canonical 스키마 계약

`canonical` 스키마는 승인 및 검증이 완결된 신뢰할 수 있는 관측 사실(Observed fact) 데이터를 영구 보관합니다. Canonical 스키마에 대한 write 트랜잭션은 오직 검증된 승인 기반 데이터 승격 파이프라인 내부에서만 제한적으로 수행되며, 분석 예측용 `model-serving` 산출물 데이터는 본 영역에 진입하지 못하고 `mart`, `ops`, `qa` 스키마 내부로 격리 적재됩니다.

| **테이블 명 (Table)** | **역할 범위** | **쓰기 주체** | **핵심 엔티티 열 (Column)** | **행 수 (Tup)** | **용량 (Size)** | **비고 및 실행 제약** |
| --- | --- | --- | --- | --- | --- | --- |
| `canonical.measurement_1min` | 승인된 1분 단위 사실 데이터 | 제어 권한을 소유한 전용 승격 worker | `bucket_ts`, `resolution`, `meter_urn`, `measurement`, `value`, `unit`, `aggregation_policy`, `expected_points`, `observed_points`, `missing_points`, `coverage_ratio`, `quality_summary`, `provenance`, `source_event_ids`, `source_run_id`, `promotion_id`, `lineage_key`, `loaded_at` | 6,166,408 | 5373 MB | 정밀 관측 사실 데이터 파이프라인의 1분 단위 기초 기준 테이블 |
| `canonical.measurement_15min` | 승인된 15분 단위 사실 데이터 | 제어 권한을 소유한 전용 승격 worker | `bucket_ts`, `resolution`, `meter_urn`, `measurement`, `value`, `unit`, `aggregation_policy`, `expected_points`, `observed_points`, `missing_points`, `coverage_ratio`, `quality_summary`, `provenance`, `source_event_ids`, `source_run_id`, `promotion_id`, `lineage_key`, `loaded_at` | 104,621 | 130 MB | 분석 보고서 아티팩트 및 인프라 운영 대시보드의 15분 단위 신뢰 소스 데이터 |
| `canonical.measurement_h` | 승인된 1시간 단위 사실 데이터 | 제어 권한을 소유한 전용 승격 worker | `bucket_ts`, `resolution`, `meter_urn`, `measurement`, `value`, `unit`, `aggregation_policy`, `expected_points`, `observed_points`, `missing_points`, `coverage_ratio`, `quality_summary`, `provenance`, `source_event_ids`, `source_run_id`, `promotion_id`, `lineage_key`, `loaded_at` | 22,462 | 39 MB | 시간 해상도 단위 관측 사실 마스터 데이터 및 외부 분석 모델 대조용 비교 원천 |

## 8. mart 스키마 계약

`mart` 스키마는 `model-serving` 추론 단계 전후의 입력 피처 데이터셋과 최종 모델 예측/알람 산출물을 영구 격리하여 관리합니다. P-Max 모델 파이프라인과 이상 탐지(Anomaly) 모델 파이프라인은 특성 입력(Feature) 테이블과 서빙 출력(Output) 테이블을 엄격히 분리 설계하였으며, 파이프라인 제어 `scheduler`는 구동 시 실행 메타데이터와 참조 소스 명세(`source_refs`)를 필수로 기록합니다.

| **테이블 명 (Table)** | **역할 범위** | **쓰기 주체** | **핵심 엔티티 열 (Column)** | **행 수 (Tup)** | **용량 (Size)** | **비고 및 실행 제약** |
| --- | --- | --- | --- | --- | --- | --- |
| `mart.peak_feature_15min` | P-Max 모델용 15분 피처 데이터셋 | `cms_peak_feature_worker` | `window_ts`, `meter_urn`, `measurement`, `mean_value`, `max_value`, `min_value`, `p95_value`, `p99_value`, `std_value`, `last_value`, `peak_ts`, `peak_value`, `observed_points`, `expected_points`, `coverage_ratio`, `source_file`, `run_id`, `source_layer`, `source_mode`, `provenance` | 37,270,990 | 33 GB | 데이터 최신성 및 인덱스 파티셔닝 조회의 메인 기준 필드로 `window_ts` 활용 |
| `mart.pmax_forecast_15min` | P-Max 모델 추론 예측 산출물 | `pmax_scheduler` | `logical_meter`, `source_meter_urn`, `base_ts`, `input_end_ts`, `target_ts`, `actual_window_ts`, `horizon_minutes`, `predicted_p_max`, `created_at` | 515,912 | 95 MB | 예측 대상 타겟 시각 쿼리 및 서비스 plane 전달의 기준 필드로 `target_ts` 활용 |
| `mart.anomaly_feature_1h` | Anomaly 모델용 1시간 피처 데이터셋 | `cms-anomaly-feature-worker` | `bucket_ts`, `meter_urn`, `feature_set`, `p_value`, `u1_value`, `pf_value`, `qv_value`, `tdiff_value`, `derived_features`, `input_quality`, `source_refs`, `created_at` | 3,392,283 | 3849 MB | 이상 탐지 추론 엔진 구동을 위한 실시간 live feature 정규화 데이터 적재 테이블 |
| `mart.anomaly_warning_1h` | Anomaly 모델 추론 경고 알람 산출물 | `anomaly_scheduler` | `warning_id`, `run_id`, `model_name`, `model_version`, `release_version`, `meter_urn`, `forecast_origin_ts`, `target_ts`, `lead_step`, `horizon_hours`, `predicted_p`, `threshold_lower`, `threshold_upper`, `warning_flag`, `warning_type`, `status`, `input_quality`, `warning_reason_code`, `source_input_refs`, `created_at` | 1,372,067 | 1031 MB | `warning_flag=true` 조건으로 적재된 row를 정식 비즈니스 운영 경고 알람 이벤트로 정의 |

## 9. ops 스키마 계약

`ops` 스키마는 시스템 런타임 가용 상태 지표와 하이 레벨 운영 산출물 아티팩트를 보관합니다. `ops.worker_heartbeat` 및 `ops.worker_event_log` 테이블은 worker 프로세스의 라이브니스 실시간 증적으로 활용되며, 파이프라인 전반을 아우르는 data-flow snapshot 성격의 감사는 별도의 독립 관리 경계 스펙 문서에서 설계합니다.

| **테이블 명 (Table)** | **역할 범위** | **쓰기 주체** | **핵심 엔티티 열 (Column)** | **행 수 (Tup)** | **용량 (Size)** | **비고 및 실행 제약** |
| --- | --- | --- | --- | --- | --- | --- |
| `ops.worker_heartbeat` | worker 최신 가용 상태 지표 | 가용 worker, 파이프라인 scheduler | `worker_name`, `status`, `heartbeat_at`, `updated_at`, `last_error`, `restart_count`, `processed_count`, `failed_count`, `details` | 62 | 56 kB | `pmax_scheduler`, `anomaly_scheduler`, `anomaly_feature_scheduler`, `canonical_scheduler` 식별자 명칭 사용 |
| `ops.worker_event_log` | worker 이벤트 로그 (Append-only) | 가용 worker, 파이프라인 scheduler | `event_id`, `worker_name`, `event_at`, `status`, `processed_delta`, `failed_delta`, `restart_delta`, `error_message`, `details` | 127,811 | 74 MB | 프로세스 상태 변화 내역 및 예외 오류 발생 히스토리를 append-only 방식으로 영구 기록 |
| `ops.pmax_log` | P-Max 추론 파이프라인 실행 로그 | `pmax_scheduler` | `run_id`, `base_ts`, `status`, `quality_status`, `logical_meter_count`, `forecast_row_count`, `replacement_row_count`, `error_reason`, `details`, `started_at`, `completed_at` | 228 | 160 kB | 운영 Freshness 및 완료 정합성 계측의 기준 필드로 `completed_at` 지정 |
| `ops.anomaly_log` | Anomaly 추론 파이프라인 실행 로그 | `anomaly_scheduler` | `run_id`, `job_id`, `model_name`, `model_version`, `release_version`, `forecast_origin_ts`, `artifact_ref`, `status`, `meter_count`, `prediction_count`, `warning_count`, `blocked_reason`, `details`, `started_at`, `finished_at` | 576 | 4 kB | 운영 Freshness 및 완료 정합성 계측의 기준 필드로 `finished_at` 지정 |
| `ops.model_artifact` | 모델 아티팩트 메타데이터 레지스트리 | 아티팩트 인벤토리 자동 로더 엔진 | `source_host`, `relative_path`, `artifact_root`, `artifact_path`, `artifact_name`, `model_kind`, `artifact_type`, `logical_target`, `run_id`, `file_size_bytes`, `modified_at`, `sha256`, `registry_status`, `metadata`, `discovered_at`, `loaded_at` | 1,028 | 824 kB | 무거운 모델 바이너리는 분산 파일 시스템에 격리 적재하고 DB는 메타데이터 스펙만 보관 |
| `ops.energy_doc` | RAG 인프라 레이어 참조 지식 원천 | 비정형 지식 소스/도큐먼트 자동 로더 | `id`, `content`, `embedding`, `source`, `hash`, `created_at` | 624 | 80 kB | `vector` 데이터베이스 익스텐션 기반의 고차원 지식 임베딩 원천 벡터 데이터 적재 테이블 |
| `ops.daily_report` | 일간 통합 분석 보고서 상태 원장 | 보고서 생성 전용 DAG 및 서비스 API | 각 날짜별 총 전력 사용량(`total_consumption_kwh`), 자립률(`self_sufficiency_pct`), COP 지표, 리포트 스냅샷 JSON 및 `source_refs` 명세 | 78 | 1080 kB | 최종 승인된 일간 보고서 비즈니스 페이로드 데이터와 참조 계보 메타데이터 통합 제어 |
| `ops.weekly_report` | 주간 통합 분석 보고서 상태 원장 | 보고서 생성 전용 DAG 및 서비스 API | 주간 주기 범위(`period_start`/`period_end`), 통계 집계치, 리포트 스냅샷 JSON 및 `source_refs` 명세 | 32 | 352 kB | 최종 승인된 주간 보고서 비즈니스 페이로드 데이터와 참조 계보 메타데이터 통합 제어 |
| `ops.monthly_report` | 월간 통합 분석 보고서 상태 원장 | 보고서 생성 전용 DAG 및 서비스 API | 월간 스케줄 주기 정보, 전력 사용 동향 통계 지표, 리포트 스냅샷 JSON 및 `source_refs` 명세 | 6 | 1392 kB | 최종 승인된 월간 보고서 비즈니스 페이로드 데이터와 참조 계보 메타데이터 통합 제어 |

## 10. qa 스키마 계약

`qa` 스키마는 파이프라인 전반의 data quality issue, `model-serving` 단계의 입출력 evidence, 그리고 모델의 정밀 성능 evaluation 평가지표를 보관합니다. QA 스키마의 각 row 엔티티는 최초 원천 데이터 로우, 시간 집계 버킷, 모델 실행 `run_id` 및 서빙 증적 아티팩트 묶음을 유기적으로 상호 연결하는 핵심 검증 증적으로 기능합니다.

| **테이블 명 (Table)** | **역할 범위** | **쓰기 주체** | **핵심 엔티티 열 (Column)** | **행 수 (Tup)** | **용량 (Size)** | **비고 및 실행 제약** |
| --- | --- | --- | --- | --- | --- | --- |
| `qa.bad_row` | 원천 데이터 유효성 실패 레코드 | 수집 레이어 consumer, QA 데이터 로더 | `run_id`, `source_file`, `source_row_no`, `reason`, `raw_ts`, `raw_value`, `created_at`, `reason_code`, `observed_at`, `source_ref`, `lineage_ref`, `meter_urn`, `measurement`, `qa_stage`, `review_status`, `raw_payload` | 0 | 16 kB | 원천 소스 파일 파싱 실패 및 컬럼 포맷 오염 레코드 데이터의 로우 단위 격리 레이어 |
| `qa.live_issue` | 수집 및 집계 단계 품질 장애 로그 | 수집 consumer, 집계 배치 worker, QA 모니터 | `issue_id`, `issue_kind`, `severity`, `meter_urn`, `measurement`, `event_id`, `bucket_ts`, `resolution`, `policy_id`, `policy_version`, `reason`, `details`, `created_at` | 18 | 304 kB | 실시간 수집 이벤트 및 정규화 버킷 단계에서 발생한 도메인 품질 issue 추적 인덱스 |
| `qa.serving_evidence` | 추론 모델 서빙 근거 증적 패킷 | `pmax_scheduler`, `thought`, `anomaly_scheduler` | `packet_id`, `run_id`, `forecast_origin_ts`, `dry_run`, `writes_enabled`, `pmax_prediction_count`, `anomaly_prediction_count`, `evidence`, `created_at` | 28 | 2328 kB | 서빙 산출물의 원천 추론 모드, 실제 테이블 write 적용 플래그 및 카운트 증적 보관 |
| `qa.pmax_eval` | P-Max 모델 예측 성능 평가 지표 | 오프라인 모델 evaluation worker | `eval_id`, `logical_meter`, `source_meter_urn`, `base_ts`, `target_ts`, `actual_window_ts`, `horizon_minutes`, `predicted_p_max`, `actual_p_max`, `absolute_error`, `squared_error`, `evaluated_at` | 200 | 80 kB | 추론 예측 산출물과 실제 canonical 사실 데이터 적재 구간(Actual window) 간의 통계 오차 대조 |
| `qa.anomaly_eval` | Anomaly 모델 알람 정밀 평가 지표 | 오프라인 모델 evaluation worker | `eval_id`, `run_id`, `warning_id`, `meter_urn`, `forecast_origin_ts`, `target_ts`, `lead_step`, `metric_name`, `metric_value`, `quality_status`, `evidence_ref`, `created_at` | 200 | 160 kB | 모델이 산출한 경고 이벤트에 대한 사후 오탐(False Positive)/미탐(False Negative) 비율 정밀 계측 |
| `qa.meter_tag` | 계량기별 테크니컬 QA 태그 매핑 | QA 메타 로더, 마스터 제어 API | `meter_urn`, `tag`, `created_at` | 0 | 8192 B | 물리 및 논리 계량기(Meter) 인프라 단위의 품질 제어 세부 태그 필터 매핑 관리 |

## 11. reference 스키마 계약

`reference` 스키마는 오프라인 샌드박스 환경에서 정밀 보정 및 재샘플링(Resampled) 가공 처리가 완결된 표준 기준 시계열 데이터와 모델 벤치마크용 외부 정형 소스를 격리 적재합니다. Reference 레이어의 row 엔티티는 관측 기반의 순수 사실만을 기록하는 canonical 데이터와 명확히 차별화된 독립적 라인업 계보를 가지며, 분석 모델 추론의 초기 warm-start 데이터셋 가이드라인 및 오프라인 품질 대조 소스로만 제한적으로 활용됩니다.

| **테이블 명 (Table)** | **역할 범위** | **쓰기 주체** | **핵심 엔티티 열 (Column)** | **행 수 (Tup)** | **용량 (Size)** | **비고 및 실행 제약** |
| --- | --- | --- | --- | --- | --- | --- |
| `reference.corrected_resampled_15min` | 15분 단위 정제 보정 기준 데이터 | 오프라인 reference data loader | `ts`, `meter_urn`, `measurement`, `value`, `source_file`, `run_id`, `created_at` | 268,177,845 | 46 GB | 오프라인 가공 기준 데이터의 마스터 시계열 표준 시간축 key로 `ts` 필드 지정 |
| `reference.corrected_resampled_1h` | 1시간 단위 정제 보정 기준 데이터 | 오프라인 reference data loader | `ts`, `meter_urn`, `measurement`, `value`, `source_file`, `run_id`, `created_at` | 67,345,904 | 11 GB | anomaly 탐지 모델의 초기 구동 warm-start 데이터셋 및 다운스트림 정합성 대조 비교 원천 |

## 12. 쓰기 권한과 마이그레이션 경계

| **아키텍처 제어 경계** | **권한 할당 및 쓰기 트랜잭션 제약 기준** |
| --- | --- |
| **Canonical 쓰기** | 승인 게이트 규칙을 통과하고 제어 권한을 명확히 바인딩받은 전용 승격 worker만 `canonical.measurement_*`에 write 허용 |
| **Live 쓰기** | 실시간 수집 Kafka consumer 파이프라인 및 정규화 롤업 배치 worker가 `live.*` 테이블군 상태 데이터 전이 제어 |
| **Mart 쓰기** | 분석 도메인 라인별 전용 worker/scheduler가 `mart.*` 테이블을 갱신하며 execution metadata 및 `source_refs` 필수 기재 |
| **Ops 쓰기** | 가용 worker, 런타임 scheduler, 서비스 API 엔진, Airflow DAG 엔진이 heartbeat, runtime event, system log, report 상태 기록 |
| **QA 쓰기** | QA 전용 worker 및 서빙 파이프라인 프로세스가 장애 issue 레코드, serving evidence 패킷, evaluation 평가지표 write 수행 |
| **기준 데이터 쓰기** | reference data loader 엔진이 오프라인에서 보정 및 재샘플링 처리가 완결된 reference 표준 데이터셋 적재 |
| **DDL / 마이그레이션** | 데이터베이스 schema 정의, index 튜닝, database role 권한 설정, hypertable 파티셔닝, extension 활성화는 독립된 migration 관리 경계에서 전담 |

데이터베이스 운영 역할(Operational role)은 시스템 무결성 확보를 위해 최소 권한 원칙(Principle of Least Privilege)에 따라 아키텍처 레벨에서 엄격히 분리합니다. Canonical 테이블군에 대한 쓰기 권한은 데이터 승격 파이프라인 채널로만 완전히 종속시키며, 클라이언트와 맞닿는 외부 서비스/API 경로는 읽기 전용 조회 쿼리와 비동기 작업 등록 접수 기능 레이어로만 제한 구성합니다. 대용량 table의 데이터 최신성(Freshness) 및 총 로우 수는 무거운 count 연산 대신 index 기반의 timestamp probe 기술, `pg_stat_user_tables` 메트릭, `pg_class` 통계 및 worker 프로세스 활성 증적 데이터를 결합하여 정밀하게 판정합니다.

## 13. 문서 연결 구조

| **연결 스펙 명세서 경로** | **개별 규약 명세 및 범위** |
| --- | --- |
| `docs/specs/overview.md` | 시스템 전체 구조와 개별 명세서 간의 연결 관계 요약 |
| `docs/specs/runtime.md` | PC1·PC2·PC3 및 AWS 인프라 환경 내 물리 컴포넌트 배치 구조와 worker/API 경계 명세 |
| `docs/specs/data_platform.md` | 데이터 원천 시스템 인터페이스, Kafka 토픽 구조, PostgreSQL 상태 전이 규칙 및 Canonical/Mart 스키마 격리 경계 정의 |
| `docs/specs/measurement_processing_policy.md` | 수집 주기 기준, 시계열 시간 버킷 정규화 산식, 데이터 커버리지 산정, NULL 필드 처리 및 canonical 승격 기준 명세 |
| `docs/qa/qa_contract.md` | QA 품질 검증 증적 스키마 설계, 실시간 수집 장애, 부적합 로우 관리 규정 및 모델 서빙 성능 지표 명세 |
| `docs/specs/backend_frontend_api_contract.md` | Backend 및 Frontend API 레이어 전체 엔드포인트 상세 라우트 스펙 및 데이터 조회/쓰기 제어 권한 매핑 명세 |
| `docs/specs/llm_contract.md` | LLM, RAG 검색 레이어, Text-to-SQL 파이프라인의 데이터 참조 범위 설정 및 프롬프트 보안 안전 규약 정의 |
| `docs/specs/knowledge_db_contract.md` | 지식 베이스 아키텍처 구성을 위한 Vector DB 인덱싱 최적화 설계 및 비정형 외부 지식 소스 적재 기준 명세 |
| `docs/specs/ontology.md` | 도메인 온톨로지 개념 클래스 정보, 프로퍼티 관계 스키마 매핑 및 데이터 플랫폼 소스 레이어 간의 동기화 규칙 정의 |
