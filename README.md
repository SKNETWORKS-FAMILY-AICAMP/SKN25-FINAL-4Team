# CMS

![CMS 개요 아키텍처](docs/diagrams/stack/overview.svg)

CMS는 건물·설비 계량 데이터를 운영자가 판단할 수 있는 상태 정보, 품질 근거, 보고서, 예측·경고로 연결하는 에너지 운영 지원 시스템입니다. 이 저장소는 CMS의 소스 보관본과 팀 공유용 기술 문서를 함께 보관합니다.

이 README는 `docs/specs/overview.md`, `docs/specs/runtime.md`, `docs/specs/data_platform.md`, `docs/specs/database.md`, `docs/specs/ontology.md`와 기존 SKN25 기술 문서의 운영 구조·데이터 플랫폼·DB 계약 내용을 대조해 정리했습니다. 서버별 인증 정보, 실제 `.env`, 대용량 모델 파일, cache, 로컬 실행 log는 저장소 범위에서 제외합니다.

## 프로젝트 소개

CMS는 계량 이벤트가 들어오는 시점부터 운영 화면, 보고서, 모델 예측·경고, RAG 응답에 사용되는 시점까지 같은 데이터 계약을 유지하도록 설계된 시스템입니다. 실시간 데이터 흐름은 Kafka가 담당하고, PostgreSQL은 Kafka 이후의 상태 전이와 운영 데이터를 보관합니다. FastAPI는 화면 요청, 읽기 전용 조회, 작업 상태, RAG 응답을 연결하는 API 계층으로 둡니다.

상세 처리 단계는 파이프라인 다이어그램에서 확인합니다. README 상단의 개요 아키텍처는 기술 스택 사이의 상호작용만 축약해 보여줍니다.

## 프로젝트 목표

CMS의 목표는 계량 시계열을 단순히 저장하는 것이 아니라, 운영자가 에너지 사용 현황과 설비 이상 징후를 신뢰할 수 있는 근거로 확인하게 만드는 것입니다. 이를 위해 원천 이벤트, 실시간 수집, 승인 관측 데이터, 모델 산출물, 운영 보고서가 같은 출처 정보와 품질 기준을 공유하도록 구성합니다.

프로젝트의 핵심 목표는 다음과 같습니다.

- 여러 원천에서 들어오는 계량 이벤트를 같은 기준으로 수집하고, 출처·시각·품질 상태를 함께 남깁니다.
- 실시간 수집과 재생 수집을 Kafka topic으로 분리하고, PostgreSQL에서 이벤트 장부와 후속 처리 상태를 추적합니다.
- 관측 사실 데이터와 모델 예측·경고 산출물을 명확히 분리합니다.
- 승인된 관측 데이터는 `canonical` 경계에서 관리하고, P-Max·이상 감지 산출물은 `mart`, `ops`, `qa` 경계에 근거와 함께 보관합니다.
- API, 대시보드, 보고서, RAG가 같은 데이터 계약을 바라보도록 구성합니다.
- 팀원이 저장소만 보고 실행 구조, 데이터 흐름, 스키마 경계, 배포 단위, 모델 산출물 경계를 추적할 수 있게 합니다.

## 저장소 범위

포함 범위:

- CMS Python package 소스
- React/Vite 화면 소스
- FastAPI route와 서비스 계약
- Airflow DAG와 Scheduler/Worker 소스
- Docker Compose 스택과 비밀값을 제외한 env 템플릿
- PostgreSQL, Kafka, 모델 서빙, 운영 관측 관련 스크립트
- 데이터 플랫폼, 실행 환경, DB, ontology, RAG, QA 명세 문서
- Mermaid/DBML 다이어그램 원본과 렌더 결과

제외 범위:

- 서버별 실제 `.env`
- 개인 키, token, password, credential
- 대용량 모델 파일과 실행 산출물 본체
- 빌드 산출물, cache, log, 로컬 notebook/runtime dump
- cleanup archive와 test tree

## 전체 아키텍처

개요 아키텍처는 서비스 기술 스택 간 상호작용을 보여줍니다. 실시간·재생 수집은 `Source / Replay Producer -> Kafka -> PostgreSQL` 경로로 표현하고, FastAPI는 React/Vite 화면, PostgreSQL 조회, RAG/Vector DB/Ontology 응답을 연결하는 서비스 계층으로 둡니다.

상세 파이프라인은 아래 다이어그램에서 확인합니다.

![전체 파이프라인](docs/diagrams/flow/00_overall.svg)

핵심 영역은 다음처럼 나뉩니다.

| 영역 | 책임 | 주요 구성 |
| --- | --- | --- |
| 데이터 전달 | 원천 이벤트와 재생 이벤트를 Kafka로 전달 | Source/Replay Producer, Kafka |
| 데이터 저장소 | Kafka 이후 운영 상태와 데이터 계약 보관 | PostgreSQL, TimescaleDB, pgvector |
| 서비스 응답 | 화면, API 응답, 읽기 전용 조회, RAG 연결 | React/Vite, FastAPI, RAG/Vector DB/Ontology |
| 작업 실행·모델 서빙 | 예약 작업, 장시간 처리, 예측·경고 산출 | Airflow, Scheduler/Worker, Model Serving |
| 운영 관측 | Kafka 지연, DB 최신성, 실행 지표, 대시보드 확인 | Prometheus, Grafana, exporters |

## 실행 환경 구성

CMS 실행 환경은 PC1~PC3 edge 실행 환경과 AWS PostgreSQL 구역을 나누어 운영합니다.

![실행 환경 구성](docs/diagrams/flow/02_runtime.svg)

| 구역 | 역할 | 주요 구성 |
| --- | --- | --- |
| PC1 | 실시간·재생 수집, 백엔드 API, 프론트엔드, Airflow 중심 실행 | `cms-ingestion-api`, `cms-backend-api`, `cms-agent-frontend`, Kafka 소비자, Airflow |
| PC2 | 운영 관측 스택 | `cms-grafana`, `cms-prometheus`, Kafka/node exporters |
| PC3 | 모델 서빙과 MLOps 제어 | `pmax_scheduler`, `anomaly_scheduler`, `cms-model-ops-api`, exporters |
| AWS PostgreSQL | 운영 DB와 실행 상태 장부 | `cms` DB, TimescaleDB, `live`, `canonical`, `mart`, `ops`, `qa`, `reference` 스키마 |

API 요청 경로는 상태 응답, 읽기 조회, 작업 등록, 산출물·상태 제공을 담당합니다. 대량 ETL, 집계, 승인 데이터 승격, 모델 추론, 보고서 생성은 Worker, Scheduler, Airflow task 경계에서 처리합니다.

## 데이터 플랫폼

데이터 플랫폼은 Kafka topic과 PostgreSQL 스키마, 상태 전이, 품질 근거, 승인 데이터, 모델 산출물의 경계를 정의합니다.

![DB 실시간·승인 데이터 흐름](docs/diagrams/flow/01_db.svg)

기본 데이터 흐름은 다음과 같습니다.

```text
원천 / 재생 생산자
-> Kafka measurement_live_v1 또는 measurement_backfill_v1
-> Kafka 소비자
-> PostgreSQL live.measurement_event
-> live 집계 후보 / `bucket_queue` / `promotion_check`
-> canonical.measurement_* 또는 mart.*
-> API / 대시보드 / 보고서 / RAG 소비자
```

PostgreSQL `cms` DB는 Kafka 이후 상태 전이를 보관하는 장부입니다.

| 스키마 | 역할 | 대표 테이블 |
| --- | --- | --- |
| `live` | 수집 이벤트 장부, live 집계 후보, 처리 대기열, `promotion_check` | `measurement_event`, `measurement_1min`, `measurement_15min`, `measurement_1h`, `bucket_queue`, `promotion_check` |
| `canonical` | 승인된 관측 사실 데이터 | `measurement_1min`, `measurement_15min`, `measurement_1h` |
| `mart` | 모델 서빙 입력/산출물과 분석 mart | `peak_feature_15min`, `pmax_forecast_15min`, `anomaly_feature_1h`, `anomaly_warning_1h` |
| `ops` | Worker 상태, 시스템 이벤트, 추론 로그, 보고서/RAG 원천 | `worker_heartbeat`, `worker_event_log`, `pmax_log`, `anomaly_log`, `daily_report`, `weekly_report`, `monthly_report`, `energy_doc` |
| `qa` | 데이터 품질, 모델 평가, `serving_evidence` | `bad_row`, `live_issue`, `pmax_eval`, `anomaly_eval`, `serving_evidence` |
| `reference` | 보정·재샘플 기준 데이터 | `corrected_resampled_15min`, `corrected_resampled_1h` |

상태 전이는 이벤트 ID, Kafka topic/partition/offset, bucket key, policy ID/version, `source_refs`, `run_id`, `promotion_id`, 품질 근거 JSON으로 추적합니다.

## 작업 실행과 모델 서빙

작업 실행과 모델 서빙 영역은 장시간 실행, 예약 실행, 모델 예측·경고, 보고서 산출물 생성 경계를 담당합니다.

![작업 실행과 Airflow](docs/diagrams/flow/03_airflow.svg)

| 작업 | 입력 | 출력 |
| --- | --- | --- |
| 재생/backfill | 원천 archive, 재생 구간, Kafka topic | `live.measurement_event`, 재생 계보 |
| 집계 | `live.measurement_event`, `live.bucket_queue` | `live.measurement_15min`, `live.measurement_1h` |
| 승격 | `live.promotion_check`, 승인 경계 | `canonical.measurement_*` |
| P-Max | `mart.peak_feature_15min` | `mart.pmax_forecast_15min`, `ops.pmax_log`, `qa.pmax_eval` |
| 이상 감지 | `mart.anomaly_feature_1h` | `mart.anomaly_warning_1h`, `ops.anomaly_log`, `qa.anomaly_eval` |
| 보고서 | QA 근거, canonical/mart 상태, 보고서 문맥 | 일간/주간/월간 보고서 산출물과 상태 |

P-Max와 이상 감지는 관측 사실 데이터를 직접 수정하지 않습니다. 모델 입력과 산출물은 `mart`, 실행 로그는 `ops`, 평가와 `serving_evidence`는 `qa`에 남깁니다.

LangGraph는 동기 채팅 경로가 아니라 비동기 검토, QA 권고, 승인 권고, 보고서 초안 검토 경계에서 선택적으로 사용합니다.

![LangGraph 검토 흐름](docs/diagrams/flow/04_graph.svg)

## 운영 화면과 API 경계

운영 화면과 API 경계는 운영자가 보는 화면과 API 응답 경계를 담당합니다.

![운영 화면과 서비스 흐름](docs/diagrams/flow/05_app.svg)

- `src/cms/service/` — FastAPI 앱 생성, router, 응답 계약
- `src/cms/data/` — 실시간 처리, DB adapter, 균등화, 승격, 저장 로직
- `src/cms/workflow/` — Airflow interface, 보고서 workflow, LangGraph adapter, scheduler guard
- `src/cms/modeling/` — P-Max, anomaly, 모델 artifact, 모델 운영 로직
- `src/cms/ontology/` — ontology schema 원본과 domain mapping helper
- `src/frontend/` — React/Vite 프론트엔드 dashboard 소스

FastAPI는 stream 생산자가 아니라 서비스 응답과 읽기 조회 계층입니다. 실시간·재생 수집은 Kafka 중심으로 두고, 프론트엔드와 보고서/RAG 응답은 FastAPI와 PostgreSQL 계약을 통해 제공합니다.

## 기술 구성

| 영역 | 기술 |
| --- | --- |
| 구현 언어 | Python, JavaScript |
| API | FastAPI, Pydantic |
| 화면 | React, Vite, Recharts |
| 이벤트 전달 | Kafka |
| 운영 DB | PostgreSQL, TimescaleDB, pgvector |
| 작업 실행 | Airflow, Scheduler Worker, LangGraph |
| 운영 관측 | Grafana, Prometheus, exporters |
| 컨테이너 | Docker, Docker Compose |
| 모델 서빙 | LightGBM/CatBoost/LSTM artifact 계약, P-Max, 이상 감지 서빙 파이프라인 |
| 지식 근거화 | Ontology TTL/OWL, Graphify, RAG/knowledge 원천 계약 |

## 저장소 구조

```text
artifacts/              외부 모델 산출물 manifest와 placeholder 계약
configs/                Grafana와 Prometheus 설정 소스
dags/                   Airflow DAG 정의
docs/                   아키텍처 명세, 다이어그램, ontology, 계획 근거
env/                    비밀값을 제외한 .env.example 템플릿
scripts/                운영, DB, 서빙, stream, 검증 CLI 도구
src/cms/                CMS Python package
src/frontend/           React/Vite frontend 소스
stacks/                 Compose bundle, Dockerfile/Containerfile, 스택 requirements
requirements.txt        공통 Python 의존성 목록
```

## 로컬 검증

이 저장소는 실행 환경의 실제 비밀값을 포함하지 않습니다. 실행 환경에서는 필요한 `.env.example`을 복사해 서버별 실제 값을 채운 뒤 compose bundle을 선택합니다.

```bash
# Python 소스 문법 확인
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src scripts dags

# 프론트엔드 빌드
cd src/frontend
npm install
npm run build
```

Compose 렌더링은 bundle별 env 계약을 채운 뒤 확인합니다.

```bash
docker compose --env-file env/compose_render.env.example -f stacks/workflow/airflow.yml config
```

## 주요 문서

- [CMS Spec Overview](docs/specs/overview.md)
- [Runtime Architecture](docs/specs/runtime.md)
- [Data Platform Contract](docs/specs/data_platform.md)
- [Database Contract](docs/specs/database.md)
- [Ontology Contract](docs/specs/ontology.md)
- [Diagram Index](docs/diagrams/readme.md)

## 다이어그램 안내

개요 아키텍처는 README 상단에서 기술 스택 간 상호작용을 보여줍니다. 상세 파이프라인은 기존 Mermaid/DBML 다이어그램에서 확인합니다.

| 관점 | 원본 | 렌더 결과 |
| --- | --- | --- |
| 개요 아키텍처 | 직접 작성 SVG | [stack/overview.svg](docs/diagrams/stack/overview.svg) |
| 전체 파이프라인 | [flow/00_overall.mmd](docs/diagrams/flow/00_overall.mmd) | [flow/00_overall.svg](docs/diagrams/flow/00_overall.svg) |
| DB 실시간·승인 데이터 흐름 | [flow/01_db.mmd](docs/diagrams/flow/01_db.mmd) | [flow/01_db.svg](docs/diagrams/flow/01_db.svg) |
| 실행 환경 구성 | [flow/02_runtime.mmd](docs/diagrams/flow/02_runtime.mmd) | [flow/02_runtime.svg](docs/diagrams/flow/02_runtime.svg) |
| 작업 실행과 Airflow | [flow/03_airflow.mmd](docs/diagrams/flow/03_airflow.mmd) | [flow/03_airflow.svg](docs/diagrams/flow/03_airflow.svg) |
| LangGraph 검토 | [flow/04_graph.mmd](docs/diagrams/flow/04_graph.mmd) | [flow/04_graph.svg](docs/diagrams/flow/04_graph.svg) |
| 앱과 서비스 | [flow/05_app.mmd](docs/diagrams/flow/05_app.mmd) | [flow/05_app.svg](docs/diagrams/flow/05_app.svg) |
| 실시간 파이프라인 ERD | [erd/live_contract.dbml](docs/diagrams/erd/live_contract.dbml) | dbdiagram.io 원본 |

## 안전 경계

- 서버별 실제 비밀값, 개인 키, 서버 전용 `.env`, 실행 log, cache, 생성된 빌드 산출물, 대용량 모델 파일은 저장소에 포함하지 않습니다.
- `artifacts/external/`에는 Git에서 추적 가능한 자리표시자와 안내만 둡니다. 모델 본체 파일은 저장소 밖에서 관리합니다.
- 승인 관측 데이터 쓰기, 운영 DDL, 배포·재시작, DB 변경은 실행 환경의 통제된 운영 작업입니다. 이 저장소를 가져오는 것만으로 수행되지 않습니다.
- `ALLOW_CANONICAL_WRITE`, `ALLOW_MODEL_SERVING_WRITE`, `ALLOW_PRODUCTION_DDL`은 스택 템플릿에서 기본적으로 닫힌 값 또는 비활성 값으로 둡니다.

## 라이선스

내부 프로젝트입니다.
