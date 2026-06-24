# Pipeline Diagrams

**갱신일:** 2026-06-17
**상태:** 현재 PC1~PC3 edge runtime + AWS DB plane 기준 diagram index
**범위:** CMS service/runtime, data platform, workflow, review, app surface, DB ERD diagram의 source/render 관리 기준을 정의한다.

## 1. 관리 원칙

- Mermaid flow/sequence diagram은 `.mmd`를 canonical source로 관리한다.
- `.svg`는 팀 공유와 Markdown preview를 위한 render 결과다.
- DB ERD는 dbdiagram.io용 `.dbml`을 canonical source로 관리한다.
- Diagram 의미를 바꾸면 `.mmd`와 대응 `.svg`를 같은 change set에서 함께 갱신한다.
- Render readability gate는 `foreignObject == 0`, `nodeLabel == 0`, `text_tags > 0`이다.

## 2. 현재 runtime 기준

2026-06-15 기준 diagram은 다음 runtime fact를 반영한다.

- PC1: `CMS Ingestion API`, `CMS Backend API`, frontend, Airflow, Kafka broker, 3x Kafka-to-PostgreSQL consumers, live bucket worker.
- PC2: Kafka broker, Prometheus, Grafana, Kafka/node exporters.
- PC3: Kafka broker, canonical/anomaly/model-serving workers. `cms:model-serving` image는 torch 포함 rebuild 완료.
- AWS: PostgreSQL/TimescaleDB, Grafana, postgres/node exporters.
- Kafka lag는 API latency가 아니라 consumer offset backlog다.
- 2023 timestamp는 historical replay/virtual-clock event time이다.
- P-Max direct runtime input은 `mart.peak_feature_15min`이다. `mart.peak_feature_15min`은 legacy/helper projection으로만 표기한다.
- Graphify/LLM Wiki는 docs/specs context grounding 영역이며 active data pipeline node가 아니다.

## 3. Diagram 목록

| 구분 | Source | Render | 설명 |
|---|---|---|---|
| 전체 flow | `flow/00_overall.mmd` | `flow/00_overall.svg` | source/archive, Kafka live ingestion buffer, PostgreSQL live/canonical, peak/model-serving branch, service/workflow/knowledge plane의 전체 연결을 표현한다. |
| live pipeline ERD | `erd/live_contract.dbml` | dbdiagram.io | current deployed/read-back tables와 target/future contract tables를 구분한 DBML ERD 코드다. |
| DB live/canonical flow | `flow/01_db.mmd` | `flow/01_db.svg` | FastAPI, Kafka, PostgreSQL live processing, rollup, QA eligibility, canonical promotion을 상세 표현한다. |
| Runtime topology/data platform flow | `flow/02_runtime.mmd` | `flow/02_runtime.svg` | PC1~PC3 edge runtime과 AWS DB plane의 현재 서비스 배치 및 연결을 표현한다. |
| Airflow/workflow flow | `flow/03_airflow.mmd` | `flow/03_airflow.svg` | PC1 Airflow와 PC3 operational scheduler, report/model/canonical workflow 책임을 표현한다. |
| LangGraph review flow | `flow/04_graph.mmd` | `flow/04_graph.svg` | LangGraph가 일반 chat path가 아니라 async review/QA/approval recommendation에만 위치한다는 경계를 표현한다. |
| App/service flow | `flow/05_app.mmd` | `flow/05_app.svg` | PC1 FastAPI services, frontend/Grafana, read-only SQL, managed ingestion boundary, optional async review를 표현한다. |
| 전체 sequence | `seq/00_overall.mmd` | `seq/00_overall.svg` | ingest, processing, evidence, approval, response까지의 전체 순서를 표현한다. |
| DB live/canonical sequence | `seq/01_db.mmd` | `seq/01_db.svg` | live event ingest, trigger, rollup, QA, canonical promotion 순서를 표현한다. |
| Runtime topology/data platform sequence | `seq/02_runtime.mmd` | `seq/02_runtime.svg` | PC1 ingestion/consumers, Kafka cluster, AWS DB, PC3 model-serving/promotion, read surface의 순서를 표현한다. |
| Airflow/workflow sequence | `seq/03_airflow.mmd` | `seq/03_airflow.svg` | scheduled/manual job, Airflow report/replay, PC3 scheduler, AWS evidence, service status 순서를 표현한다. |
| LangGraph review sequence | `seq/04_graph.mmd` | `seq/04_graph.svg` | review request, context retrieval, evidence boundary check, recommendation, artifact/status 순서를 표현한다. |
| App/service sequence | `seq/05_app.mmd` | `seq/05_app.svg` | ingestion API, backend API, read-only query, model evidence, background job/review status 순서를 표현한다. |

## 4. Plane 기준

| Plane | 포함 | 제외 |
|---|---|---|
| Data plane | Kafka raw topic, PostgreSQL live/mart/ops/qa/canonical, model-serving output | Discord/Hermes/agent-internal delivery path |
| Service plane | FastAPI ingestion/backend, frontend, Grafana, read-only SQL, artifact/status | blocking model inference, uncontrolled DB mutation |
| Workflow plane | Airflow, scheduler, report worker, PC3 model-serving, canonical promotion worker, optional LangGraph review | synchronous chat path |
| Observability plane | Prometheus, Grafana, exporters, Kafka lag/backlog | source-of-truth data correction |

## 5. Render / DBML 기준

Mermaid render 설정은 `config/mermaid.json`을 사용한다.

```bash
mmdc -c docs/diagrams/config/mermaid.json \
  -i docs/diagrams/flow/00_overall.mmd \
  -o docs/diagrams/flow/00_overall.svg
```

전체 재렌더:

```bash
for f in docs/diagrams/*.mmd; do
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

## 6. 수정 규칙

1. Mermaid diagram 의미를 바꿀 때는 `.mmd`를 먼저 수정한다.
2. `.mmd` 수정 후 대응 `.svg`를 재생성한다.
3. DB ERD 의미를 바꿀 때는 `.dbml`을 수정하고 dbdiagram.io에서 렌더링을 확인한다.
4. 새 diagram을 추가하면 이 문서의 목록에 source/render/설명을 추가한다.
5. Diagram 설명 문서는 이 `readme.md` 하나로 유지한다.
