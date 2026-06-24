# CMS

CMS(에너지 운영 지원 시스템)는 건물 및 설비에서 발생하는 계량 데이터를 수집하여, 운영자가 직관적으로 파악할 수 있는 상태 정보, 데이터 품질 검증 근거, 운영 리포트, 그리고 AI 기반의 예측 및 경고 지표로 변환해 주는 시스템입니다.

본 저장소는 CMS의 핵심 소스 코드와 팀 내 아키텍처 공유를 위한 기술 문서를 통합 관리하기 위해 구성되었습니다.

![CMS 개요 아키텍처](docs/diagrams/stack/overview.svg)

## 프로젝트 소개

CMS는 계량 이벤트가 시스템에 최초로 인입되는 시점부터 운영 대시보드 표출, 보고서 생성, 모델 예측 및 경고 지표에 이르기까지 전체 파이프라인에서 일관된 데이터 계약(Data Contract)을 유지하도록 설계되었습니다.

실시간 데이터 스트리밍은 Kafka가 전담하며, 이후의 데이터 상태 전이 및 운영 이력은 PostgreSQL이 보관합니다. FastAPI는 프론트엔드 화면 요청, 읽기 전용 데이터 조회, 작업 상태 확인, 보고서·모델 산출물 조회를 중개하는 서비스 API 계층으로 동작합니다.

상세한 데이터 처리 파이프라인은 하단 다이어그램에서 확인할 수 있으며, 상단의 개요 아키텍처는 기술 스택 간의 핵심적인 상호작용만을 요약하여 보여줍니다.

## 프로젝트 목표

CMS의 목표는 계량 시계열 데이터를 운영자가 에너지 사용 현황, 설비 이상 징후, 모델 예측 결과, 운영 보고서로 확인할 수 있게 정리하는 것입니다. 원본 이벤트, 실시간 수집 데이터, 검증된 관측 데이터, 모델 산출물, 최종 운영 보고서는 동일한 데이터 출처와 품질 기준을 기준으로 연결됩니다.

프로젝트의 핵심 목표는 다음과 같습니다.

- 다양한 출처에서 유입되는 계량 이벤트를 단일 기준으로 수집하고, 데이터의 출처, 발생 시각, 품질 상태를 누락 없이 기록합니다.
- 실시간 수집과 과거 데이터 재수집(Replay)을 별도의 Kafka 토픽으로 분리하고, PostgreSQL을 통해 전체 이벤트 원장(Ledger)과 후속 처리 상태를 투명하게 추적합니다.
- 실제 관측된 사실 데이터와 AI 모델의 예측 및 경고 산출물을 논리적으로 엄격하게 분리합니다.
- 검증 및 승인이 완료된 관측 데이터는 canonical 스키마에서 안전하게 관리하며, P-Max(최대수요예측) 및 이상 감지 산출물은 산출 근거와 함께 mart, ops, qa 스키마에 격리하여 보관합니다.
- API, 운영 대시보드, 리포트, 모델 산출물 조회가 동일한 데이터 계약을 참조하도록 구성합니다.
- 소스 코드, 실행 구성, 데이터 흐름, 스키마 경계, 배포 단위, 모델 산출물 영역을 한 저장소 안에서 함께 설명합니다.

## 저장소 포함 범위

본 저장소에서 관리하고 추적하는 소스 및 산출물의 범위는 다음과 같습니다.

- CMS Python 패키지 소스 코드
- React/Vite 기반 프론트엔드 화면 소스
- FastAPI 라우팅 및 서비스 API 계약 규약
- Airflow DAG, Scheduler, 백그라운드 실행 흐름 소스
- Docker Compose 스택 및 환경 변수 템플릿
- PostgreSQL, Kafka, 모델 서빙, 운영 모니터링 관련 스크립트
- 데이터 플랫폼, 실행 환경, DB, 온톨로지(Ontology), QA 관련 기술 명세 문서
- Mermaid/DBML 기반의 아키텍처 다이어그램 원본 및 렌더링 결과물

## 전체 아키텍처

상단의 개요 아키텍처는 서비스 기술 스택 간의 상호작용을 보여줍니다. 데이터의 실시간 및 재수집 파이프라인은 `Source / Replay Producer -> Kafka -> PostgreSQL` 경로를 거치며, FastAPI는 React/Vite 프론트엔드 화면 요청, PostgreSQL 데이터 조회, 보고서·모델 산출물 조회를 연결하는 서비스 API 계층의 역할을 수행합니다.

상세한 파이프라인 구조는 아래 다이어그램을 참고해 주시기 바랍니다.

![전체 파이프라인](docs/diagrams/flow/00_overall.svg)

시스템의 핵심 영역과 주요 책임은 다음과 같이 나뉩니다.

| 영역 | 역할 및 책임 | 주요 구성 요소 |
| --- | --- | --- |
| 데이터 전달 | 원본 이벤트 및 재수집 계량 이벤트를 Kafka로 안정적으로 스트리밍 | Source/Replay Producer, Kafka |
| 데이터 저장소 | Kafka 인입 이후의 운영 상태 전이 기록 및 데이터 계약 보관 | PostgreSQL, TimescaleDB, pgvector |
| 서비스 응답 | 대시보드 화면 연동, API 응답, 읽기 전용 데이터 조회, 보고서·모델 산출물 조회 | React/Vite, FastAPI, PostgreSQL |
| 작업 실행 및 모델 서빙 | 스케줄링 기반 예약 작업, 장시간 배치 처리, AI 예측 및 경고 모델 구동 | Airflow, Scheduler, Model Serving |
| 운영 관측 (Observability) | Kafka Lag 모니터링, DB 최신성 확인, 실행 지표 및 대시보드 제공 | Prometheus, Grafana, 각종 Exporters |

## 실행 환경 구성

CMS의 실행 환경은 현장의 Edge 환경인 PC1~PC3 구역과 클라우드 환경의 AWS PostgreSQL 구역으로 분리하여 운영됩니다.

![실행 환경 구성](docs/diagrams/flow/02_runtime.svg)

| 구역 | 역할 | 핵심 구성 요소 |
| --- | --- | --- |
| PC1 | 실시간 및 재수집 데이터 처리, 백엔드 API 서빙, 프론트엔드 호스팅, Airflow 기반 워크플로우 실행 | `cms-ingestion-api`, `cms-backend-api`, `cms-agent-frontend`, Kafka Consumer, Airflow |
| PC2 | 시스템 운영 모니터링 및 관측 스택 | `cms-grafana`, `cms-prometheus`, Kafka/Node Exporters |
| PC3 | AI 모델 서빙 및 MLOps 파이프라인 제어 | `pmax_scheduler`, `anomaly_scheduler`, `cms-model-ops-api`, Exporters |
| AWS PostgreSQL | 중앙 운영 DB 및 시스템 상태 원장(Ledger) 관리 | `cms` DB, TimescaleDB, `live`, `canonical`, `mart`, `ops`, `qa`, `reference` 스키마 |

FastAPI 기반의 API 경로는 주로 시스템 상태 응답, 데이터 읽기 조회, 비동기 작업 등록, 모델 산출물 제공을 담당합니다. 시스템 부하가 큰 대용량 ETL, 데이터 집계, 관측 데이터 승격(Promotion), 모델 추론 및 보고서 생성은 Scheduler, Airflow Task, 백그라운드 실행 프로세스로 분리하여 처리합니다.

## 데이터 플랫폼 설계

데이터 플랫폼 영역에서는 Kafka 토픽과 PostgreSQL 스키마를 중심으로 데이터의 상태 전이, 품질 검증 근거, 승인된 데이터, 그리고 모델 산출물 간의 명확한 논리적 경계를 정의합니다.

![DB 실시간·승인 데이터 흐름](docs/diagrams/flow/01_db.svg)

전체적인 기본 데이터 흐름은 다음과 같이 진행됩니다.

```text
원천 / 재생 Producer
-> Kafka (measurement_live_v1 또는 measurement_backfill_v1 토픽)
-> Kafka Consumer
-> PostgreSQL (live.measurement_event 테이블 적재)
-> live 스키마 내 집계 후보 / bucket_queue / promotion_check 검증
-> canonical.measurement_* 또는 mart.* 스키마로 최종 승격 및 저장
-> API / 운영 대시보드 / 보고서 / 모델 산출물 조회를 통한 데이터 소비
```

PostgreSQL 내의 `cms` 데이터베이스는 Kafka를 거쳐 인입된 데이터의 모든 상태 변화를 빠짐없이 기록하는 시스템 원장 역할을 수행합니다.

| 스키마 | 역할 | 대표 테이블 |
| --- | --- | --- |
| `live` | 실시간 수집 이벤트 원장, 집계 후보 데이터 보관, 처리 대기열 및 승격 검사 | `measurement_event`, `measurement_1min`, `measurement_15min`, `measurement_1h`, `bucket_queue`, `promotion_check` |
| `canonical` | 품질 검증 및 승인이 최종 완료된 공식 관측 데이터 | `measurement_1min`, `measurement_15min`, `measurement_1h` |
| `mart` | 모델 서빙을 위한 입력 피처, 모델 산출물 및 데이터 분석용 마트 | `peak_feature_15min`, `pmax_forecast_15min`, `anomaly_feature_1h`, `anomaly_warning_1h` |
| `ops` | 실행 상태 기록, 시스템 이벤트 로그, 추론 기록, 보고서 및 문서 원천 메타데이터 | `worker_heartbeat`, `worker_event_log`, `pmax_log`, `anomaly_log`, `daily_report`, `weekly_report`, `monthly_report`, `energy_doc` |
| `qa` | 데이터 품질 이상 지표, 모델 평가 결과, 서빙 신뢰성 근거 자료 | `bad_row`, `live_issue`, `pmax_eval`, `anomaly_eval`, `serving_evidence` |
| `reference` | 데이터 보정 및 리샘플링 작업 시 기준이 되는 마스터 데이터 | `corrected_resampled_15min`, `corrected_resampled_1h` |

모든 데이터의 상태 전이 내역은 이벤트 ID, Kafka 메타데이터(토픽/파티션/오프셋), 버킷 키, 적용된 정책(ID/버전), 출처 참조 정보(`source_refs`), 실행 ID(`run_id`), 승격 ID(`promotion_id`), 그리고 품질 검증 근거를 담은 JSON 형태로 세밀하게 추적 가능합니다.

## 작업 오케스트레이션 및 모델 서빙

작업 실행 및 모델 서빙 영역에서는 장기 실행(Long-running) 작업, 스케줄링 기반 예약 작업, AI 모델의 예측 및 경고, 자동 리포트 생성 등 시스템 부하가 있는 배치 워크플로우를 담당합니다.

![작업 실행과 Airflow](docs/diagrams/flow/03_airflow.svg)

| 작업 명칭 | 입력 데이터 | 출력 결과물 |
| --- | --- | --- |
| 재수집 (Backfill) | 원본 아카이브, 재생 구간 정보, Kafka 토픽 | `live.measurement_event` 적재 및 재생 계보 기록 |
| 데이터 집계 | `live.measurement_event`, `live.bucket_queue` | `live.measurement_15min`, `live.measurement_1h` 생성 |
| 데이터 승격 | `live.promotion_check` 및 승인 경계 조건 | `canonical.measurement_*` 테이블로 데이터 승격 |
| P-Max (최대수요예측) | `mart.peak_feature_15min` | `mart.pmax_forecast_15min`, `ops.pmax_log`, `qa.pmax_eval` |
| 이상 감지 (Anomaly) | `mart.anomaly_feature_1h` | `mart.anomaly_warning_1h`, `ops.anomaly_log`, `qa.anomaly_eval` |
| 보고서 생성 | QA 품질 근거, canonical/mart 상태, 문서 컨텍스트 | 일간/주간/월간 보고서 산출물 및 생성 상태 |

아키텍처의 핵심 원칙 중 하나는 P-Max와 이상 감지 모델이 어떠한 경우에도 원본 관측 데이터를 직접 수정하지 않는다는 것입니다. 모델의 입력 피처와 산출물은 mart 스키마에, 실행 로그는 ops에, 그리고 모델 평가 결과와 서빙 근거(`serving_evidence`)는 qa 스키마에 엄격하게 분리되어 저장됩니다.

또한, LangGraph는 일반적인 실시간 챗봇 응답용이 아닌, 데이터의 비동기 검토, QA 및 데이터 승인 권고 프로세스, 보고서 초안 리뷰 등 특정 워크플로우 경계에서만 제한적이고 전략적으로 활용됩니다.

![LangGraph 검토 흐름](docs/diagrams/flow/04_graph.svg)

## 운영 화면 및 API 경계

운영자가 직관적으로 시스템을 파악할 수 있는 프론트엔드 화면과 이를 뒷받침하는 백엔드 API 서비스 간의 책임을 명확히 구분합니다.

![운영 화면과 서비스 흐름](docs/diagrams/flow/05_app.svg)

- `src/cms/service/` : FastAPI 애플리케이션 초기화, 라우터 정의, API 응답 규약 관리
- `src/cms/data/` : 실시간 데이터 처리, DB 어댑터, 데이터 균등화(Equalization), 승격 및 저장 핵심 비즈니스 로직
- `src/cms/workflow/` : Airflow 인터페이스, 보고서 워크플로우, LangGraph 어댑터, 스케줄러 가드 로직
- `src/cms/modeling/` : P-Max 및 이상 감지 파이프라인, 모델 아티팩트 관리, 모델 운영 로직
- `src/cms/ontology/` : 온톨로지(Ontology) 스키마 원본 및 도메인 매핑 헬퍼 로직
- `src/frontend/` : React/Vite 기반의 프론트엔드 대시보드 소스 코드

백엔드 설계 시 FastAPI는 실시간 이벤트를 쏟아내는 스트림 프로듀서(Stream Producer)가 아닙니다. FastAPI는 서비스 응답과 읽기 전용 데이터 조회 계층으로 동작합니다. 실시간 수집 및 재수집의 주도권은 Kafka가 가지며, 프론트엔드 표출, 보고서 조회, 모델 산출물 조회는 FastAPI와 PostgreSQL 사이에 정의된 스키마 계약을 통해 제공됩니다.

## 기술 스택 구성

| 영역 | 적용 기술 |
| --- | --- |
| 구현 언어 | Python, JavaScript |
| API 프레임워크 | FastAPI, Pydantic |
| 프론트엔드 | React, Vite, Recharts |
| 이벤트 스트리밍 | Kafka |
| 운영 데이터베이스 | PostgreSQL, TimescaleDB, pgvector |
| 작업 오케스트레이션 | Airflow, Scheduler, LangGraph |
| 운영 모니터링 | Grafana, Prometheus, 각종 Exporters |
| 컨테이너 환경 | Docker, Docker Compose |
| 모델 서빙 | LightGBM / CatBoost / LSTM 아티팩트 계약, P-Max 및 이상 감지 서빙 파이프라인 |
| 문서·온톨로지 보조 자료 | Ontology TTL/OWL, Graphify, 기술 문서 원본 계약 |

## 디렉토리 구조

```text
artifacts/             외부 모델 산출물 매니페스트 및 Placeholder 규약
configs/               Grafana 및 Prometheus 설정 파일
dags/                  Airflow DAG 정의 디렉토리
docs/                  아키텍처 명세서, 시스템 다이어그램, 온톨로지, 기획 근거 문서
env/                   비밀값이 제외된 .env.example 환경 변수 템플릿
scripts/               운영, DB, 모델 서빙, 스트리밍, 검증용 CLI 유틸리티 도구
src/cms/               CMS Python 패키지 메인 소스
src/frontend/          React/Vite 프론트엔드 메인 소스
stacks/                Compose 번들, Dockerfile/Containerfile, 스택별 패키지 의존성
requirements.txt       공통 Python 의존성 목록
```

## 로컬 실행 및 검증

본 저장소에는 프로덕션 환경의 실제 시크릿(Secret) 값이 포함되어 있지 않습니다. 로컬 또는 각 서버 환경에서 프로젝트를 구동하려면, 제공된 `.env.example`을 복사하여 알맞은 환경 변수를 주입한 후 Docker Compose 번들을 실행해야 합니다.

### 1. Python 문법 검증 및 프론트엔드 빌드

```bash
# Python 소스 코드 문법 사전 확인
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src scripts dags

# 프론트엔드 의존성 설치 및 정적 자원 빌드
cd src/frontend
npm install
npm run build
```

### 2. Docker Compose 설정 렌더링 확인

각 번들별로 환경 변수 규약이 올바르게 채워졌는지 아래 명령어로 검증할 수 있습니다.

```bash
docker compose --env-file env/compose_render.env.example -f stacks/workflow/airflow.yml config
```

## 주요 기술 문서

- [CMS 기술 명세 개요 (Overview)](docs/specs/overview.md)
- [런타임 아키텍처 (Runtime Architecture)](docs/specs/runtime.md)
- [데이터 플랫폼 계약 (Data Platform Contract)](docs/specs/data_platform.md)
- [데이터베이스 계약 (Database Contract)](docs/specs/database.md)
- [온톨로지 계약 (Ontology Contract)](docs/specs/ontology.md)
- [다이어그램 인덱스 (Diagram Index)](docs/diagrams/readme.md)

## 다이어그램 안내

문서 최상단의 개요 아키텍처는 기술 스택 간의 전체적인 상호작용을 요약해서 보여줍니다. 세부적인 데이터 파이프라인과 스키마 흐름은 기존 포맷(Mermaid/DBML)으로 작성된 아래 다이어그램들을 통해 확인할 수 있습니다.

| 관점 | 원본 소스 | 렌더링 결과물 |
| --- | --- | --- |
| 개요 아키텍처 | * | [stack/overview.svg](docs/diagrams/stack/overview.svg) |
| 전체 파이프라인 | [flow/00_overall.mmd](docs/diagrams/flow/00_overall.mmd) | [flow/00_overall.svg](docs/diagrams/flow/00_overall.svg) |
| DB 실시간·승인 데이터 흐름 | [flow/01_db.mmd](docs/diagrams/flow/01_db.mmd) | [flow/01_db.svg](docs/diagrams/flow/01_db.svg) |
| 실행 환경 구성 | [flow/02_runtime.mmd](docs/diagrams/flow/02_runtime.mmd) | [flow/02_runtime.svg](docs/diagrams/flow/02_runtime.svg) |
| 작업 실행과 Airflow | [flow/03_airflow.mmd](docs/diagrams/flow/03_airflow.mmd) | [flow/03_airflow.svg](docs/diagrams/flow/03_airflow.svg) |
| LangGraph 검토 흐름 | [flow/04_graph.mmd](docs/diagrams/flow/04_graph.mmd) | [flow/04_graph.svg](docs/diagrams/flow/04_graph.svg) |
| 앱과 서비스 경계 | [flow/05_app.mmd](docs/diagrams/flow/05_app.mmd) | [flow/05_app.svg](docs/diagrams/flow/05_app.svg) |
| 실시간 파이프라인 ERD | [erd/live_contract.dbml](docs/diagrams/erd/live_contract.dbml) | dbdiagram.io 원본 참조 |

## 시스템 안전 및 보안 경계

프로덕션 DB의 승인된 관측 데이터에 대한 쓰기 권한, 운영 환경의 DDL 수정, 시스템 배포 및 재시작 작업은 실행 환경 내부에서 엄격하게 통제됩니다. 저장소의 코드를 내려받는 것만으로는 운영 환경에 영향을 줄 수 없습니다.

모델 서빙 시 사용되는 대용량 AI 모델 파일 본체는 저장소에서 직접 관리하지 않으며, `artifacts/external/` 경로에는 Git으로 추적 가능한 Placeholder(자리표시자)와 연동 안내 규약만 위치합니다.

휴먼 에러를 방지하기 위해 스택 템플릿 내의 `ALLOW_CANONICAL_WRITE`, `ALLOW_MODEL_SERVING_WRITE`, `ALLOW_PRODUCTION_DDL` 변수들은 기본적으로 비활성화(False/닫힌 값) 상태로 고정되어 있습니다.

## 라이선스

본 프로젝트는 사내 내부 운영 목적의 프로젝트입니다.
