# Pipeline Diagram Explanations

이 문서는 `docs/specs/diagrams/`의 일반 diagram과 sequence diagram이 보여주는 pipeline 흐름만 설명한다.

## 전체 pipeline

`flow_00_overall_pipeline`은 CMS 전체 구조를 Source plane, Data plane, Workflow plane, Application plane으로 나누어 보여준다. Honda Nature/Dryad source와 live replay event는 MongoDB/PostgreSQL processing lane으로 들어가고, QA evidence와 approval request를 거친 뒤에만 canonical observed table에 반영된다. Ontology, Graphify `graphify-out`, vector DB target은 Application plane의 grounding/context source로만 연결된다.

`sequence_00_overall_pipeline`은 같은 구조를 실행 순서로 압축한다. Source load, QA, approval, canonical write, context retrieval, guarded SELECT, Airflow/LangGraph artifact publication 순서를 보여준다.

## DB pipeline

`flow_01_database_pipeline`은 AWS PostgreSQL `cms` database의 `staging`, `reference`, `canonical`, `qa`, `ops`, planned `vector` target과 Mongo raw buffer의 관계를 보여준다. 현재 Graphify와 ontology는 DB server가 아니라 App/LLM grounding source로 연결된다.

`sequence_01_database_pipeline`은 source event가 Mongo raw lane 또는 PostgreSQL reference/staging lane으로 들어가고, QA evidence와 ops approval을 거쳐 canonical observed table로 promotion되는 순서를 보여준다. App은 read-only SELECT evidence만 조회한다.

## Airflow pipeline

`flow_02_airflow_pipeline`은 schedule/manual trigger에서 source inventory, load/replay, QA gate, approval packet, report build, artifact store로 이어지는 background workflow를 보여준다. Scheduled report는 FastAPI 직접 실행이 아니라 Airflow/worker 쪽 책임이다.

`sequence_02_airflow_pipeline`은 Airflow DAG가 DB input/output을 읽고 쓰는 scratch/batch 단계, QA caveat 수신, optional LangGraph review, artifact status publication 순서를 보여준다.

## LangGraph pipeline

`flow_03_langgraph_pipeline`은 LangGraph를 async review workflow로만 배치한다. 입력은 report plan, approval wording, replay plan 같은 review target이고, 출력은 review artifact, approval recommendation, caveat list다.

`sequence_03_langgraph_pipeline`은 FastAPI가 review를 요청하면 LangGraph가 Graphify/vector/ontology/spec context를 조회하고 QA evidence를 확인한 뒤 review note를 남기는 순서를 보여준다. LangGraph는 DB write, promotion, deployment를 실행하지 않는다.

## App pipeline

`flow_04_app_pipeline`은 FastAPI lightweight router의 분기를 보여준다. Quick answer, read-only evidence query, background job registration, approval request, mutation denial path가 분리되어 있다.

`sequence_04_app_pipeline`은 사용자 요청이 router로 들어와 quick answer, SQLLM SELECT-only evidence query, Airflow background job, LangGraph approval review 중 하나로 분기되는 실행 순서를 보여준다.
