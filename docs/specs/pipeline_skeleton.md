# CMS 파이프라인 스켈레톤 명세

**최종 업데이트:** 2026-05-30

이 문서는 분석 모델이 들어오기 전에 확정 가능한 CMS 데이터 운영 골격을 정의한다. 범위는 데이터 유입, MongoDB buffer, 등간격 measurement 후보 생성, QA/quarantine, PostgreSQL canonical 적재, FastAPI/Airflow/LangGraph 위치 지정까지다. 모델 feature, 예측, 이상탐지 모델, dashboard 전용 mart, LangGraph 상세 node logic은 이 문서 범위 밖이다.

---

## 1. 확정 원칙

```text
PostgreSQL database = cms
MongoDB database    = cms
```

PostgreSQL은 `cms` database 안에서 schema/table로 분리한다. MongoDB는 `cms` database 안에서 collection으로 분리한다.

운영 DB naming에서는 `resampled` 대신 `measurement`를 사용한다.

```text
reference lineage: reference.corrected_resampled_1min/15min/1h
runtime table:     canonical.measurement_1min
runtime table:     canonical.measurement_15min
runtime table:     canonical.measurement_1h
```

`resampled`는 원천 파일명, reference schema, lineage 설명, 처리 이력 metadata에서만 사용한다.

---

## 2. 구성 범위

### 지금 구성한다

- source/archive catalog
- MongoDB live/replay buffer collection 계약
- measurement interval worker 계약
- QA/quarantine 계약
- PostgreSQL canonical measurement 계약
- ops run/file/split/job 상태 계약
- FastAPI read/status/job/chat shell
- Airflow disabled-by-default DAG shell
- LangGraph workflow 위치. 일반 chat path가 아니라 report / QA review / replay planning / approval review에서 선택 사용
- email delivery contract

### 지금 구성하지 않는다

- model feature mart
- prediction table
- anomaly model result table
- dashboard-specific mart
- LangGraph 상세 node logic
- LLM prompt 상세
- 실제 email 자동 발송

---

## 3. PostgreSQL schema 구조

```text
cms
├── archive
├── staging
├── reference
├── canonical
├── qa
├── ops
└── mart
```

| Schema | 책임 |
|---|---|
| `archive` | 압축 원본, manifest, checksum, source catalog |
| `staging` | 임시 load, worker scratch, promote 전 buffer |
| `reference` | corrected_resampled reference data와 provenance |
| `canonical` | 검증된 measurement fact |
| `qa` | 품질 검증, quarantine, coverage |
| `ops` | run, file, split, replay, job 상태 |
| `mart` | dashboard/API/model/LLM 목적별 table/view. 현재 보류 |

현재 canonical truth table은 다음이다.

```text
canonical.measurement_1min
canonical.measurement_15min
canonical.measurement_1h
```

---

## 4. MongoDB collection 구조

MongoDB database는 `cms` 하나만 사용한다.

```text
cms
├── measurement_raw
├── measurement_buffer
├── measurement_reject
├── measurement_cursor
└── measurement_read_cache
```

| Collection | 책임 |
|---|---|
| `measurement_raw` | live/replay source event 원형 buffer |
| `measurement_buffer` | 등간격 조정 후 controlled promotion 전 candidate/preview buffer |
| `measurement_reject` | parse/normalize/candidate generation 실패 event의 임시 reject buffer |
| `measurement_cursor` | replay/live cursor, watermark, offset |
| `measurement_read_cache` | dashboard/chat 최근 window cache |

MongoDB는 장기 분석 truth가 아니다.

```text
MongoDB = recent live/replay buffer + cache + transient quarantine
PostgreSQL canonical = long-term truth
```

---

## 5. 정상/비정상 데이터 분기

### 정상 path

```text
source event
-> MongoDB cms.measurement_raw
-> measurement_interval_worker
-> candidate / serving preview
-> qa_gate / model_mask / promotion evidence
-> ops state update

candidate가 canonical로 이동하는 경우:
promotion evidence
-> ops.promotion_request
-> approval + controlled promotion role
-> PostgreSQL canonical.measurement_1min / canonical.measurement_15min / canonical.measurement_1h
```

### 비정상 data-quality path

```text
source event
-> measurement_interval_worker 또는 qa_gate fail
-> MongoDB cms.measurement_reject
-> PostgreSQL qa.measurement_quarantine
-> qa.measurement_check_result
```

데이터 품질 이상과 설비 이상 후보는 분리한다.

```text
data quality issue
  timestamp 오류, 중복, 결측, non-finite, 단위 오류, coverage 부족

operation anomaly candidate
  모델 또는 rule 분석 이후 발견되는 설비/운영 이상 후보
```

분석 모델 전에는 data quality issue만 이 skeleton에서 다룬다.

---

## 6. Latency budget 기준

### Live ingestion

| Stage | 목표 p95 | 상한 | 비고 |
|---|---:|---:|---|
| event receive | 50~200ms | 500ms | API/MQTT/worker 수신 |
| Mongo `measurement_raw` write | 50~200ms | 500ms | 단건 또는 micro-batch |
| equal interval buffer update | 200ms~2s | 5s | window 크기 의존 |
| QA light check | 100ms~1s | 3s | schema/type/range/duplicate |
| candidate preview availability | 0.5~5s | 10s | scratch/candidate write and read-back 기준 |
| ops/qa state write | 50~300ms | 1s | run/counter 갱신 |

Canonical promotion latency는 live ingestion SLA가 아니라 approval + controlled promotion 절차의 별도 SLA로 측정한다. 권장 micro-batch 기준은 둘 중 먼저 도달하는 조건이다.

```text
interval: 5s ~ 60s
row threshold: 1k ~ 50k rows
```

### Dashboard read

| Query type | 목표 p95 | 처리 |
|---|---:|---|
| latest status | 300ms~1s | Mongo cache 또는 ops table |
| recent 15min window | 1~2s | canonical index read |
| daily summary | 1~3s | cached summary 또는 future mart |
| wide dashboard panel | 2~5s | 반복되면 mart 후보 |

### Chat

| Route | 목표 p95 | 처리 |
|---|---:|---|
| `quick_answer` | 1~3s | cache/packet/summary |
| `evidence_answer` | 3~8s | canonical/qa/ops 근거 조회 |
| `needs_job` | 0.5~2s | `ops.api_job` 생성 후 job_id 반환 |
| `approval_required` | 0.5~2s | 승인 요청 반환 |

### Report

정기 보고서는 chat SLA와 분리한다. 느려도 되지만 재현성과 검증을 우선한다.

| Stage | 허용 |
|---|---:|
| QA summary | 수십 초~수 분 |
| report packet build | 수십 초~수 분 |
| LLM draft shell | 수십 초 |
| numeric validation | 수십 초~수 분 |
| critic/revision | 수십 초 |
| render/email | 수 초~수십 초 |

---

## 7. Component skeleton

### Worker

```text
measurement_ingest_worker
measurement_interval_worker
measurement_promote_worker
measurement_qa_worker
```

| Worker | Input | Output |
|---|---|---|
| `measurement_ingest_worker` | live/replay event | `measurement_raw`, `ops.measurement_load_run` |
| `measurement_interval_worker` | `measurement_raw` | candidate/serving preview, `measurement_reject`, `measurement_cursor` |
| `measurement_promote_worker` | 승인된 promotion request + QA evidence | `canonical.measurement_*`, `ops.*` |
| `measurement_qa_worker` | canonical/window/buffer | `qa.measurement_*` |

### FastAPI shell

```text
GET  /health
GET  /contracts
GET  /measurements/window
GET  /measurements/latest
GET  /qa/checks
GET  /qa/quarantine
GET  /ops/runs
GET  /ops/jobs/{job_id}
POST /live-replay/plan
POST /jobs
POST /chat
```

FastAPI는 직접 무거운 계산을 하지 않는다. 읽기, 상태 조회, job handoff, chat shell만 담당한다.

### Airflow shell

Airflow는 disabled-by-default에서 시작한다.

```text
cms_measurement_qa
cms_live_replay
cms_report_daily
```

### Chat shell 및 LangGraph workflow

사용자-facing chat path는 응답 속도를 위해 lightweight router와 read-only service로 처리한다. LangGraph 상세 node logic은 별도 설계하며, 일반 chat 응답 경로가 아니라 background job, report, replay planning, QA review, approval review처럼 상태 추적이 필요한 workflow에 선택적으로 배치한다.

```text
FastAPI /chat
-> lightweight router
   -> quick_answer
   -> read_only_evidence
   -> create ops.api_job
   -> create approval_request

ops.api_job / approval_request / scheduler
-> optional LangGraph workflow
   -> report / QA / replay planning / approval review
```

정기 보고서는 FastAPI 아래가 아니라 Airflow/scheduler/report worker가 소유한다. LangGraph는 report packet 이후 draft, caveat, claim review가 필요한 경우 선택적으로 들어간다.

```text
Airflow cms_report_daily
-> build_report_packet
-> optional LangGraph report workflow
-> validate output
-> render
-> artifact store
-> notification adapter
```

LangGraph는 bulk ETL, Mongo write, canonical promotion write, index 생성, feature/model 계산, email 직접 발송을 하지 않는다.

---

## 8. Mermaid diagram 파일

Mermaid diagram은 본문에 길게 붙이지 않고 파일별로 분리한다. GitHub에서는 `.md` 파일의 Mermaid block이 바로 렌더링되고, `.mmd` 파일은 Mermaid CLI용 원본으로 사용한다. `.svg`는 현재 source에서 재생성한 shareable render이며, sequence SVG에는 message label 뒤 흰 배경 박스를 적용한다.

| 범위 | 일반 diagram | Sequence diagram |
|---|---|---|
| 전체 pipeline | [`flow_00_overall_pipeline.md`](diagrams/flow_00_overall_pipeline.md) / [`.mmd`](diagrams/flow_00_overall_pipeline.mmd) / [`.svg`](diagrams/flow_00_overall_pipeline.svg) | [`sequence_00_overall_pipeline.md`](diagrams/sequence_00_overall_pipeline.md) / [`.mmd`](diagrams/sequence_00_overall_pipeline.mmd) / [`.svg`](diagrams/sequence_00_overall_pipeline.svg) |
| DB pipeline | [`flow_01_database_pipeline.md`](diagrams/flow_01_database_pipeline.md) / [`.mmd`](diagrams/flow_01_database_pipeline.mmd) / [`.svg`](diagrams/flow_01_database_pipeline.svg) | [`sequence_01_database_pipeline.md`](diagrams/sequence_01_database_pipeline.md) / [`.mmd`](diagrams/sequence_01_database_pipeline.mmd) / [`.svg`](diagrams/sequence_01_database_pipeline.svg) |
| Airflow pipeline | [`flow_02_airflow_pipeline.md`](diagrams/flow_02_airflow_pipeline.md) / [`.mmd`](diagrams/flow_02_airflow_pipeline.mmd) / [`.svg`](diagrams/flow_02_airflow_pipeline.svg) | [`sequence_02_airflow_pipeline.md`](diagrams/sequence_02_airflow_pipeline.md) / [`.mmd`](diagrams/sequence_02_airflow_pipeline.mmd) / [`.svg`](diagrams/sequence_02_airflow_pipeline.svg) |
| LangGraph pipeline | [`flow_03_langgraph_pipeline.md`](diagrams/flow_03_langgraph_pipeline.md) / [`.mmd`](diagrams/flow_03_langgraph_pipeline.mmd) / [`.svg`](diagrams/flow_03_langgraph_pipeline.svg) | [`sequence_03_langgraph_pipeline.md`](diagrams/sequence_03_langgraph_pipeline.md) / [`.mmd`](diagrams/sequence_03_langgraph_pipeline.mmd) / [`.svg`](diagrams/sequence_03_langgraph_pipeline.svg) |
| App pipeline | [`flow_04_app_pipeline.md`](diagrams/flow_04_app_pipeline.md) / [`.mmd`](diagrams/flow_04_app_pipeline.mmd) / [`.svg`](diagrams/flow_04_app_pipeline.svg) | [`sequence_04_app_pipeline.md`](diagrams/sequence_04_app_pipeline.md) / [`.mmd`](diagrams/sequence_04_app_pipeline.mmd) / [`.svg`](diagrams/sequence_04_app_pipeline.svg) |

Diagram index와 설명은 다음 파일이다.

```text
docs/specs/diagrams/README.md
docs/specs/diagrams/pipeline_explanations.md
```

---

## 9. Git 반영 방식

이 명세와 contract code는 파일로 commit하면 팀원이 clone/pull 즉시 확인할 수 있다.

```bash
git add \
  docs/specs/pipeline_skeleton.md \
  docs/specs/diagrams/ \
  src/cms/contracts/measurement.py \
  src/cms/contracts/qa.py \
  src/cms/contracts/job.py \
  scripts/verify/verify_skeleton_contracts.py

git commit -m "docs: add CMS measurement pipeline skeleton"
```

검증 명령:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
```
