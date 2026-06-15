# SKN25-FINAL-4Team

## CMS 데이터 인사이트 및 live/replay runtime 프로젝트

본 저장소는 Honda R&D Europe Offenbach 시설의 공개 계측 데이터를 CMS 관점에서 정리하고, live/replay 데이터 처리, QA evidence, controlled promotion, report workflow의 service/runtime contract를 검증하는 팀 프로젝트다. Active runtime namespace와 문서 표기는 `CMS` / `cms`를 기준으로 한다.

## 1. 프로젝트 목표

| 영역 | 목표 |
|---|---|
| 데이터 기반 | 원천 논문, Dryad compressed source archive, CMS data platform contract를 기준 source로 정리한다. |
| 계량기 metadata | 81개 계량기 URN을 domain, role, equipment group, building, redundancy, sign convention 기준으로 정규화한다. |
| Data QA | live/replay, batch, scratch DB integration의 evidence level을 구분하고 QA packet을 생성한다. |
| Candidate/Canonical 경계 | live/replay output을 candidate/serving preview로 다루고, canonical write는 approval과 controlled promotion 이후로 제한한다. |
| Service/API | FastAPI는 quick status, read-only query, lightweight chat, manual job registration, artifact download interface를 담당한다. |
| Workflow | Airflow/scheduler/background worker가 batch, replay, scheduled report를 소유하며 LangGraph는 optional async review layer로 둔다. |
| 분석/모델링 | 15min/1h canonical 또는 명시적 preview source를 기반으로 CMS scenario, feature, model dry-run을 검증한다. |

## 2. 기준 데이터

| 항목 | 내용 |
|---|---|
| 데이터셋 | Smart company building optimization and machine learning용 실측 에너지 관리 데이터셋 |
| 논문 | Scientific Data 2025, DOI `10.1038/s41597-025-05186-3` |
| 데이터 저장소 | Dryad DOI `10.5061/dryad.73n5tb363` |
| 수집 시설 | Honda R&D Europe, Offenbach am Main, Germany |
| 수집 기간 | 2018-01-01 00:00 GMT+1 ~ 2024-01-01 00:00 GMT+1 |
| 로컬 시간대 | Europe/Berlin |
| 계측 범위 | 전력, 열·냉방, PV, CHP, 기상 |
| 계량기 수 | 81개 URN |
| runtime convention | PostgreSQL database/user `cms`, observed canonical tables `canonical.measurement_1min/15min/1h`, reference corrected/resampled tables `reference.corrected_resampled_15min/1h` |

분석 grain은 다음 조합을 기본으로 둔다.

```text
(ts, meter_urn, measurement, resolution)
```

주요 해상도는 `15min`, `1h`다. `1min`과 `5min`은 live equalization 또는 scratch branch에서 evidence level을 명시한 뒤 사용한다.

## 3. Architecture

CMS 구조는 Data plane, Service plane, Workflow plane을 분리한다.

```text
Live input
  -> FastAPI ingestion
  -> Kafka measurement_raw_v1
  -> kafka_to_postgres_consumer
  -> PostgreSQL live event / processor and interval/NULL logic
Source archive / historical replay
  -> PostgreSQL staging/backfill path
  -> processor and interval/NULL logic
  -> candidate output + QA evidence
  -> ops.promotion_request
  -> approval + controlled promotion role
  -> canonical.measurement_15min / canonical.measurement_1h
  -> mart / API / report / model read paths
```

FastAPI는 낮은 latency가 필요한 service path를 담당한다. LangGraph는 synchronous chat path에 배치하지 않고, report review, QA evidence packet review, replay planning, approval review, incident review, model inference dry-run 같은 비동기 workflow에 사용한다.

정기 report와 replay/backfill은 FastAPI가 아니라 Airflow, scheduler, report worker가 소유한다. FastAPI는 report 상태 조회, artifact download, manual job registration interface를 제공한다.

## 4. 저장소 구조

```text
SKN25-FINAL-4Team/
├── readme.md
├── pyproject.toml
├── requirements.txt
├── docker_compose.yml
├── docker/
│   ├── backend_containerfile
│   ├── model_serving_containerfile
│   ├── compose_edge_stream.yml
│   ├── compose_local_kafka_broker.yml
│   ├── compose_model_serving.yml
│   └── compose_aws_db.yml
├── docs/
│   ├── specs/              # project specifications
│   ├── qa/                 # QA contract and test gates
│   ├── reference/          # source inventory and measurement glossary
│   └── ontology/           # RDF/OWL/SHACL ontology artifacts
├── scripts/
│   ├── ontology/           # ontology generation, validation, query scripts
│   ├── live/               # live/replay dry-run and QA latency smoke scripts
│   ├── migrations/         # offline migration draft generators
│   ├── scratch/            # scratch DB integration scripts
│   └── verify/             # skeleton/query/migration verification scripts
├── src/
│   └── cms/                # CMS package
│       ├── contracts/      # data/agent/timestamp contract models
│       ├── data/           # Data plane: live/replay, timestamp QA, scratch DB
│       ├── service/        # Service plane: FastAPI/query planner
│       ├── workflow/       # Workflow plane: Airflow / LangGraph adapters
│       └── ontology/       # ontology helper module
└── tests/                  # unit and integration tests
```

Graphify 산출물은 active service source가 아니다. `docs/specs/knowledge_db_contract.md`와 wiki-side graph copy를 기준으로 하며, project-root generated graph output은 push 대상에서 제외한다.

## 5. Git 추적 기준

| 경로 | 기준 |
|---|---|
| `docs/specs/`, `docs/qa/`, `docs/reference/`, `docs/ontology/` | 공유 기준 문서, QA contract, reference, ontology artifact. `docs/specs/diagrams/*.svg` render는 diagram source의 shareable artifact로 함께 추적한다. |
| `scripts/ontology/`, `scripts/live/`, `scripts/stream/`, `scripts/serving/`, `scripts/migrations/`, `scripts/scratch/`, `scripts/verify/` | ontology, dry-run, migration draft, smoke, scratch guard, contract verification code |
| `src/cms/` | CMS Python package |
| `tests/` | unit and integration tests |
| `.env`, `.venv`, `data/`, `outputs/`, `artifacts/`, `reports/`, `notebooks/`, `docs/plans/`, `**/_archive/` | local-only, generated, planning archive, external archive, or ignored runtime artifact |

## 6. 실행 환경

### Python

`pyproject.toml`은 Python `>=3.12,<3.13`을 선언한다. Project verification은 Python 3.12 virtualenv 기준으로 수행한다.

```bash
python --version
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`pytest`는 pytest-based tests를 실행하기 위한 dev/test dependency다. 환경에 없으면 pytest test suite는 `not installed`로 보고한다.

### Docker compose 검증

```bash
docker compose config --quiet
```

Runtime package namespace는 `cms`다. Service/container/image name을 변경할 때는 Docker volume/data compatibility를 별도 확인한다.

## 7. 검증 명령

### Contract guard smoke

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src   python scripts/verify/verify_skeleton_contracts.py
```

성공 기준:

```text
cms skeleton contracts ok  # legacy guard output string
```

### Python syntax check

```bash
PYTHONDONTWRITEBYTECODE=1 python - <<'PY'
from pathlib import Path
for base in ['scripts', 'src', 'tests']:
    for path in Path(base).rglob('*.py'):
        if any(part in {'__pycache__', '_archive'} for part in path.parts):
            continue
        compile(path.read_text(encoding='utf-8'), str(path), 'exec')
print('syntax ok')
PY
```

### Pytest

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q
```

If `pytest` is missing, install the test dependency or report the environment blocker. Do not report pytest as passed when the command is unavailable.

### Ontology artifact generation and validation

```bash
uv run --with rdflib --with pyshacl --with 'psycopg[binary]' --with python-dotenv   python scripts/ontology/generate_ontology.py

uv run --with rdflib --with pyshacl   python scripts/ontology/validate_ontology.py

uv run --with rdflib --with pyshacl   python scripts/ontology/query_ontology.py
```

## 8. 주요 문서

| 경로 | 내용 |
|---|---|
| `docs/specs/project_overview.md` | CMS architecture 개요 |
| `docs/specs/data_platform_contract.md` | source, Kafka/PostgreSQL live ingestion, candidate, QA evidence, canonical, feature/mart 경계 |
| `docs/specs/runtime_architecture.md` | Data/Service/Workflow plane, FastAPI, Airflow, LangGraph 책임 경계 |
| `docs/specs/measurement_processing_policy.md` | measurement cadence, NULL/state-hold, expected/coverage, canonical eligibility 정책 |
| `docs/specs/ontology_schema.md` | ontology class/property/artifact coverage와 역량질문 |
| `docs/specs/meter_metadata.md` | meter classification, role, redundancy, source metadata |
| `docs/specs/knowledge_db_contract.md` | Vector DB, pgvector, Graphify 기준 |
| `docs/specs/llm_contract.md` | LLM 역할, prompt boundary, retrieval routing, SQL safety |
| `docs/qa/qa_contract.md` | data QA, evidence level, report/chat route, live/replay latency 기준 |
| `docs/reference/source_inventory.md` | Honda Nature/Dryad source tier와 Vector DB 제외 기준 |
| `docs/reference/measurement_glossary.md` | 전기/열/기상 measurement와 전력 개념 참조 |
| `docs/specs/diagrams/readme.md` | pipeline diagram index와 render 기준 |

## 9. 보안 및 운영 경계

1. `.env`, DB password, SSH key, token은 Git에 커밋하지 않는다.
2. Production/canonical write, destructive SQL, 권한 변경은 사전 승인 후 실행한다.
3. Scratch DB write는 default-deny guard와 isolated target naming을 통과해야 한다.
4. Local dry-run이나 Docker scratch evidence를 AWS/live/production evidence로 승격하지 않는다.
5. Candidate output은 canonical이 아니며, approval과 controlled promotion 전에는 preview로만 다룬다.
6. 작업 산출물, 노트북, 발표·중간 산출물, 대용량 outputs는 archive 또는 ignored output 경로에 보존한다.

## 10. 산출 방향

- 기준 문서를 Vector DB 적재 source로 사용한다.
- CMS source, candidate, QA evidence, canonical boundary를 기준 문서로 고정한다.
- Markdown-first report package와 rendered diagram을 팀 공유 산출물로 유지한다.
- FastAPI service path와 Airflow/scheduler workflow path를 분리한다.
- LangGraph는 optional async review layer로 배치한다.
- Production/canonical work는 DB evidence, test gate, approval workflow를 먼저 정의한 뒤 진행한다.
