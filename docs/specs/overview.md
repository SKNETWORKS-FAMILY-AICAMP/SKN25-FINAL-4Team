# CMS Spec Overview

**갱신일:** 2026-06-23

**상태:** 운영 구조 개요

**범위:** CMS 프로젝트의 실행 구조, 데이터 흐름, 서버 역할, 핵심 테이블(Table) 및 관련 문서 연결 구조 요약. 세부 명세는 `runtime.md`, `data_platform.md`, `database.md`, `measurement_processing_policy.md`, `qa_contract.md`, `backend_frontend_api_contract.md`, `llm_contract.md`, `knowledge_db_contract.md`, `ontology.md`에서 상세 정의.

## 1. 개요

CMS는 건물 및 설비의 계량 시계열 데이터를 수집하고, 품질 기준을 통과한 데이터를 분석·서빙·보고서 생성에 활용하는 데이터 운영 시스템입니다. 시스템 구조는 Data plane, Service plane, Workflow plane으로 상호 격리되도록 설계합니다.

- **Data plane:** Source event, Kafka topic, PostgreSQL 상태 전이, QA evidence, canonical fact, mart output 관리

- **Service plane:** API, 대시보드, 읽기 전용 조회, Report/RAG/Forecast 조회 기능 제공

- **Workflow plane:** Airflow, Scheduler, Worker container 기반의 장시간 소요 작업(Long-running task) 및 예약 작업 담당

`CMS` / `cms`는 활성 프로젝트 명칭이자 기본 네임스페이스(Namespace)입니다. Python 패키지는 `src/cms`를 사용하며, PostgreSQL DB와 런타임 네임스페이스 역시 `cms`를 기준으로 명명합니다. Schema, table, path, container, API route 등의 식별자는 원문 이름을 그대로 유지합니다.

## 2. 운영 구성

CMS 운영 환경은 PC1, PC2, PC3 및 AWS PostgreSQL로 구성됩니다.

| **구역** | **역할** | **주요 구성 요소** |
| --- | --- | --- |
| PC1 | Kafka 실시간 스트림 수집, 백엔드, Kafka consumer, Replay, Rollup, Peak feature, Canonical promotion, Airflow 운영 | `live-replay-pc1`, `kafka_live_consumer_pc1`, `cms-backfill-consumer-pc1`, `cms_live_mean_rollup_worker`, `cms_peak_feature_worker`, `cms_canonical_promotion_worker`, `cms-ingestion-api`, `cms-backend-api`, `cms-agent-backend`, `cms-agent-frontend`, Airflow container |
| PC2 | 모니터링 및 메트릭 수집 스택 운영 | `cms-grafana`, `cms-prometheus`, `cms-kafka`, `cms-kafka-exporter`, `cms-node-exporter` |
| PC3 | `model-serving`, MLOps API, Anomaly feature worker, Exporter 운영 | `pmax_scheduler`, `cms-model-ops-api`, `cms-anomaly-feature-worker`, `cms-postgres-exporter`, Kafka/exporter/node exporter |
| AWS PostgreSQL | 마스터 운영 데이터베이스 (DB) 및 `state ledger` 관리 | `cms` DB, `timescaledb`, `vector`, `pg_stat_statements` 익스텐션 및 `live`, `canonical`, `mart`, `ops`, `qa`, `reference` 스키마 |

운영 DB에는 `timescaledb` 익스텐션이 설치되어 있으며, 하이퍼테이블(Hypertable) 정책은 별도 운영 계약서에 따라 제어됩니다. `ops.audit` 및 `cms_metadata.meter` / `cms_metadata.measurement`는 후속 스키마 계약 대상으로 분류하며, 데이터 흐름 감사(Audit) 및 메타데이터 스키마가 필요한 경우 해당 계약에서 별도로 정의합니다.

## 3. 핵심 아키텍처

CMS의 전체 데이터 파이프라인 흐름은 다음과 같습니다.

```
Source / Replay Producer
-> Kafka measurement_live_v1
-> PostgreSQL live ledger
-> Worker / Scheduler
-> Canonical / Mart / API / Dashboard
```

Data plane의 주 전송 경로는 Kafka 실시간 스트림입니다. Live lane은 `measurement_live_v1` 토픽을, Backfill lane은 `measurement_backfill_v1` 토픽을 통해 이벤트를 수신합니다. PostgreSQL은 Kafka consumer가 적재한 이벤트와 후속 처리 상태를 기록하는 원장 역할을 합니다. Worker와 Scheduler는 Rollup, QA, Canonical promotion, `model-serving` 처리를 API 요청 경로(Request path) 외부에서 비동기로 수행합니다. FastAPI는 운영 API 조회, 상태 응답, 보조 Ingestion 엔드포인트 및 Report/RAG/Forecast 응답을 담당하는 Service plane의 핵심 구성요소입니다.

| **구성 요소** | **역할 및 명세** |
| --- | --- |
| Kafka live stream | Live lane(`measurement_live_v1`) 및 Backfill lane(`measurement_backfill_v1`) 토픽 운용 |
| FastAPI / backend API | 운영 데이터 조회, 상태 응답, 보조 ingestion 엔드포인트 및 report/RAG/forecast API 제공 |
| PostgreSQL 상태 원장 | `live.measurement_event`를 시작으로 각 스키마별 데이터 상태 변화 및 검증 근거 보관 |
| Canonical (승인 경계) | 승인 경계(Approval gate) 기반 관측 사실 데이터 관리 및 모델 산출물 격리 |
| `model-serving` 경계 | P-Max 및 Anomaly 분석 결과 등 주요 서빙 산출물 스키마 적재 관리 |
| Grafana / Prometheus (관측 계층) | DB, API, Exporter 상태 모니터링 및 데이터 최종 신뢰성 검증 기준 제공 |

## 4. Plane 구조

| **Plane** | **책임 범위** | **주요 구성요소** | **아키텍처 주의 사항** |
| --- | --- | --- | --- |
| Data plane | Source, Kafka 이벤트, PostgreSQL live 원장, 후보 데이터, QA evidence, canonical/mart 산출물 관리 | Kafka, `live`, `canonical`, `qa`, `mart`, `ops`, `reference` | Canonical write에 대한 승인 경계 엄격 유지, `model output`과 관측 사실 데이터 상호 격리 |
| Service plane | API 응답, 읽기 전용 조회, 상태 모니터링, Artifact/Status 제공, 대시보드 환경 제공 | FastAPI, backend API, Grafana, Text-to-SQL/RAG API | 대용량 ETL, 장시간 소요 Batch, `model inference` 작업 제외 및 Worker/Scheduler 경계 처리 |
| Workflow plane | 예약 작업 실행, 롱러닝 워커 관리, 리포트 및 모델 파이프라인 구동, 운영 검증 | Airflow, scheduler, worker container, 선택적 LangGraph review | API 요청 경로와 장시간 소요되는 부수 효과(Side effect) 작업 철저 분리 |

## 5. Live/backfill 수집 경로

PC1에서 실행되는 데이터 수집 경로는 다음과 같습니다.

```
Source / Replay Producer
-> Kafka measurement_live_v1 또는 measurement_backfill_v1
-> Kafka Consumer
-> PostgreSQL live.measurement_event
-> Live rollup / Queue / QA / Promotion / Mart worker
```

Kafka 운영 경계 세부 설정은 다음과 같습니다.

| **분류** | **설정 및 식별자 값** |
| --- | --- |
| live stream topic | `measurement_live_v1` |
| live consumer container | `kafka_live_consumer_pc1` |
| live consumer group | `postgres-live-ingest` |
| live topic identity | `local_pc123.measurement_live_v1` |
| backfill topic | `measurement_backfill_v1` |
| backfill consumer group | `postgres-backfill-ingest` |
| DLQ topic | `measurement_dead_letter_v1` |

`measurement_raw_v1` 토픽은 이전 Raw lane 데이터 및 백로그(Backlog) 설명 용도로만 한정하여 사용합니다. 현재 활성화된 Live 경로는 실행 중인 컨테이너 환경 변수(Env)와 Kafka group lag을 기준으로 `measurement_live_v1`을 사용합니다.

## 6. PostgreSQL 스키마 기준

AWS PostgreSQL `cms` DB의 주요 스키마 및 테이블별 역할은 다음과 같습니다.

| **스키마 (Schema)** | **역할** | **대표 테이블 (Table)** |
| --- | --- | --- |
| `live` | 수집 이벤트 원장 기록, Live 버킷, 승격(Promotion) 대상 후보 데이터 관리 | `measurement_event`, `measurement_1min`, `measurement_15min`, `measurement_1h`, `bucket_queue`, `promotion_check`, `measurement_policy` |
| `canonical` | 검증 및 승인이 완료된 관측 사실(Observed fact) 데이터 적재 | `measurement_1min`, `measurement_15min`, `measurement_1h` |
| `mart` | `model-serving` 입출력 데이터 및 가공 분석 마트 관리 | `peak_feature_15min`, `pmax_forecast_15min`, `anomaly_feature_1h`, `anomaly_warning_1h` |
| `ops` | 시스템 운영 로그, 메트릭, 최종 리포트, 워커 상태 기록 | `metric`, `worker_heartbeat`, `worker_event_log`, `pmax_log`, `anomaly_log`, `daily_report`, `weekly_report`, `monthly_report`, `energy_doc` |
| `qa` | 데이터 품질 검증 근거 및 모델 평가 데이터 관리 | `bad_row`, `live_issue`, `pmax_eval`, `anomaly_eval`, `serving_evidence`, `meter_tag` |
| `reference` | 기준 데이터 보정 및 재샘플링(Resampled) 데이터 보관 | `corrected_resampled_15min`, `corrected_resampled_1h` |

각 테이블의 대략적인 로우(Row) 규모는 다음과 같습니다. 해당 수치는 `pg_stat_user_tables.n_live_tup` 기반의 추정치이며, 정확한 회계성 카운트가 필요한 경우 Bounded query를 별도로 수행해야 합니다.

| **테이블 명 (Table)** | **추정 로우 수 (n_live_tup)** |
| --- | --- |
| `live.measurement_event` | 10,434,541 |
| `live.measurement_1min` | 6,147,657 |
| `live.measurement_15min` | 484,742 |
| `live.measurement_1h` | 115,744 |
| `live.bucket_queue` | 676,539 |
| `live.promotion_check` | 6,108,123 |
| `canonical.measurement_1min` | 1,010,444 |
| `canonical.measurement_15min` | 106,232 |
| `canonical.measurement_1h` | 24,462 |
| `mart.peak_feature_15min` | 37,267,852 |
| `mart.pmax_forecast_15min` | 515,784 |
| `mart.anomaly_feature_1h` | 3,392,131 |
| `mart.anomaly_warning_1h` | 1,371,701 |
| `reference.corrected_resampled_15min` | 268,177,845 |
| `reference.corrected_resampled_1h` | 67,345,904 |

## 7. 데이터 상태(Data State) 흐름

데이터의 생명 주기 및 상태 전이 흐름은 다음과 같습니다.

```
Source Event
-> Kafka Live/Backfill Topic
-> live.measurement_event (원장 적재)
-> live.measurement_1min / 15min / 1h (집계 후보 생성)
-> QA evidence / promotion_check (품질 검증)
-> canonical.measurement_* (승인 경계 통과 및 사실 적재)
-> mart feature / forecast / warning (분석 및 예측 산출물 생성)
-> API / Dashboard / Report / RAG Consumer (최종 소비)
```

| **상태 단계** | **정의 및 데이터 정합성 의미** |
| --- | --- |
| `source event` | 원천 소스 또는 Replay producer에 의해 생성된 순수 계량 이벤트 |
| `live.measurement_event` | Kafka consumer가 수신하여 최초로 기록한 이벤트 적재 원장 |
| `live.measurement_*` | 배치/스트리밍 정책에 따라 1차 집계된 Live 단계의 후보 데이터 테이블 |
| `live.promotion_check` | Canonical 스키마 승격 전 품질 검증 결과 및 통과 근거 기록 경계 영역 |
| `canonical.measurement_*` | 검증 및 승인이 완료되어 신뢰할 수 있는 관측 사실(Observed fact) 데이터 |
| `mart.*` | `model-serving` 입출력 데이터 및 다운스트림 분석용 구체화(Materialized) 결과물 |
| `ops.*` | 시스템 운영 로그, 워커 상태, 최종 리포트 및 추론 히스토리 로그 |
| `qa.*` | 데이터 품질 검증 지표, 모델 평가 결과 및 서빙 검증 근거(Evidence) |

## 8. `model-serving` 경계

P-Max 및 Anomaly 시스템은 사후 검증이 가능한 서빙 산출물을 생성하는 파이프라인입니다. 오염되지 않은 순수 관측 사실은 `canonical.measurement_*` 경계 내에서 철저히 분리하여 관리합니다.

| **분석 모듈** | **입력 데이터 (Input)** | **출력 데이터 (Output)** | **운영 로그 및 검증 테이블** |
| --- | --- | --- | --- |
| P-Max | `mart.peak_feature_15min` | `mart.pmax_forecast_15min` | `ops.pmax_log`, `qa.pmax_eval`, `qa.serving_evidence` |
| Anomaly | `mart.anomaly_feature_1h` | `mart.anomaly_warning_1h` | `ops.anomaly_log`, `qa.anomaly_eval`, `qa.serving_evidence` |

PC3의 `model-serving` 구역은 `pmax_scheduler`, `cms-model-ops-api`, `cms-anomaly-feature-worker`를 중심으로 구동됩니다. 각 Worker의 활성화 상태(Heartbeat)는 AWS DB의 `ops.worker_heartbeat` 테이블에 실시간으로 기록됩니다.

`model-serving` 파이프라인의 기준 시각 정보는 다음과 같습니다.

| **관리 항목** | **기준 시각 및 타임스탬프 정보** |
| --- | --- |
| `mart.pmax_forecast_15min` latest target | Target: `2023-12-04 10:15:00+09` (Created at: `2026-06-23 10:03:45+09`) |
| `mart.anomaly_warning_1h` latest target | Target: `2023-10-26 02:00:00+09` (Created at: `2026-06-16 13:38:27+09`) |
| `canonical.measurement_15min` latest loaded | Bucket: `2023-12-04 09:30:00+09` (Loaded at: `2026-06-23 09:54:14+09`) |
| `canonical.measurement_1h` latest loaded | Bucket: `2023-12-04 09:00:00+09` (Loaded at: `2026-06-23 09:31:12+09`) |

## 9. Service/API 경계

| **API / 서비스 명** | **핵심 역할** | **운영 및 제어 기준** |
| --- | --- | --- |
| `cms-ingestion-api` | 데이터 수집(Ingestion) 전용 API | `CMS Ingestion API` 스펙 준수, `writes_allowed=false` 제약 적용 |
| `cms-backend-api` / `cms-agent-backend` | 백엔드 Read / 상태 조회 / 리포트 및 RAG 인프라 제공 | `backend_frontend_api_contract.md` 명세 준수 |
| `cms-agent-frontend` | 프론트엔드 UI 제공 | PC1 Nginx 기반 프론트엔드 컨테이너 구동 |
| `cms-model-ops-api` | `model-serving` 운영 관리 API | PC3 배치 `model-serving` 전용 제어 API |
| Grafana | 운영 현황 대시보드 | PC2 내 Grafana `11.5.2` 버전 기준 운영 |
| Prometheus | 시스템 및 비즈니스 메트릭 수집 | PC2 에이전트 기반 수집 엔진 구동 |

FastAPI 레이어는 상태 조회, 보조 Ingestion 접수, Report/RAG/Forecast 조회 서빙에 집중합니다. 중량 연산이 필요한 Canonical promotion 및 `model inference`는 API 진입점 외부의 Worker 또는 Scheduler가 전담합니다.

## 10. QA 및 운영 관측 기준

| **항목** | **관측 및 검증 기준** |
| --- | --- |
| Data QA | `qa.bad_row`, `qa.live_issue` 및 핵심 품질 메트릭 기반 데이터 품질 및 누락 구간 정량적 식별 |
| Worker health | `ops.worker_heartbeat` 테이블 기반 개별 Worker 프로세스 가용성(Liveness) 실시간 모니터링 |
| Worker event history | `ops.worker_event_log` 테이블 기반 워커 데이터 처리 이력 및 예외 상태 기록 및 추적 |
| Data-flow audit | `ops.audit` 테이블 및 파이프라인 감사 메트릭 기반 엔드투엔드 흐름 관리 |
| Dashboard | Grafana 상태 모니터링 및 DB Read-back / 워커 가용 증적 데이터 활용 최종 정합성 검증 |

## 11. 문서 연결 구조

| **문서 경로** | **역할 및 명세 범위** |
| --- | --- |
| `docs/specs/overview.md` | 시스템 전체 구조와 개별 명세서 간의 연결 관계 요약 |
| `docs/specs/runtime.md` | PC1·PC2·PC3 및 AWS 인프라 배치, API/Worker/Scheduler 격리 경계 정의 |
| `docs/specs/data_platform.md` | Source, Kafka 토픽 구조, PostgreSQL 상태 전이 및 스키마 격리 경계 정의 |
| `docs/specs/database.md` | AWS PostgreSQL 상세 스키마, 테이블, 컬럼 제약 조건 명세 |
| `docs/specs/measurement_processing_policy.md` | 데이터 수집 Cadence, 버킷 구성, 널(NULL) 처리 정책, 승격 자격 정의 |
| `docs/qa/qa_contract.md` | QA 증적 데이터, Live issue, Bad row 정의 및 정량 평가 기준 명세 |
| `docs/specs/backend_frontend_api_contract.md` | Backend / Frontend API 전체 라우트 명세 및 권한 경계 정의 |
| `docs/specs/llm_contract.md` | LLM, RAG, Text-to-SQL 아키텍처 활용 범위 및 보안 규칙 정의 |
| `docs/specs/knowledge_db_contract.md` | Vector DB 및 지식 소스 적재/인덱싱 기준 명세 |
| `docs/specs/ontology.md` | 온톨로지 클래스 및 프로퍼티 설계와 Source-tier 연결 구조 매핑 |
| `docs/specs/meter_metadata.md` | 물리 계량기 명세 및 논리적 계량 그룹 추상화 기준 정의 |
| `docs/reference/source_inventory.md` | 데이터 원천 시스템 인벤토리 리스트 관리 |
| `docs/reference/measurement_glossary.md` | 시스템 내부 도메인 용어 및 계량 비즈니스 용어 사전 |
| `docs/diagrams/readme.md` | 시스템 아키텍처 Mermaid/SVG 다이어그램 인덱스 및 형상 관리 기준 정의 |
