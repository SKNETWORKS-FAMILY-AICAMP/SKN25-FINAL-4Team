# Pipeline Markdown Explanations

이 문서는 `docs/specs/diagrams/`의 네 개 pipeline Markdown 파일에 들어 있는 sequence 흐름만 설명한다.

## 01. `01_pre_model_pipeline.md`

### 범위

CMS 전체 pre-model pipeline을 source 입력부터 service status까지 순서대로 표현한다. Historical archive lane, live/replay lane, QA, approval, canonical/reference, model/mart, FastAPI, scheduler/review의 연결을 한 sequence로 압축한 MD다.

### 참여자

```text
Archive source
Live replay input
Data workers loader processor
QA evidence
Ops approval promotion
Canonical reference
Model mart features
FastAPI service
Scheduler workflow
LangGraph review
```

### Sequence

Historical/archive 입력은 archive source에서 data worker로 들어간다.

```text
Archive source
-> Data workers loader processor
-> QA evidence
-> Ops approval promotion
-> Canonical reference
```

`Archive source`는 manifest, harmonized product, corrected_resampled reference의 출발점이다. `Data workers`는 loader와 processor 역할을 묶은 participant이며, staging row와 quality evidence를 만든다. `QA evidence`는 coverage, duplicate, divergence 같은 검증 결과를 만든다. 검증 결과는 바로 canonical에 쓰이지 않고 `Ops approval promotion`으로 넘어간다. 승인된 observed fact만 `Canonical reference`에 반영된다.

Corrected/resampled reference는 archive source에서 canonical/reference participant로 직접 등록되는 별도 reference path다. 이 path는 observed canonical fact와 같은 의미가 아니다.

Live/replay 입력은 별도 경로로 data worker에 들어간다.

```text
Live replay input
-> Data workers loader processor
-> QA evidence
-> Ops approval promotion
```

이 메시지는 Mongo raw buffer, checkpoint/watermark, observed interval processor, candidate observed output을 하나의 sequence 단계로 압축한 것이다. Candidate output은 QA evidence를 만들 수 있지만 canonical이 아니다. Canonical 반영은 approval과 controlled promotion 이후에만 가능하다.

Canonical/reference는 model/mart feature를 공급한다.

```text
Canonical reference
-> Model mart features
-> FastAPI service
```

`Model mart features`는 anomaly input, forecast input, prediction results, mart/read model을 묶은 participant다. FastAPI는 이 read model과 status를 노출한다.

FastAPI가 장시간 작업을 직접 실행하지 않고 scheduler에 등록한다.

```text
FastAPI service
-> Scheduler workflow
-> Data workers loader processor
-> FastAPI service
```

Scheduler는 historical load, replay processor, report job을 실행하고 artifact path를 FastAPI 쪽으로 돌려준다.

LangGraph는 선택적 review participant다.

```text
Ops approval promotion -> LangGraph review
Scheduler workflow -> LangGraph review
LangGraph review -> FastAPI service
```

이 연결은 approval wording, report, replay plan review를 위한 것이다. 일반 chat path가 아니다.

## 02. `02_latency_sequence.md`

### 범위

`live81_1min_60m` scratch replay run의 실제 실행 순서를 나타낸다. 전체 architecture가 아니라 81개 synthetic source identifier, 60분 event, scratch MongoDB/PostgreSQL, QA, FastAPI status, report artifact의 순서와 count evidence를 설명하는 MD다.

### 참여자

```text
Source 81 streams
Mongo raw scratch
Cursor watermark
Processor equalizer
PostgreSQL scratch
QA evidence
FastAPI status
Report artifact
```

### Sequence

Source가 Mongo raw scratch에 raw document를 넣는다.

```text
Source 81 streams
-> Mongo raw scratch: insert 4,860 raw docs
```

Mongo side에서 raw count와 coverage를 확인한다.

```text
raw count = 4,860
minute ticks = 60
docs per tick = 81
docs per source = 60
```

Cursor/watermark가 processor window를 만든다.

```text
Mongo raw scratch
-> Cursor watermark
-> Processor equalizer
```

Window는 `00:00-01:00 UTC`다. Processor는 `meter_urn`별로 source batch를 읽는다.

```text
loop each meter_urn
    Processor equalizer -> Mongo raw scratch
    Mongo raw scratch -> Processor equalizer: 60 ordered events
end
```

Processor는 native 1min cadence를 적용하고 PostgreSQL scratch에 네 grain을 쓴다.

```text
measurement_1min = 4,860 rows
measurement_5min = 972 rows
measurement_15min = 324 rows
measurement_1h = 81 rows
```

QA는 PostgreSQL row count를 read-back한다.

```text
PostgreSQL scratch -> QA evidence: 4,860 / 972 / 324 / 81
```

15min bucket은 source마다 4개이고 각 bucket은 15/15 point를 가진다. 1h bucket은 source마다 1개이고 60/60 point를 가진다.

QA 결과는 status와 report로 나뉜다.

```text
QA evidence -> FastAPI status
QA evidence -> Report artifact
Report artifact -> FastAPI status
```

Latency evidence는 scratch replay 기준이다.

```text
mongo_visible_to_pg_outputs_sec = 2.385613
processor_to_postgres_sec = 2.385611
total_sec = 3.104198
```

이 값은 Uvicorn HTTP latency나 production throughput이 아니다.

## 03. `03_chat_routing.md`

### 범위

FastAPI lightweight router가 user request를 어떤 execution path로 분기하는지 표현한다. Data processing 자체가 아니라 service routing boundary를 설명하는 MD다.

### 참여자

```text
User request
FastAPI router
Quick answer
Evidence read only
Text to SQL guard
API job request
Worker scheduler
Approval request
LangGraph review
Artifact status
Deny write admin
```

### Sequence

모든 요청은 user request에서 FastAPI router로 들어온다.

```text
User request -> FastAPI router
```

Router는 요청을 네 가지 path로 분기한다.

첫 번째는 quick status answer다.

```text
FastAPI router
-> Quick answer
-> User request
```

이 path는 cache 또는 contract-level response로 처리할 수 있는 요청이다.

두 번째는 read-only evidence query다.

```text
FastAPI router
-> Evidence read only
-> Text to SQL guard
-> Evidence read only
-> User request
```

Evidence 대상은 canonical, QA, ops, mart다. Text-to-SQL guard는 SELECT-only scope를 강제한다.

Write 또는 admin attempt가 감지되면 deny path로 간다.

```text
Text to SQL guard
-> Deny write admin
-> User request
```

세 번째는 background work request다.

```text
FastAPI router
-> API job request
-> Worker scheduler
-> Artifact status
-> FastAPI router
-> User request
```

FastAPI는 job을 등록하고 status를 반환한다. 실제 long-running work는 worker scheduler가 실행한다.

네 번째는 promotion 또는 risky action이다.

```text
FastAPI router
-> Approval request
-> LangGraph review
-> Artifact status
-> FastAPI router
-> User request
```

Approval이 필요한 요청은 직접 실행하지 않고 review note와 approval status로 반환한다.

Job도 review가 필요하면 LangGraph review로 연결될 수 있다.

```text
API job request
-> LangGraph review
-> Artifact status
```

## 04. `04_airflow_report.md`

### 범위

Airflow schedule 또는 manual trigger에서 report artifact가 만들어지는 순서를 나타낸다. FastAPI chat route가 아니라 scheduler/report worker 중심의 보고서 생성 sequence다.

### 참여자

```text
Airflow schedule
Report packet
QA validation
Draft worker
LangGraph review
Render artifact
Artifact store
FastAPI endpoint
Notification adapter
```

### Sequence

보고서 pipeline은 schedule 또는 manual trigger에서 시작한다.

```text
Airflow schedule
-> Report packet
```

Report packet은 canonical, QA, ops evidence를 모은다.

```text
Report packet
-> QA validation
```

QA validation은 counts, windows, caveats를 확인한다. 검증된 packet만 draft worker로 넘어간다.

```text
QA validation
-> Draft worker
-> Render artifact
```

Draft worker는 Markdown draft와 table을 만든다. Review가 필요한 경우 LangGraph review를 거친다.

```text
Draft worker
-> LangGraph review
-> Render artifact
```

Render artifact는 최종 report artifact를 만든다.

```text
Render artifact
-> Artifact store
-> FastAPI endpoint
```

Artifact store는 immutable report packet을 보관한다. FastAPI endpoint는 download와 status를 제공한다.

Notification adapter는 저장된 artifact의 link 또는 attachment를 전달한다.

```text
Artifact store
-> Notification adapter
```

이 sequence에서 FastAPI는 report를 직접 생성하지 않는다. FastAPI는 저장된 artifact의 status와 download endpoint를 제공한다.
