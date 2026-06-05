# Pipeline Diagrams

**갱신일:** 2026-06-04  
**상태:** 통합 diagram index  
**범위:** 이 문서는 pipeline diagram의 목록, 각 diagram의 역할, Mermaid source/render 관리 기준을 정의한다.

## 1. 관리 원칙

- 각 diagram은 서로 다른 관점을 표현하므로 개별 `.mmd`와 `.svg` 파일을 유지한다.
- `.mmd`는 canonical Mermaid source다.
- `.svg`는 팀 공유와 Markdown preview를 위한 render 결과다.
- Mermaid source는 `.mmd`로 관리하고 설명은 이 문서에 모아 관리한다.
- Diagram 설명은 이 `README.md`에 모아 관리한다.
- `flow_01` / `sequence_01`은 live DB 처리와 canonical promotion의 상세 기준 diagram으로 유지한다.

## 2. Diagram 목록

| 구분 | Mermaid source | Render | 설명 |
|---|---|---|---|
| 전체 flow | `flow_00_overall_pipeline.mmd` | `flow_00_overall_pipeline.svg` | source/archive, Kafka live ingestion buffer, PostgreSQL staging/candidate/canonical, service/workflow plane의 전체 연결을 표현한다. |
| 전체 flow visual variant | `flow_00_reference_architecture.svg` | `flow_00_reference_architecture.png` | `flow_00_overall_pipeline`의 내용을 참조해 팀 공유용 reference architecture 스타일로 재배치한 시각화다. 원본 flow 00은 변경하지 않는다. |
| 전체 flow dark variant | `flow_00_dark_control_tower.svg` | `flow_00_dark_control_tower.png` | `flow_00_overall_pipeline`을 dark control-tower 스타일로 재배치한 발표/공유용 시각화다. |
| 전체 flow reference-photo variant | `flow_00_reference_photo_style.svg` | `flow_00_reference_photo_style.png` | 첨부 레퍼런스의 white background, red header/boundary, icon grid, orange arrow 스타일을 기준으로 재배치한 시각화다. |
| DB live/canonical flow | `flow_01_database_pipeline.mmd` | `flow_01_database_pipeline.svg` | 1개월 live event가 FastAPI, Kafka, PostgreSQL live processing, 목적별 rollup, QA eligibility, canonical promotion으로 이어지는 상세 data plane을 표현한다. |
| DB data platform flow | `flow_02_data_platform_pipeline.mmd` | `flow_02_data_platform_pipeline.svg` | deprecated/historical data-platform view다. MongoDB raw buffer 표기는 legacy/debug-only 경계이며 별도 승인 없이는 Phase 1 live ingestion path가 아니다. |
| Airflow flow | `flow_03_airflow_pipeline.mmd` | `flow_03_airflow_pipeline.svg` | scheduler, batch, replay, report worker의 workflow 책임을 표현한다. |
| LangGraph flow | `flow_04_langgraph_pipeline.mmd` | `flow_04_langgraph_pipeline.svg` | LangGraph가 일반 chat path가 아니라 review/QA/approval workflow 뒤에 위치한다는 경계를 표현한다. |
| App flow | `flow_05_app_pipeline.mmd` | `flow_05_app_pipeline.svg` | FastAPI, dashboard, Text-to-SQL, artifact download 등 service plane을 표현한다. |
| 전체 sequence | `sequence_00_overall_pipeline.mmd` | `sequence_00_overall_pipeline.svg` | request, job registration, processing, evidence, approval까지의 전체 순서를 표현한다. |
| DB live/canonical sequence | `sequence_01_database_pipeline.mmd` | `sequence_01_database_pipeline.svg` | live event ingest, trigger, 1min alignment, 목적별 rollup, QA eligibility, canonical promotion의 순서를 표현한다. |
| DB data platform sequence | `sequence_02_data_platform_pipeline.mmd` | `sequence_02_data_platform_pipeline.svg` | deprecated/historical data-platform sequence다. MongoDB ingest 표기는 legacy/debug-only 경계이며 별도 승인 없이는 Phase 1 live ingestion path가 아니다. |
| Airflow sequence | `sequence_03_airflow_pipeline.mmd` | `sequence_03_airflow_pipeline.svg` | scheduled report, replay, batch worker 실행 순서를 표현한다. |
| LangGraph sequence | `sequence_04_langgraph_pipeline.mmd` | `sequence_04_langgraph_pipeline.svg` | QA/review/approval recommendation workflow의 순서를 표현한다. |
| App sequence | `sequence_05_app_pipeline.mmd` | `sequence_05_app_pipeline.svg` | FastAPI quick response, read-only query, artifact/status response의 순서를 표현한다. |

## 3. Plane 기준

| Plane | 포함 | 제외 |
|---|---|---|
| Data plane | source archive, Kafka live ingestion buffer, PostgreSQL live/staging/candidate/canonical, QA evidence | 외부 알림 또는 운영 전달 경로 |
| Service plane | FastAPI, dashboard, Text-to-SQL, read-only query, job registration, artifact download | bulk ETL, canonical promotion 직접 실행 |
| Workflow plane | Airflow, scheduler, batch/report worker, optional LangGraph review workflow | synchronous chat path |

## 4. Render 기준

Render 설정은 `mermaid_render_config.json`을 사용한다.

```bash
mmdc -c docs/specs/diagrams/mermaid_render_config.json \
  -i docs/specs/diagrams/flow_00_overall_pipeline.mmd \
  -o docs/specs/diagrams/flow_00_overall_pipeline.svg
```

전체 재렌더가 필요하면 `.mmd`별로 동일 설정을 적용한다. SVG readability 검증 기준은 다음과 같다.

```text
foreignObject == 0
nodeLabel == 0
text_tags > 0
```

## 5. 수정 규칙

1. Diagram 의미를 바꿀 때는 `.mmd`를 먼저 수정한다.
2. `.mmd` 수정 후 대응 `.svg`를 재생성한다.
3. 새 diagram을 추가하면 이 `README.md`의 목록에 source/render/설명을 추가한다.
4. Mermaid 설명 문서는 이 `README.md` 하나로 유지한다.
