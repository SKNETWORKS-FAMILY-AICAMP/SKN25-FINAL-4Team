# CMS Mermaid Diagram 모음

이 폴더는 CMS pre-model pipeline skeleton의 Mermaid source와 GitHub-renderable Markdown wrapper를 보관한다.

GitHub에서는 `.md` 파일의 Mermaid block이 바로 렌더링된다. `.mmd` 파일은 Mermaid CLI, 문서 변환, 이미지 렌더링용 원본이다. Generated `.svg`/`.png` render는 이 폴더의 active source가 아니며, 필요 시 임시 output 또는 shareable report package에만 둔다.

## Diagram 읽는 방법

네 개의 diagram은 모두 sequenceDiagram 형식이며, 같은 시스템을 서로 다른 실행 순서로 설명한다.

자연어 설명은 [`pipeline_explanations.md`](pipeline_explanations.md)에 diagram Markdown 파일별로 정리했다.

1. `01_pre_model_pipeline`은 원천 파일과 live/replay 데이터가 QA, approval, canonical/reference, model/mart, service, scheduler/review로 이동하는 전체 sequence다.
2. `02_latency_sequence`는 81개 source의 1시간 scratch replay가 MongoDB raw scratch, cursor, interval processor, PostgreSQL scratch, QA evidence, FastAPI status/report artifact로 이동하는 사다리형 실행 순서다.
3. `03_chat_routing`은 사용자 요청이 FastAPI router에서 quick answer, read-only evidence query, background job, approval request로 분기되는 정책 경계다.
4. `04_airflow_report`는 schedule/manual trigger가 report packet, QA validation, draft, optional LangGraph review, rendered artifact로 이어지는 보고서 생성 경로다.

## Diagram 파일

| Diagram | GitHub render | Mermaid source | Pipeline 설명 |
|---|---|---|---|
| 전체 pipeline sequence | [`01_pre_model_pipeline.md`](01_pre_model_pipeline.md) | [`01_pre_model_pipeline.mmd`](01_pre_model_pipeline.mmd) | Archive와 live/replay 입력이 QA와 approval gate를 거쳐 canonical/reference/mart와 model/service/scheduler로 들어간다. |
| Live81 latency sequence ladder | [`02_latency_sequence.md`](02_latency_sequence.md) | [`02_latency_sequence.mmd`](02_latency_sequence.mmd) | 81개 source의 1시간 replay event가 MongoDB raw scratch, cursor, processor, PostgreSQL scratch, QA evidence, FastAPI status/report artifact로 이어지고 4,860/972/324/81 row count와 latency가 검증된다. |
| Chat routing skeleton | [`03_chat_routing.md`](03_chat_routing.md) | [`03_chat_routing.mmd`](03_chat_routing.mmd) | User request는 FastAPI lightweight router에서 read-only answer, job registration, approval request로 분기되고 write/admin command는 차단된다. |
| Airflow report skeleton | [`04_airflow_report.md`](04_airflow_report.md) | [`04_airflow_report.mmd`](04_airflow_report.mmd) | Schedule 또는 manual trigger가 evidence packet, QA validation, report draft, optional review, artifact store로 이어진다. |

## 자연어 설명 문서

| 문서 | 내용 |
|---|---|
| [`pipeline_explanations.md`](pipeline_explanations.md) | 네 개 sequence diagram Markdown 파일의 pipeline 흐름을 자연어로 설명한다. |

## Local render

이미지 산출물이 필요하면 Mermaid CLI로 임시 SVG를 만든 뒤, text background 후처리를 적용하고 PNG를 생성한다. 현재 render 설정은 `foreignObject`를 쓰지 않도록 `htmlLabels=false`를 지정한다. Active `docs/specs/diagrams/`에는 generated SVG/PNG를 남기지 않는다.

```bash
mkdir -p /tmp/cms_spec_diagram_render
npx -y @mermaid-js/mermaid-cli@latest -c docs/specs/diagrams/mermaid_render_config.json -i docs/specs/diagrams/01_pre_model_pipeline.mmd -o /tmp/cms_spec_diagram_render/01_pre_model_pipeline.svg -b white
rsvg-convert -b white -f png -o /tmp/cms_spec_diagram_render/01_pre_model_pipeline.png /tmp/cms_spec_diagram_render/01_pre_model_pipeline.svg
```
