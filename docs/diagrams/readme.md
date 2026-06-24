# Pipeline Diagrams

**갱신일:** 2026-06-24
**상태:** 현재 PC1~PC3 edge runtime + AWS DB plane 기준 diagram index
**범위:** CMS service/runtime, data platform, workflow, review, app surface, DB ERD diagram의 source/render 관리 기준을 정의한다.

## 1. 폴더 구조

```text
docs/diagrams/
├── stack/      # README용 overview SVG
├── flow/       # Mermaid flow source/render
├── seq/        # Mermaid sequence source/render
├── erd/        # DBML ERD source
├── config/     # Mermaid render config
└── archive/    # 이전 원본/정리 전 render 보관
```

## 2. 관리 원칙

- Mermaid flow/sequence diagram은 `.mmd`를 canonical source로 관리한다.
- `.svg`는 팀 공유와 Markdown preview를 위한 render 결과다.
- DB ERD는 dbdiagram.io용 `.dbml`을 canonical source로 관리한다.
- Stack overview SVG는 README 상단용 기술 스택 상호작용 지도다. Pipeline 상세는 `flow/`와 `seq/`에서 관리한다.
- Diagram 의미를 바꾸면 `.mmd`와 대응 `.svg`를 같은 change set에서 함께 갱신한다.
- Render readability gate는 `foreignObject == 0`, `nodeLabel == 0`, `text_tags > 0`이다.

## 3. 현재 runtime 기준

2026-06-24 기준 diagram은 `runtime_architecture.md`의 다음 runtime fact를 반영한다. 직전 원본은 `archive/originals_2026_06_24/`에 보관되어 있다.

- PC1: `cms-ingestion-api`, `cms-backend-api`, `cms-agent-frontend`, `cms-airflow-standalone`/`scheduler`, Kafka broker, `kafka_live_consumer_pc1`, `cms-backfill-consumer-pc1`, `cms_live_mean_rollup_worker`, `cms_peak_feature_worker`, `cms_canonical_promotion_worker`.
- PC2: Kafka broker, `cms-prometheus`, `cms-grafana`, Kafka/node exporters.
- PC3: Kafka broker, `pmax_scheduler`, `anomaly_scheduler`, `cms-anomaly-feature-worker`, `cms-model-ops-api`, postgres/node exporters.
- AWS: PostgreSQL/TimescaleDB, DB/exporter 관측 plane.
- 활성 ingestion topic은 `measurement_live_v1`, backfill topic은 `measurement_backfill_v1`, DLQ는 `measurement_dead_letter_v1`이다. consumer group은 `postgres-live-ingest` / `postgres-backfill-ingest`다.
- canonical promotion은 승인 경계 뒤 실행하고, PC3는 model-serving 영역을 담당한다.
- 데이터 흐름 도면은 `source -> Kafka -> PostgreSQL -> workers -> mart/canonical` 흐름을 중심으로 표기한다.
- Kafka lag는 API latency가 아니라 consumer offset backlog다.
- 2023 timestamp는 historical replay/virtual-clock event time이다.
- P-Max direct runtime input은 `mart.peak_feature_15min`이고, `mart.pmax_forecast_15min` / `mart.anomaly_warning_1h`는 서빙 산출물로 canonical과 분리한다.
- Graphify/LLM Wiki는 docs/specs context grounding 영역이며 active data pipeline node가 아니다.

## 4. Diagram 목록

| 구분 | Source | Render | 설명 |
|---|---|---|---|
| overview architecture | direct SVG | `stack/overview.svg` | 서비스 기술 스택 간 상호작용을 축약해 보여준다. Live stream은 Source/Replay Producer -> Kafka -> PostgreSQL 경로로 두고, FastAPI는 서비스 계층으로 분리한다. |
| 전체 flow | `flow/00_overall.mmd` | `flow/00_overall.svg` | source/archive, Kafka live ingestion buffer, PostgreSQL live/canonical, peak/model-serving branch, service/workflow/knowledge plane의 전체 연결을 표현한다. |
| live pipeline ERD | `erd/live_contract.dbml` | dbdiagram.io | AWS `cms` DB로 검증한 live pipeline DBML ERD다. |
| DB live/canonical flow | `flow/01_db.mmd` | `flow/01_db.svg` | FastAPI, Kafka, PostgreSQL live processing, rollup, QA eligibility, canonical promotion을 상세 표현한다. |
| Runtime topology/data platform flow | `flow/02_runtime.mmd` | `flow/02_runtime.svg` | PC1~PC3 edge runtime과 AWS DB plane의 현재 서비스 배치 및 연결을 표현한다. |
| Airflow/workflow flow | `flow/03_airflow.mmd` | `flow/03_airflow.svg` | PC1 Airflow와 PC3 operational scheduler, report/model/canonical workflow 책임을 표현한다. |
| LangGraph router/review flow | `flow/04_graph.mmd` | `flow/04_graph.svg` | Stage1 요청 유형 분류, Stage2 agent route, runtime gate, LangGraph orchestration, 근거 조회, backend/frontend output 경계를 표현한다. |
| App/service flow | `flow/05_app.mmd` | `flow/05_app.svg` | PC1 FastAPI services, frontend/Grafana, read-only SQL, managed ingestion boundary, optional async review를 표현한다. |
| 전체 sequence | `seq/00_overall.mmd` | `seq/00_overall.svg` | ingest, processing, evidence, approval, response까지의 전체 순서를 표현한다. |
| DB live/canonical sequence | `seq/01_db.mmd` | `seq/01_db.svg` | live event ingest, trigger, rollup, QA, canonical promotion 순서를 표현한다. |
| Runtime topology/data platform sequence | `seq/02_runtime.mmd` | `seq/02_runtime.svg` | PC1 ingestion/consumers, Kafka cluster, AWS DB, PC3 model-serving/promotion, read surface의 순서를 표현한다. |
| Airflow/workflow sequence | `seq/03_airflow.mmd` | `seq/03_airflow.svg` | scheduled/manual job, Airflow report/replay, PC3 scheduler, AWS evidence, service status 순서를 표현한다. |
| LangGraph review sequence | `seq/04_graph.mmd` | `seq/04_graph.svg` | review request, context retrieval, evidence boundary check, recommendation, artifact/status 순서를 표현한다. |
| App/service sequence | `seq/05_app.mmd` | `seq/05_app.svg` | ingestion API, backend API, read-only query, model evidence, background job/review status 순서를 표현한다. |

## 5. README 배치 기준

README에서는 overview와 pipeline detail을 분리해 배치한다.

| README 섹션 | Diagram | 역할 |
|---|---|---|
| 문서 상단 | `stack/overview.svg` | 서비스 기술 스택 간 상호작용을 한 장으로 보여준다. |
| 전체 아키텍처 | `flow/00_overall.svg` | 전체 pipeline detail로 연결한다. |
| Runtime 구성 | `flow/02_runtime.svg` | PC1~PC3 edge runtime과 AWS DB plane 배치를 보여준다. |
| Data Platform | `flow/01_db.svg` | Kafka 이후 PostgreSQL live/canonical 경계를 보여준다. |
| Workflow and Model-serving | `flow/03_airflow.svg` | Airflow, Scheduler, report/model workflow 경계를 보여준다. |
| Workflow and Model-serving | `flow/04_graph.svg` | Stage1/Stage2 router, runtime gate, LangGraph orchestration, 근거 조회, backend/frontend output 경계를 보여준다. |
| Application Surface | `flow/05_app.svg` | FastAPI, frontend, Grafana, report/RAG service 경계를 보여준다. |

## 6. Plane 기준

| Plane | 포함 | 제외 |
|---|---|---|
| Data plane | Kafka raw topic, PostgreSQL live/mart/ops/qa/canonical, model-serving output | Discord/Hermes/agent-internal delivery path |
| Service plane | FastAPI ingestion/backend, frontend, Grafana, read-only SQL, artifact/status | blocking model inference, uncontrolled DB mutation |
| Workflow plane | Airflow, scheduler, report worker, PC3 model-serving, canonical promotion worker, optional LangGraph review | synchronous chat path |
| Observability plane | Prometheus, Grafana, exporters, Kafka lag/backlog | source-of-truth data correction |

## 7. Render / DBML 기준

Mermaid render 설정은 `config/mermaid.json`을 사용한다.

```bash
mmdc -c docs/diagrams/config/mermaid.json \
  -i docs/diagrams/flow/00_overall.mmd \
  -o docs/diagrams/flow/00_overall.svg
```

전체 재렌더:

```bash
for f in docs/diagrams/flow/*.mmd docs/diagrams/seq/*.mmd; do
  mmdc -c docs/diagrams/config/mermaid.json -i "$f" -o "${f%.mmd}.svg"
done
```

검증:

```text
foreignObject == 0
nodeLabel == 0
text_tags > 0
git diff --check
```

## 8. 수정 규칙

1. Mermaid diagram 의미를 바꿀 때는 `.mmd`를 먼저 수정한다.
2. `.mmd` 수정 후 대응 `.svg`를 재생성한다.
3. DB ERD 의미를 바꿀 때는 `.dbml`을 수정하고 dbdiagram.io에서 렌더링을 확인한다.
4. 새 diagram을 추가하면 이 문서의 목록에 source/render/설명을 추가한다.
5. Diagram 설명 문서는 이 `readme.md` 하나로 유지한다.
