# Runtime Architecture

**갱신일:** 2026-06-23

**상태:** 운영 runtime 구조

**범위:** PC1, PC2, PC3, AWS PostgreSQL 환경에 배치된 CMS runtime 구성과 Data plane, Service plane, Workflow plane, worker, scheduler, API, dashboard의 격리 경계 정의.

## 1. Runtime 개요

CMS runtime은 Kafka 실시간 스트림(Live stream), PostgreSQL `state ledger`, `worker`/`scheduler`, API/대시보드 계층을 철저히 격리하여 운영합니다. API 요청 경로는 유효성 검증과 경량 상태 응답만을 담당하며, 장시간 소요되는 데이터 처리 및 DB 상태 전이는 `worker`와 `scheduler`가 비동기로 전담합니다.

핵심 데이터 파이프라인 흐름은 다음 구조를 기준으로 합니다.

```
Source / Replay Producer
-> Kafka measurement_live_v1 또는 measurement_backfill_v1
-> Kafka consumer
-> PostgreSQL live.measurement_event
-> worker / scheduler
-> live 집계 후보 / 품질 근거 자료 / canonical / mart / API / 대시보드
```

`CMS` / `cms`는 활성 프로젝트의 네임스페이스(Namespace)입니다. Python 패키지는 `src/cms`, 운영 DB는 `cms`, 실행 스키마는 `live`, `canonical`, `mart`, `ops`, `qa`, `reference`를 사용합니다. Container, 스키마, 테이블, API route, topic, `consumer group` 같은 식별자는 원문 이름을 그대로 유지합니다.

## 2. 서버 역할

| **구역** | **Runtime 역할** | **주요 구성** |
| --- | --- | --- |
| PC1 | Kafka 실시간/백필 스트림 생산 및 소비, 백엔드 API, 프론트엔드 서빙, 재생(Replay), 롤업(Rollup) 집계, 피크 특성(Peak feature) 산출, 승인 데이터 승격 및 Airflow 운영 | `live-replay-pc1`, `kafka_live_consumer_pc1`, `cms-backfill-consumer-pc1`, `cms_live_mean_rollup_worker`, `cms_peak_feature_worker`, `cms_canonical_promotion_worker`, `cms-ingestion-api`, `cms-backend-api`, `cms-agent-backend`, `cms-agent-frontend`, Airflow container |
| PC2 | 관측 스택(Observability stack) 운영, 인프라 및 카프카 익스포터 기반 메트릭 수집 | `cms-grafana`, `cms-prometheus`, `cms-kafka`, `cms-kafka-exporter`, `cms-node-exporter` |
| PC3 | `model-serving` scheduler, MLOps API, anomaly feature worker 및 데이터 내보내기(Exporter) 운영 | `pmax_scheduler`, `cms-model-ops-api`, `cms-anomaly-feature-worker`, `cms-postgres-exporter`, Kafka/exporter/node exporter |
| AWS PostgreSQL | 마스터 운영 데이터베이스 및 실행 `state ledger` 보관 | `cms` DB, `timescaledb`, `vector`, `pg_stat_statements` 익스텐션 및 `live`, `canonical`, `mart`, `ops`, `qa`, `reference` 스키마 |

PC1은 실시간/백필 데이터 경로와 서비스 경로가 통합되어 구동되는 핵심 실행 구역입니다. PC2는 모니터링 대시보드 제공과 메트릭 관측을 전담합니다. PC3는 `model-serving` 연산과 추론 운영 API 제어를 담당합니다. AWS PostgreSQL은 이벤트 원장, 처리 큐(Queue), 품질 검증 근거 자료, 승인 관측 사실 데이터, 마트 산출물 및 시스템 운영 로그를 통합 보관합니다.

## 3. Plane 구조

| **Plane** | **책임** | **Runtime 구성** | **상태 객체** |
| --- | --- | --- | --- |
| Data plane | 원천 이벤트 수신, Kafka 이벤트 전달, PostgreSQL 상태 전이 제어, 품질 검증 근거 자료 및 승인 데이터/마트 산출물 관리 | Kafka topic, `consumer`, live `worker`, promotion `worker`, 모델 특성 `worker` | `live.*`, `canonical.*`, `mart.*`, `qa.*`, `ops.*`, `reference.*` |
| Service plane | API 요청 처리, 시스템 상태 응답, 읽기 조회 쿼리, 보고서/RAG/예측 응답 제공 및 대시보드 서빙 | `cms-ingestion-api`, `cms-backend-api`, `cms-agent-backend`, `cms-agent-frontend`, `cms-model-ops-api`, Grafana | API 응답, 아티팩트/상태 데이터, 대시보드 판넬 |
| Workflow plane | 예약 작업 주기적 실행, 데이터 재생, 집계 배치 구동, 데이터 승격 제어, `model-serving` 및 보고서 생성 작업 관리 | Airflow, `pmax_scheduler`, `anomaly_scheduler`, `cms-anomaly-feature-worker`, `worker container` | 작업 상태, `worker heartbeat`, 시스템 이벤트 로그, 서빙 검증 근거 자료 |

Data plane은 검증된 관측 사실 데이터와 모델 산출물을 엄격히 격리합니다. `canonical.measurement_*` 테이블은 승인 경계(Approval gate) 뒤에 위치하는 순수 관측 데이터이며, P-Max 및 이상 감지의 입력 피처와 최종 산출물은 `mart`, `ops`, `qa` 스키마 경계 내에서 독립적으로 관리합니다.

## 4. Kafka live/backfill 경로

운영 수집 경로는 Kafka 실시간 스트림을 중심으로 구성합니다.

```
Source / Replay Producer
-> measurement_live_v1 또는 measurement_backfill_v1
-> Kafka consumer
-> live.measurement_event
```

| **항목** | **Runtime 값** |
| --- | --- |
| live topic | `measurement_live_v1` |
| live `consumer` container | `kafka_live_consumer_pc1` |
| live `consumer group` | `postgres-live-ingest` |
| live topic identity | `local_pc123.measurement_live_v1` |
| backfill topic | `measurement_backfill_v1` |
| backfill `consumer` container | `cms-backfill-consumer-pc1` |
| backfill `consumer group` | `postgres-backfill-ingest` |
| DLQ topic | `measurement_dead_letter_v1` |

Kafka `consumer`는 수신한 이벤트 엔벨로프(Event envelope)를 PostgreSQL `live.measurement_event` 테이블에 적재합니다. 토픽, 파티션(Partition), 오프셋(Offset), 메시지 키, 원천 이벤트 ID, 페이로드 해시, 수집 시각은 멱등성 보장 재생 및 장애 추적의 핵심 기준으로 활용됩니다. 데이터베이스 트랜잭션 처리가 성공적으로 완료된 후 오프셋 커밋(Offset commit)을 수행하는 구조를 실행 계약의 기본 원칙으로 설정합니다. 활성 실시간 데이터 경로는 `measurement_live_v1` 및 `measurement_backfill_v1` 토픽의 실행 계약을 기준으로 제어됩니다.

## 5. PostgreSQL 실행 상태 원장

AWS PostgreSQL `cms` DB는 모든 시스템 실행 상태 전이를 기록하는 원장 역할을 수행합니다.

| **Schema** | **Runtime 역할** | **대표 table** |
| --- | --- | --- |
| `live` | 수집 이벤트 원장 관리, Live 단계 버킷 집계, 처리 큐 및 승격(Promotion) 대상 후보 제어 | `measurement_event`, `measurement_1min`, `measurement_15min`, `measurement_1h`, `bucket_queue`, `promotion_check`, `measurement_policy` |
| `canonical` | 검증 및 승인이 완료된 신뢰할 수 있는 관측 사실 데이터 적재 | `measurement_1min`, `measurement_15min`, `measurement_1h` |
| `mart` | `model-serving` 입출력 데이터 및 다운스트림 분석용 구체화된 산출물(Materialized output) 보관 | `peak_feature_15min`, `pmax_forecast_15min`, `anomaly_feature_1h`, `anomaly_warning_1h` |
| `ops` | `worker` 활성화 상태 모니터링, 시스템 운영 이벤트 로그, 최종 보고서, 추론 히스토리 기록 | `metric`, `worker_heartbeat`, `worker_event_log`, `pmax_log`, `anomaly_log`, `daily_report`, `weekly_report`, `monthly_report`, `energy_doc` |
| `qa` | 데이터 품질 검증(QA), 모델 서빙 평가 및 검증 근거 자료 관리 | `bad_row`, `live_issue`, `pmax_eval`, `anomaly_eval`, `serving_evidence`, `meter_tag` |
| `reference` | 오프라인 보정 가이드라인 및 재샘플링(Resampled) 기준 데이터 보관 | `corrected_resampled_15min`, `corrected_resampled_1h` |

`timescaledb` 익스텐션은 데이터베이스 레벨에서 기본 구동되는 기능입니다. 하이퍼테이블(Hypertable)의 세부 운영 정책은 후속 `database.md` 문서의 스키마 운영 계약을 따릅니다. 데이터 흐름 감사 테이블과 메타데이터 스키마는 본 실행 계약 범위에서 제외하며, 별도의 스키마 계약 문서에서 정의합니다.

## 6. Live 집계와 queue 경계

`live.measurement_event` 테이블에 원천 데이터가 적재된 이후, `worker` 경계에서의 상태 전이 흐름은 다음과 같습니다.

```
live.measurement_event
-> live.measurement_1min
-> live.bucket_queue
-> live.measurement_15min / live.measurement_1h
-> live.promotion_check
```

`live.measurement_1min`은 수집된 원천 이벤트를 운영 정책에 맞추어 1분 단위 버킷(Minute bucket)으로 정규화한 Live 단계의 기초 후보 데이터입니다. `live.bucket_queue`는 후속 롤업 집계, 피크 특성 산출, 승격 검증 프로세스가 처리해야 할 미반영 버킷(Dirty bucket)을 추적하는 제어 큐 역할을 합니다. `live.measurement_15min` 및 `live.measurement_1h`는 통계적으로 집계된 관측 후보 데이터이며, Canonical 스키마로 승격되기 전에 품질 검증(QA evidence)과 승인 경계 제어 단계를 거칩니다.

큐 키(Queue key)는 작업의 멱등성을 보장하기 위해 다음 속성 조합을 기준으로 관리합니다.

```
meter_urn
measurement
resolution
bucket_ts
job_kind
policy_version
```

`worker`의 태스크 상태는 `pending`, `running`, `done` (실패 시 `failed` / 지연 및 차단 시 `blocked`) 흐름으로 관리합니다. 지연 수집된 이벤트(Late event)가 도래하는 경우, `worker`는 동일한 키를 가진 버킷을 다시 dirty 상태로 전환하거나 정책 버전에 맞는 재집계 레코드(Re-aggregate version)를 생성합니다.

## 7. Worker 책임

| **Worker / container** | **입력** | **출력** | **Runtime 계약** |
| --- | --- | --- | --- |
| `kafka_live_consumer_pc1` | Kafka `measurement_live_v1` envelope | `live.measurement_event` | 이벤트 고유 키(Idempotency key)와 Kafka offset 보존, DB 쓰기 트랜잭션 성공 확인 후 offset 커밋 수행 |
| `cms-backfill-consumer-pc1` | Kafka `measurement_backfill_v1` envelope | `live.measurement_event`, replay lineage | 과거 백필 이벤트를 live 원장에 적재하고 데이터 원천 및 재생 출처 이력(Lineage) 영구 기록 |
| `cms_live_mean_rollup_worker` | `live.bucket_queue`, `live.measurement_1min` | `live.measurement_15min`, `live.measurement_1h` | 시간 해상도별 평균 집계(Mean rollup) 수행 및 데이터 커버리지, 누락 구간 메트릭, 데이터 계보 보존 |
| `cms_peak_feature_worker` | `live.bucket_queue`, `live.measurement_1min` | `mart.peak_feature_15min` | P-Max 모델용 입력 피처 생성 및 마트 적재 관리, 검증된 관측 사실 데이터 영역과 물리적으로 격리 |
| `cms_canonical_promotion_worker` | 승인된 promotion request, `live.promotion_check` | `canonical.measurement_1min`, `canonical.measurement_15min`, `canonical.measurement_1h` | 승인 경계를 통과한 데이터에 한해 승격 처리 수행, 승격 고유 ID와 사후 검증 증적(Read-back evidence) 기록 |
| `cms-anomaly-feature-worker` | observed 1h source, policy/QA refs | `mart.anomaly_feature_1h` | 이상 탐지 모델 입력 데이터 경계 구성 및 모델 전용(Model-specific) 파이프라인 출처 정보를 마트 입력 데이터와 함께 기록 |
| `pmax_scheduler` | `mart.peak_feature_15min`, model config | `mart.pmax_forecast_15min`, `ops.pmax_log`, `qa.pmax_eval` | P-Max 예측 모델을 `scheduler` 경계에서 안전하게 구동하고 서빙 실행 증적 기록 |
| `report_worker` / Airflow task | QA evidence, promotion state, model-serving output | report artifact, status, run metadata | 시간이 오래 소요되는 보고서 생성 및 산출물 아티팩트 관리를 API 요청 경로 외부에서 비동기로 수행 |

모든 `worker`는 할당된 타겟 스키마 내부에서만 부수 효과(Side-effect)를 유발할 수 있도록 권한을 관리합니다. 스키마 간 교차 쓰기가 필수적인 핵심 파이프라인의 경우, 상위 레벨의 실행 계약 및 승인 경계 정책을 먼저 충족해야 합니다.

## 8. Service/API 경계

Service plane은 운영 조회와 요청 접수를 전담합니다.

| **Service** | **구역** | **역할** |
| --- | --- | --- |
| `cms-ingestion-api` | PC1 | 보조 Ingestion 엔드포인트 제공 및 시스템 헬스체크/상태 응답 |
| `cms-backend-api` | PC1 | 백엔드 Read 전용 데이터 조회, 시스템 상태 요약, 보고서 및 RAG 인프라 레이어 서빙 |
| `cms-agent-backend` | PC1 | 에이전트 연동 전용 백엔드 API, 보고서 생성 요청 접수 및 RAG/예측 응답 제공 |
| `cms-agent-frontend` | PC1 | 운영 관리자용 프론트엔드 UI 대시보드 서빙 |
| `cms-model-ops-api` | PC3 | 모델 추론 파이프라인 제어 및 MLOps 운영 관리 전용 API |
| Grafana | PC2 | 인프라 메트릭 및 데이터 흐름 시각화 대시보드 |
| Prometheus | PC2 | 시계열 메트릭 수집 및 임계치 알람 엔진 |

FastAPI 레이어는 클라이언트 요청 유효성 검증, 시스템 상태 및 읽기 전용 쿼리 처리, 비동기 작업 등록, 아티팩트 다운로드 반환 등의 경량 작업에 집중합니다. 대규모 대용량 ETL, 롤업 집계, Canonical 스키마 승격 처리, 모델 추론 연산 및 보고서 생성과 같은 중량 작업은 API 진입점 내부에서 처리하지 않고 `worker` 및 `scheduler` 영역으로 위임합니다.

`/ingest/measurements`와 같은 데이터 수집 엔드포인트는 페이로드 검증 후 카프카 토픽으로 이벤트를 발행하거나 접수 완료 응답을 반환하는 역할까지만 수행합니다. 실시간 PostgreSQL 상태 전이 및 승인 데이터/마트 테이블 쓰기는 `consumer`와 `worker`가 전담합니다.

## 9. Workflow와 Airflow 경계

Workflow plane은 장시간 소요 작업과 예약된 배치 파이프라인을 제어합니다.

```
job request / schedule
-> Airflow 또는 스케줄러
-> 작업자 실행
-> DB 읽기 검증 / 품질 근거 자료 / artifact
-> status update
```

Airflow와 `scheduler`는 다음 프로세스의 실행 책임을 소유합니다.

| **작업** | **Runtime 책임** |
| --- | --- |
| replay / backfill | 원천 window 계산, 재생 cursor 제어, Kafka 토픽 발행 및 DB 읽기 검증 |
| rollup | 큐 데이터 선점(Claim), 집계 데이터 적재, 커버리지 및 출처 정보 기록 |
| promotion | 승인 게이트 검증, 승격 ID 발급, 승인 데이터 적재 및 정합성 대조 근거 자료 생성 |
| model-serving | P-Max/이상 감지 입력 데이터셋 준비, 추론 엔진 호출, 마트 산출물 적재 및 ops/qa 로그 기록 |
| report | 일간/주간/월간 보고서 아티팩트 생성, 상태 추적 및 참조 소스 메타데이터 기재 |

LangGraph 프레임워크는 데이터 품질 리뷰 워크플로우 또는 추천 파이프라인의 선택적 컴포넌트로 활용됩니다. 데이터 QA 리뷰, 보고서 초안 검토, 재생 계획 수립 및 승격 심사 단계에서 분석 노트와 권장 사항 프리셋을 생성할 수 있습니다. 단, 실제 Canonical 스키마 쓰기, 운영 환경용 프로덕션 DDL 반영, 배포 제어 및 대용량 ETL 작업은 제어 권한을 가진 핵심 `worker`와 표준 운영 절차(SOP)에 의해서만 안전하게 실행됩니다.

## 10. `model-serving` 경계

P-Max 및 이상 감지 모델 추론 파이프라인은 원천 관측 사실(Observed fact) 데이터를 직접 수정하거나 오염시키지 않도록 설계 단계에서부터 철저히 격격리됩니다.

| **Lane** | **Input** | **Output** | **운영 로그/평가** |
| --- | --- | --- | --- |
| P-Max | `mart.peak_feature_15min` | `mart.pmax_forecast_15min` | `ops.pmax_log`, `qa.pmax_eval`, `qa.serving_evidence` |
| Anomaly | `mart.anomaly_feature_1h` | `mart.anomaly_warning_1h` | `ops.anomaly_log`, `qa.anomaly_eval`, `qa.serving_evidence` |

PC3 서버에 배치된 `model-serving` 런타임은 `pmax_scheduler`, `cms-model-ops-api`, `cms-anomaly-feature-worker` 컴포넌트를 중심으로 구동됩니다. P-Max 파이프라인은 15분 단위 특성 구간(Feature window)을 사용하며, 이상 감지는 1시간 단위 특성 구간을 기준으로 설계되었습니다.

예측 결과(Forecast) 및 이상 알람 경고(Warning)는 순수 서빙 산출물 데이터이므로, 검증된 승인 관측 데이터(Canonical measurement)와 오염되지 않도록 독립된 별도 테이블에서 격리하여 관리합니다. 생성된 `model-serving` 산출물은 시스템 API 엔드포인트, Grafana 대시보드, 보고서 아티팩트 및 RAG 엔진의 컨텍스트 참조 레이어를 통해 안정적으로 조회할 수 있습니다.

## 11. QA와 운영 관측

| **계층** | **Runtime 기준** |
| --- | --- |
| Data QA | `qa.bad_row`, `qa.live_issue` 테이블 상태 및 핵심 품질 메트릭 기반 품질 수준 및 누락 관측 구간 정량적 관리 |
| Promotion QA | `live.promotion_check` 테이블 기반 승인 데이터 승격 프로세스 진입 전 품질 검증 증적 및 관리 경계 상태 영구 보관 |
| Worker health | `ops.worker_heartbeat` 테이블 기반 `worker` 프로세스 라이브니스, 마지막 태스크 처리 시각, 최신 상태 주기적 동기화 |
| Worker event history | `ops.worker_event_log` 테이블 기반 `worker` 예외 상황 처리 기록 및 전체 작업 이력 추적 |
| Model-serving evidence | `qa.serving_evidence`, `qa.pmax_eval`, `qa.anomaly_eval` 테이블 기반 예측치 및 경고 데이터의 추론 품질 사후 증적 보존 |
| Dashboard | Grafana 시각화 도구 기반 데이터베이스, 익스포터, API 상태 관측 수행 및 DB Read-back/`worker` 가용 증적 기반 최종 정합성 검증 |

엔드투엔드 데이터 흐름 감사(Data-flow audit) 시스템은 Kafka lag, live 원장 최신성, queue 적체량, 승격 Freshness, 승인 데이터 최신성, 마트 산출물 최신성 메트릭을 단일 파이프라인 인터페이스로 모니터링하는 통합 운영 계층입니다. 세부 테이블 제약 조건 및 스키마 구조는 후속 `database.md` 및 `data_platform.md` 명세서를 기준으로 유효성을 검증합니다.

## 12. Side-effect safety boundary

| **Action** | **Runtime boundary** |
| --- | --- |
| scratch/staging write | 완전 격리된 독립 타겟 지정, run_id 부여, 작업 종료 후 자원 정리 명령 실행, Read-back 정합성 검증 조회 필수 적용 |
| canonical write | 유효한 승인 ID 및 승격 ID 검증, 제한된 통제 데이터베이스 역할 권한 적용, 데이터 정합성 대조 계획 수립 필수 |
| mart write | 데이터 라인별 전용 `scheduler`/`worker` 접근 제어, 참조 소스 명세 기재, 실행 메타데이터 및 품질 검증 증적 기록 |
| DDL/schema change | 데이터베이스 마이그레이션 스크립트 정밀 리뷰, 백업 및 유효성 복구 계획 수립, 상위 변경 관리 위원회 승인 경계 통과 |
| deployment/restart | 가동 범위에 따른 대상 서비스 범위 설정, 헬스체크 기반 런타임 상태 점검, 장애 발생 시 자동 복구 경로 확보 |
| report send | 최종 수신자 정보 크로스 체크, 보고서 아티팩트 무결성 검증, 전송 상태 및 도달 증적 기록 |

FastAPI 계층은 클라이언트로부터의 부수 효과 유발 요청을 접수하고 현재 진행 상태를 조회 반환하는 역할에 집중합니다. 실제 데이터 오염 위험이 있는 장시간 소요 부수 효과 작업은 API 진입점 외부의 `worker`, `scheduler`, Airflow task 및 제어 권한을 소유한 전용 승격 실행 역할에 의해서만 안전하게 수행됩니다.

## 13. Application module 경계

| **Path** | **역할** |
| --- | --- |
| `src/cms/contracts/` | 컴포넌트 간 순환 참조가 발생하지 않는 임포트 안전 데이터 클래스, 라우트/테이블 상수 정의 및 실행 규약 명세 보관 |
| `src/cms/data/` | 데이터 이퀄라이제이션, 실시간/재생 유틸리티, 데이터베이스 어댑터 및 스크래치 가드 모듈 구현 |
| `src/cms/service/` | FastAPI 서비스 팩토리 구성, API 엔드포인트 라우트 정의 및 입출력 응답 계약 구현 |
| `src/cms/workflow/` | Airflow DAG 인터페이스, LangGraph 워크플로우 어댑터 및 `scheduler` 프로세스 예외 가드 구현 |
| `scripts/live/` | 실시간 수집 및 재생 실행용 CLI 도구, Smoke 테스트 러너, Kafka 토픽 발행 헬퍼 스크립트 관리 |
| `scripts/migrations/` | 데이터베이스 스키마 버전 관리를 위한 마이그레이션 초안 및 스키마 유틸리티 스크립트 보관 |
| `scripts/scratch/` | 운영 환경에 영향을 주지 않는 격리된 샌드박스 기반 통합 테스트 헬퍼 스크립트 관리 |
| `scripts/verify/` | 파이프라인 정합성을 반복 검증할 수 있는 회귀 자동화 검증 게이트 구동 스크립트 관리 |
| `docs/specs/` | 런타임 실행 환경, 데이터 플랫폼, 데이터베이스 스키마, API 라우트, LLM 활용 범위 및 온톨로지 명세서 보관 |
| `docs/qa/` | 품질 검증 계약서 및 QA 증적 데이터 관리 기준 문서 보관 |

## 14. 문서 연결

| **문서** | **역할** |
| --- | --- |
| `docs/specs/overview.md` | 전체 시스템 통합 아키텍처 구조 요약 및 하위 개별 명세서 간의 종속 문서 연결 관계 정의 |
| `docs/specs/data_platform.md` | 데이터 원천 시스템 인터페이스, Kafka 토픽 구조, PostgreSQL 상태 전이 규칙 및 Canonical/Mart 스키마 격리 경계 정의 |
| `docs/specs/database.md` | AWS PostgreSQL의 물리 상세 스키마, 테이블 제약 조건, 컬럼 데이터 타입 및 하이퍼테이블 정책 명세 |
| `docs/specs/measurement_processing_policy.md` | 데이터 수집 주기, 시간 버킷 정규화 규칙, 데이터 커버리지 산정, NULL 처리 정책 및 Canonical 승격 기준 정의 |
| `docs/qa/qa_contract.md` | QA 데이터 품질 증적 스키마 설계, 실시간 장애 이슈, 부적합 로우 관리 규정 및 모델 서빙 성능 지표 명세 |
| `docs/specs/backend_frontend_api_contract.md` | Backend 및 Frontend API 레이어의 전체 라우트 인터페이스 명세 및 조회/쓰기 권한 제어 경계 정의 |
| `docs/specs/llm_contract.md` | LLM, RAG 검색 레이어, Text-to-SQL 컴포넌트의 연동 범위 설정 및 보안/프롬프트 안전 가이드라인 정의 |
| `docs/specs/knowledge_db_contract.md` | 지식 베이스 아키텍처 구성을 위한 Vector DB 인덱싱 설계 및 외부 지식 소스 적재 기준 명세 |
| `docs/specs/ontology.md` | 도메인 온톨로지 클래스 및 프로퍼티 관계 설계와 데이터 플랫폼 간의 매핑 명세 |
| `docs/diagrams/readme.md` | Mermaid 소스코드 및 SVG 아키텍처 다이어그램 인덱스 관리와 다이어그램 최신화 형상 관리 기준 정의 |
