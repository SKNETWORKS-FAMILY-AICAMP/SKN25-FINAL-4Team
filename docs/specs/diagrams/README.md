# CMS Mermaid Diagram 모음

이 폴더는 CMS pipeline을 두 가지 시점으로 설명한다.

- `flow_*`: 일반 pipeline/architecture diagram. 큰 화면에서 전체 pipeline을 보고 DB, Airflow, LangGraph, App 세부 pipeline으로 내려간다.
- `sequence_*`: 같은 범위를 실행 순서로 설명하는 sequence diagram. SVG render에는 message label 뒤 배경 박스를 넣어 화살표와 글자가 겹치지 않게 한다.

## Diagram 세트

| 범위 | 일반 diagram | Sequence diagram | 설명 |
|---|---|---|---|
| 전체 pipeline | `flow_00_overall_pipeline.md` / `.mmd` / `.svg` | `sequence_00_overall_pipeline.md` / `.mmd` / `.svg` | Source, Data, Workflow, Application, Knowledge plane을 한 화면에 배치한다. |
| DB pipeline | `flow_01_database_pipeline.md` / `.mmd` / `.svg` | `sequence_01_database_pipeline.md` / `.mmd` / `.svg` | AWS PostgreSQL `cms` database, Mongo raw lane, ontology, Graphify, vector DB target 연결을 보여준다. |
| Airflow pipeline | `flow_02_airflow_pipeline.md` / `.mmd` / `.svg` | `sequence_02_airflow_pipeline.md` / `.mmd` / `.svg` | schedule/manual trigger에서 batch/replay/report artifact까지의 background workflow를 보여준다. |
| LangGraph pipeline | `flow_03_langgraph_pipeline.md` / `.mmd` / `.svg` | `sequence_03_langgraph_pipeline.md` / `.mmd` / `.svg` | LangGraph가 review note와 approval recommendation만 만들고 DB write를 실행하지 않는 경계를 보여준다. |
| App pipeline | `flow_04_app_pipeline.md` / `.mmd` / `.svg` | `sequence_04_app_pipeline.md` / `.mmd` / `.svg` | FastAPI router, SQLLM SELECT guard, background job, approval request path를 보여준다. |

## Render/readability 기준

- `.mmd`는 canonical Mermaid source다.
- `.md`는 GitHub-renderable wrapper다.
- `.svg`는 현재 source에서 재생성한 shareable render다.
- Sequence SVG는 message text 뒤에 흰 배경 박스를 넣어 화살표와 label overlap을 줄인다.
- Render 검증은 파일 존재만으로 완료하지 않고 `foreignObject` count, SVG text/background count, PNG preview 생성으로 확인한다.

## Local render

```bash
python scripts/verify/render_diagrams.py --png-preview
```

이 script는 Mermaid CLI로 `.mmd`를 `.svg`로 렌더하고, sequence SVG의 message label background를 후처리한다. `--png-preview`는 `/tmp/cms_spec_diagram_render/png/`에 육안 검수용 PNG를 만든다.
