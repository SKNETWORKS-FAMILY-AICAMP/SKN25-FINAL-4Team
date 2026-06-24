# CMS

CMS는 건물 및 설비 계량 시계열 데이터를 Kafka 기반 live/backfill stream으로 수집하고, PostgreSQL 상태 원장과 worker/scheduler 경계를 통해 품질 검증, canonical 승격, model-serving, 운영 보고서 생성을 수행하는 에너지 데이터 운영 시스템입니다.

![CMS Stack Architecture](docs/diagrams/stack/overview.svg)

## Goals & Scope

CMS의 목표는 실시간 계량 데이터가 운영 시스템에 들어온 뒤 최종 대시보드, 보고서, 예측 산출물로 소비되기까지의 과정을 추적 가능하고 검증 가능한 데이터 파이프라인으로 구성하는 것입니다.

다루는 문제 영역은 다음과 같습니다.

- source/replay 이벤트를 Kafka topic으로 전달하고 PostgreSQL `live` 원장에 보존
- 1분, 15분, 1시간 단위 집계 후보와 품질 근거 자료 생성
- 승인 경계를 통과한 관측 사실만 `canonical` 스키마로 승격
- P-Max forecast와 anomaly warning을 `mart`, `ops`, `qa` 경계에서 별도 관리
- FastAPI, Grafana, frontend, Airflow, scheduler, LangGraph review workflow를 역할별로 분리
- 데이터 흐름, worker 상태, 모델 산출물, 보고서 생성 결과를 사후 검증 가능한 형태로 기록

이 저장소는 source archive와 설계 명세 공유를 위한 코드베이스입니다. 구체적인 운영 credential, 서버별 `.env`, 모델 binary artifact는 저장소에 포함하지 않고 `env/*.env.example` 및 `artifacts/manifests/` 계약으로 관리합니다.

## Core Capabilities

- Kafka live/backfill ingestion contract
- PostgreSQL `live`, `canonical`, `mart`, `ops`, `qa`, `reference` schema boundary
- Live rollup, bucket queue, promotion check, canonical promotion worker
- P-Max 및 anomaly model-serving scheduler와 검증 근거 기록
- FastAPI 기반 ingestion/backend/model-ops API surface
- React/Vite frontend dashboard source
- Airflow DAG 기반 일간, 주간, 월간 report workflow 및 coverage QA workflow
- Grafana/Prometheus provisioning source
- Ontology, Graphify, RAG/knowledge layer source contract
- Mermaid/SVG 기반 architecture diagram source and render set

## System Architecture

CMS runtime은 Data plane, Service plane, Workflow plane, Observability plane을 분리합니다.

![CMS Overall Pipeline](docs/diagrams/flow/00_overall.svg)

| Plane | 책임 | 주요 구성 |
| --- | --- | --- |
| Data plane | source event, Kafka event, PostgreSQL 상태 전이, QA evidence, canonical fact, mart output 관리 | Kafka, `live`, `canonical`, `mart`, `ops`, `qa`, `reference` |
| Service plane | API 응답, 읽기 전용 조회, report/RAG/forecast 응답, frontend/dashboard surface 제공 | FastAPI, backend API, frontend, Grafana |
| Workflow plane | 예약 작업, 장시간 worker, report generation, model-serving, optional review workflow 관리 | Airflow, scheduler, worker container, LangGraph review |
| Observability plane | Kafka lag, DB freshness, worker heartbeat, exporter metric, dashboard panel 관리 | Prometheus, Grafana, exporters, `ops.worker_heartbeat` |

### Runtime Topology

![CMS Runtime Flow](docs/diagrams/flow/02_runtime.svg)

| 구역 | 역할 | 주요 구성 |
| --- | --- | --- |
| PC1 | live/backfill stream, backend API, frontend, rollup, peak feature, canonical promotion, Airflow | `cms-ingestion-api`, `cms-backend-api`, `cms-agent-frontend`, Kafka consumer, rollup worker, promotion worker, Airflow |
| PC2 | observability stack | `cms-grafana`, `cms-prometheus`, Kafka/node exporters |
| PC3 | model-serving and MLOps control | `pmax_scheduler`, `cms-model-ops-api`, `cms-anomaly-feature-worker`, exporters |
| AWS PostgreSQL | master state ledger and operational database | `cms` DB, TimescaleDB, `live`, `canonical`, `mart`, `ops`, `qa`, `reference` schemas |

## Data Platform Design

기본 데이터 흐름은 다음과 같습니다.

```text
Source / Replay Producer
-> Kafka measurement_live_v1 또는 measurement_backfill_v1
-> Kafka consumer
-> PostgreSQL live.measurement_event
-> live 집계 후보 / bucket queue / promotion check
-> canonical.measurement_* 또는 mart.*
-> API / Dashboard / Report / RAG Consumer
```

![CMS DB Flow](docs/diagrams/flow/01_db.svg)

핵심 schema boundary는 다음 기준으로 분리합니다.

| Schema | 역할 | 대표 table |
| --- | --- | --- |
| `live` | 수집 이벤트 원장, live 집계 후보, 처리 queue, promotion check | `measurement_event`, `measurement_1min`, `measurement_15min`, `measurement_1h`, `bucket_queue`, `promotion_check` |
| `canonical` | 검증 및 승인 완료 관측 사실 데이터 | `measurement_1min`, `measurement_15min`, `measurement_1h` |
| `mart` | model-serving 입력/산출물 및 분석 mart | `peak_feature_15min`, `pmax_forecast_15min`, `anomaly_feature_1h`, `anomaly_warning_1h` |
| `ops` | worker 상태, 시스템 이벤트, 추론 로그, 보고서 메타데이터 | `worker_heartbeat`, `worker_event_log`, `pmax_log`, `anomaly_log`, `daily_report`, `weekly_report`, `monthly_report` |
| `qa` | 데이터 품질, 모델 평가, serving evidence | `bad_row`, `live_issue`, `pmax_eval`, `anomaly_eval`, `serving_evidence` |
| `reference` | 보정 및 재샘플링 기준 데이터 | `corrected_resampled_15min`, `corrected_resampled_1h` |

## Workflow and Model-Serving Design

장시간 작업은 API 요청 경로에서 직접 처리하지 않고 Airflow, scheduler, worker container가 비동기로 수행합니다.

![CMS Workflow Flow](docs/diagrams/flow/03_airflow.svg)

| Workflow | 입력 | 출력 |
| --- | --- | --- |
| Replay/backfill | source archive, replay window, Kafka topic | `live.measurement_event`, replay lineage |
| Rollup | `live.measurement_event`, `live.bucket_queue` | `live.measurement_15min`, `live.measurement_1h` |
| Promotion | `live.promotion_check`, approval boundary | `canonical.measurement_*` |
| P-Max | `mart.peak_feature_15min` | `mart.pmax_forecast_15min`, `ops.pmax_log`, `qa.pmax_eval` |
| Anomaly | `mart.anomaly_feature_1h` | `mart.anomaly_warning_1h`, `ops.anomaly_log`, `qa.anomaly_eval` |
| Report | QA evidence, canonical/mart state, report context | daily/weekly/monthly report artifact and status |

LangGraph는 동기 chat path가 아니라 async review, QA recommendation, approval recommendation, report draft review 경계에서 선택적으로 사용합니다.

![CMS LangGraph Review Flow](docs/diagrams/flow/04_graph.svg)

## Application Surface

![CMS App Flow](docs/diagrams/flow/05_app.svg)

- `src/cms/service/` — FastAPI application factory, routers, response contracts
- `src/frontend/` — React/Vite frontend dashboard source
- `src/cms/data/` — live processing, database adapter, equalization, promotion, sink logic
- `src/cms/workflow/` — Airflow interface, report workflow, LangGraph adapter, scheduler guard
- `src/cms/modeling/` — P-Max, anomaly, model artifact, model-ops logic
- `src/cms/ontology/` — ontology schema source and domain mapping helpers

## Tech Stack

| 영역 | 기술 |
| --- | --- |
| Language | Python, JavaScript |
| API | FastAPI, Pydantic |
| Frontend | React, Vite, Recharts |
| Stream | Kafka |
| Database | PostgreSQL, TimescaleDB, pgvector |
| Workflow | Airflow, scheduler worker, LangGraph |
| Observability | Grafana, Prometheus, exporters |
| Container | Docker, Docker Compose |
| Model-serving | LightGBM/CatBoost/LSTM artifact contract, P-Max, anomaly serving pipeline |
| Knowledge | Ontology TTL/OWL, Graphify, RAG/knowledge source contracts |

## Repository Structure

```text
artifacts/              External model artifact manifests and placeholder contract
configs/                Grafana and Prometheus provisioning source
dags/                   Airflow DAG definitions
docs/                   Architecture specs, diagrams, ontology, plan evidence
env/                    Non-secret .env.example templates
scripts/                Operations, database, serving, stream, verification CLI tools
src/cms/                CMS Python package
src/frontend/           React/Vite frontend source
stacks/                 Compose bundles, Dockerfile/Containerfile, stack requirements
requirements.txt        Shared Python dependency set
```

## Quick Start

이 저장소는 concrete runtime secret을 포함하지 않습니다. 실행 환경에서는 필요한 `.env.example`을 복사해 서버별 실제 값을 채운 뒤 compose bundle을 선택합니다.

```bash
# Python source syntax validation example
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src scripts dags

# Frontend build example
cd src/frontend
npm install
npm run build
```

Compose render는 bundle별 env contract를 채운 뒤 확인합니다.

```bash
# Example only: use a non-secret render env or server-local env file
docker compose --env-file env/compose_render.env.example -f stacks/workflow/airflow.yml config
```

## Key Documents

- [CMS Spec Overview](docs/specs/overview.md)
- [Runtime Architecture](docs/specs/runtime.md)
- [Data Platform Contract](docs/specs/data_platform.md)
- [Database Contract](docs/specs/database.md)
- [Ontology Contract](docs/specs/ontology.md)
- [Diagram Index](docs/diagrams/readme.md)

## Diagram Index

| View | Source | Render |
| --- | --- | --- |
| Overall pipeline | [flow/00_overall.mmd](docs/diagrams/flow/00_overall.mmd) | [flow/00_overall.svg](docs/diagrams/flow/00_overall.svg) |
| DB live/canonical flow | [flow/01_db.mmd](docs/diagrams/flow/01_db.mmd) | [flow/01_db.svg](docs/diagrams/flow/01_db.svg) |
| Runtime topology | [flow/02_runtime.mmd](docs/diagrams/flow/02_runtime.mmd) | [flow/02_runtime.svg](docs/diagrams/flow/02_runtime.svg) |
| Workflow/Airflow | [flow/03_airflow.mmd](docs/diagrams/flow/03_airflow.mmd) | [flow/03_airflow.svg](docs/diagrams/flow/03_airflow.svg) |
| LangGraph review | [flow/04_graph.mmd](docs/diagrams/flow/04_graph.mmd) | [flow/04_graph.svg](docs/diagrams/flow/04_graph.svg) |
| App/service | [flow/05_app.mmd](docs/diagrams/flow/05_app.mmd) | [flow/05_app.svg](docs/diagrams/flow/05_app.svg) |
| Live pipeline ERD | [erd/live_contract.dbml](docs/diagrams/erd/live_contract.dbml) | dbdiagram.io source |
| Stack overview | SVG source | [stack/overview.svg](docs/diagrams/stack/overview.svg) |

## Safety Boundary

- Concrete secrets, private keys, server-only `.env`, runtime logs, caches, generated build outputs, and large model binaries are excluded.
- `artifacts/external/` contains only placeholder guidance in Git; model payloads are externalized.
- Canonical writes, production DDL, deployment/restart, and DB mutation are controlled runtime operations and are not performed by importing this repository.
- `ALLOW_CANONICAL_WRITE`, `ALLOW_MODEL_SERVING_WRITE`, and `ALLOW_PRODUCTION_DDL` default to closed/disabled values in stack templates.

## License

Internal project.
