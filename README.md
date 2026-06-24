# CMS

![CMS Overview Architecture](docs/diagrams/stack/overview.svg)

CMS는 건물·설비 계량 데이터를 운영자가 판단할 수 있는 상태 정보, 품질 근거, 보고서, 예측·경고로 연결하는 에너지 운영 지원 시스템입니다. 이 저장소는 CMS의 source archive와 팀 공유용 기술 문서를 함께 보관합니다.

이 README는 `docs/specs/overview.md`, `docs/specs/runtime.md`, `docs/specs/data_platform.md`, `docs/specs/database.md`, `docs/specs/ontology.md`와 기존 SKN25 기술 문서의 운영 구조·데이터 플랫폼·DB 계약 내용을 대조해 정리했습니다. 서버별 credential, 실제 `.env`, 대용량 모델 binary, cache, local runtime log는 저장소 범위에서 제외합니다.

## 프로젝트 소개

CMS는 계량 이벤트가 들어오는 시점부터 운영 화면과 보고서, 모델 예측·경고, RAG 응답에 사용되는 시점까지 같은 데이터 계약을 유지하는 시스템입니다. Live stream은 Kafka가 담당하고, PostgreSQL은 Kafka 이후 상태 전이와 운영 데이터를 보관합니다. FastAPI는 서비스 요청, 읽기 전용 조회, 작업 상태, RAG 응답을 연결하는 API 계층으로 둡니다.

상세 처리 단위는 pipeline diagram에서 확인하고, README 상단의 overview architecture는 기술 스택 간 상호작용만 축약해 보여줍니다.

## 프로젝트 목표

CMS의 목표는 계량 시계열을 단순히 저장하는 것이 아니라, 운영자가 에너지 사용 현황과 설비 이상 징후를 신뢰할 수 있는 근거로 확인하게 만드는 것입니다.

프로젝트의 핵심 목표는 다음과 같습니다.

- 여러 원천에서 들어오는 계량 이벤트를 같은 기준으로 수집하고, 출처와 품질 상태를 함께 남깁니다.
- 관측 사실 데이터와 모델 예측·경고 산출물을 명확히 분리합니다.
- 승인된 관측 데이터는 `canonical` 경계에서 관리하고, P-Max·이상 감지 산출물은 `mart`, `ops`, `qa` 경계에 근거와 함께 보관합니다.
- API, 대시보드, 보고서, RAG가 같은 데이터 계약을 바라보도록 구성합니다.
- 팀원이 저장소만 보고 실행 구조, 데이터 흐름, 스키마 경계, 배포 단위, 모델 artifact 경계를 추적할 수 있게 합니다.

## 저장소 범위

포함 범위:

- CMS Python package source
- React/Vite frontend source
- FastAPI route와 service contract
- Airflow DAG와 Scheduler/Worker source
- Docker Compose stack과 non-secret env template
- PostgreSQL, Kafka, model-serving, observability 관련 운영 스크립트
- 데이터 플랫폼, runtime, DB, ontology, RAG, QA 명세 문서
- Mermaid/DBML diagram source와 render 결과

제외 범위:

- 서버별 실제 `.env`
- private key, token, password, credential
- 대용량 모델 binary와 runtime artifact payload
- build output, cache, log, local notebook/runtime dump
- cleanup archive와 test tree

## 전체 아키텍처

Overview architecture는 서비스 기술 스택 간 상호작용을 보여줍니다. Live/backfill stream은 `Source / Replay Producer -> Kafka -> PostgreSQL` 경로로 표현하고, FastAPI는 React/Vite 화면, PostgreSQL 조회, RAG/Vector DB/Ontology 응답을 연결하는 서비스 계층으로 둡니다.

전체 pipeline detail은 아래 diagram에서 확인합니다.

![Overall Pipeline](docs/diagrams/flow/00_overall.svg)

핵심 영역은 다음처럼 나뉩니다.

| 영역 | 책임 | 주요 구성 |
| --- | --- | --- |
| Data stream | 원천 이벤트와 재생 이벤트를 stream으로 전달 | Source/Replay Producer, Kafka |
| Data store | Kafka 이후 운영 상태와 데이터 계약 보관 | PostgreSQL, TimescaleDB, pgvector |
| Service | 화면, API 응답, 읽기 전용 조회, RAG 연결 | React/Vite, FastAPI, RAG/Vector DB/Ontology |
| Workflow / Model-serving | 예약 작업, 장시간 처리, 예측·경고 산출 | Airflow, Scheduler/Worker, Model Serving |
| Observability | stream lag, DB freshness, runtime metric, dashboard | Prometheus, Grafana, exporters |

## Runtime 구성

CMS runtime은 PC1~PC3 edge runtime과 AWS PostgreSQL plane을 나누어 운영합니다.

![Runtime Topology](docs/diagrams/flow/02_runtime.svg)

| 구역 | 역할 | 주요 구성 |
| --- | --- | --- |
| PC1 | live/backfill stream, backend API, frontend, Airflow 중심 실행 | `cms-ingestion-api`, `cms-backend-api`, `cms-agent-frontend`, Kafka consumer, Airflow |
| PC2 | 운영 관측 stack | `cms-grafana`, `cms-prometheus`, Kafka/node exporters |
| PC3 | model-serving과 MLOps 제어 | `pmax_scheduler`, `anomaly_scheduler`, `cms-model-ops-api`, exporters |
| AWS PostgreSQL | 운영 DB와 실행 상태 장부 | `cms` DB, TimescaleDB, `live`, `canonical`, `mart`, `ops`, `qa`, `reference` schemas |

API 요청 경로는 상태 응답, 읽기 조회, 작업 등록, artifact/status 제공을 담당합니다. Bulk ETL, 집계, 승인 데이터 승격, 모델 추론, 보고서 generation은 Worker, Scheduler, Airflow task 경계에서 처리합니다.

## Data Platform

Data Platform은 Kafka topic, PostgreSQL schema, 상태 전이, 품질 근거, 승인 데이터, 모델 산출물의 경계를 정의합니다.

![DB Live and Canonical Flow](docs/diagrams/flow/01_db.svg)

기본 데이터 흐름은 다음과 같습니다.

```text
원천 / 재생 생산자
-> Kafka measurement_live_v1 또는 measurement_backfill_v1
-> Kafka consumer
-> PostgreSQL live.measurement_event
-> live 집계 후보 / bucket queue / promotion check
-> canonical.measurement_* 또는 mart.*
-> API / Dashboard / Report / RAG consumer
```

PostgreSQL `cms` DB는 Kafka 이후 상태 전이를 보관하는 장부입니다.

| Schema | 역할 | 대표 table |
| --- | --- | --- |
| `live` | 수집 이벤트 장부, live 집계 후보, 처리 queue, promotion check | `measurement_event`, `measurement_1min`, `measurement_15min`, `measurement_1h`, `bucket_queue`, `promotion_check` |
| `canonical` | 승인된 관측 사실 데이터 | `measurement_1min`, `measurement_15min`, `measurement_1h` |
| `mart` | model-serving 입력/산출물과 분석 mart | `peak_feature_15min`, `pmax_forecast_15min`, `anomaly_feature_1h`, `anomaly_warning_1h` |
| `ops` | Worker 상태, 시스템 이벤트, 추론 log, 보고서/RAG 원천 | `worker_heartbeat`, `worker_event_log`, `pmax_log`, `anomaly_log`, `daily_report`, `weekly_report`, `monthly_report`, `energy_doc` |
| `qa` | 데이터 품질, 모델 평가, serving evidence | `bad_row`, `live_issue`, `pmax_eval`, `anomaly_eval`, `serving_evidence` |
| `reference` | 보정·재샘플 기준 데이터 | `corrected_resampled_15min`, `corrected_resampled_1h` |

상태 전이는 이벤트 id, Kafka topic/partition/offset, bucket key, policy id/version, `source_refs`, `run_id`, `promotion_id`, 품질 근거 JSON으로 추적합니다.

## Workflow and Model-serving

Workflow와 Model-serving 영역은 장시간 실행, 예약 실행, 모델 예측·경고, 보고서 산출물 생성 경계를 담당합니다.

![Workflow and Airflow](docs/diagrams/flow/03_airflow.svg)

| Workflow | 입력 | 출력 |
| --- | --- | --- |
| Replay/backfill | source archive, replay window, Kafka topic | `live.measurement_event`, replay lineage |
| Rollup | `live.measurement_event`, `live.bucket_queue` | `live.measurement_15min`, `live.measurement_1h` |
| Promotion | `live.promotion_check`, approval boundary | `canonical.measurement_*` |
| P-Max | `mart.peak_feature_15min` | `mart.pmax_forecast_15min`, `ops.pmax_log`, `qa.pmax_eval` |
| Anomaly | `mart.anomaly_feature_1h` | `mart.anomaly_warning_1h`, `ops.anomaly_log`, `qa.anomaly_eval` |
| Report | QA 근거, canonical/mart 상태, report context | daily/weekly/monthly report artifact and status |

P-Max와 이상 감지는 관측 사실 데이터를 직접 수정하지 않습니다. 모델 입력과 산출물은 `mart`, 실행 log는 `ops`, 평가와 serving evidence는 `qa`에 남깁니다.

LangGraph는 동기 chat path가 아니라 async review, QA recommendation, approval recommendation, report draft review 경계에서 선택적으로 사용합니다.

![LangGraph Review Flow](docs/diagrams/flow/04_graph.svg)

## Application Surface

Application Surface는 운영자가 보는 화면과 API 응답 경계를 담당합니다.

![Application and Service Flow](docs/diagrams/flow/05_app.svg)

- `src/cms/service/` — FastAPI application factory, routers, response contract
- `src/cms/data/` — live processing, database adapter, equalization, promotion, sink logic
- `src/cms/workflow/` — Airflow interface, report workflow, LangGraph adapter, scheduler guard
- `src/cms/modeling/` — P-Max, anomaly, model artifact, model-ops logic
- `src/cms/ontology/` — ontology schema source와 domain mapping helper
- `src/frontend/` — React/Vite frontend dashboard source

FastAPI는 stream producer가 아니라 service/read/response 계층입니다. Live/backfill stream은 Kafka 중심으로 두고, frontend와 report/RAG 응답은 FastAPI와 PostgreSQL 계약을 통해 제공합니다.

## Tech Stack

| 영역 | 기술 |
| --- | --- |
| Language | Python, JavaScript |
| API | FastAPI, Pydantic |
| Frontend | React, Vite, Recharts |
| Stream | Kafka |
| Database | PostgreSQL, TimescaleDB, pgvector |
| Workflow | Airflow, Scheduler Worker, LangGraph |
| Observability | Grafana, Prometheus, exporters |
| Container | Docker, Docker Compose |
| Model-serving | LightGBM/CatBoost/LSTM artifact contract, P-Max, anomaly serving pipeline |
| Knowledge | Ontology TTL/OWL, Graphify, RAG/knowledge source contract |

## Repository Structure

```text
artifacts/              External model artifact manifest and placeholder contract
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

## Local Verification

이 저장소는 concrete runtime secret을 포함하지 않습니다. 실행 환경에서는 필요한 `.env.example`을 복사해 서버별 실제 값을 채운 뒤 compose bundle을 선택합니다.

```bash
# Python source syntax validation
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src scripts dags

# Frontend build
cd src/frontend
npm install
npm run build
```

Compose render는 bundle별 env contract를 채운 뒤 확인합니다.

```bash
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

Overview architecture는 README 상단에서 기술 스택 간 상호작용을 보여줍니다. Pipeline detail은 기존 Mermaid/DBML diagram에서 확인합니다.

| View | Source | Render |
| --- | --- | --- |
| Overview architecture | direct SVG | [stack/overview.svg](docs/diagrams/stack/overview.svg) |
| Overall pipeline | [flow/00_overall.mmd](docs/diagrams/flow/00_overall.mmd) | [flow/00_overall.svg](docs/diagrams/flow/00_overall.svg) |
| DB live/canonical flow | [flow/01_db.mmd](docs/diagrams/flow/01_db.mmd) | [flow/01_db.svg](docs/diagrams/flow/01_db.svg) |
| Runtime topology | [flow/02_runtime.mmd](docs/diagrams/flow/02_runtime.mmd) | [flow/02_runtime.svg](docs/diagrams/flow/02_runtime.svg) |
| Workflow/Airflow | [flow/03_airflow.mmd](docs/diagrams/flow/03_airflow.mmd) | [flow/03_airflow.svg](docs/diagrams/flow/03_airflow.svg) |
| LangGraph review | [flow/04_graph.mmd](docs/diagrams/flow/04_graph.mmd) | [flow/04_graph.svg](docs/diagrams/flow/04_graph.svg) |
| App/service | [flow/05_app.mmd](docs/diagrams/flow/05_app.mmd) | [flow/05_app.svg](docs/diagrams/flow/05_app.svg) |
| Live pipeline ERD | [erd/live_contract.dbml](docs/diagrams/erd/live_contract.dbml) | dbdiagram.io source |

## Safety Boundary

- Concrete secrets, private keys, server-only `.env`, runtime logs, caches, generated build outputs, and large model binaries are excluded.
- `artifacts/external/` contains only placeholder guidance in Git; model payloads are externalized.
- Canonical writes, production DDL, deployment/restart, and DB mutation are controlled runtime operations and are not performed by importing this repository.
- `ALLOW_CANONICAL_WRITE`, `ALLOW_MODEL_SERVING_WRITE`, and `ALLOW_PRODUCTION_DDL` default to closed/disabled values in stack templates.

## License

Internal project.
