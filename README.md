# SKN25-FINAL-4Team

## CMS 데이터 인사이트 및 live/replay skeleton 프로젝트

본 저장소는 독일 Honda R&D Europe Offenbach 시설의 공개 EMS 계측 데이터를 CMS 관점에서 분석하고, live/replay 데이터 처리와 QA evidence, controlled promotion, report workflow의 skeleton을 검증하는 팀 프로젝트입니다. 과거 분석 산출물에는 PostgreSQL `ems` schema와 `ems.cr_measurement_*` mart 표현이 남아 있을 수 있으나, 현재 active architecture는 `src/cms` package와 CMS runtime contract를 기준으로 관리합니다.

---

## 1. 프로젝트 목표

| 영역 | 현재 목표 |
|---|---|
| 데이터 기반 | EMS 원천 데이터와 compressed source archive를 CMS pipeline contract로 정리합니다. |
| 계량기 metadata | 81개 계량기 URN을 domain, role, equipment group, building, redundancy, sign convention 기준으로 정규화합니다. |
| Data QA | live/replay, batch, scratch DB integration의 evidence level을 구분하고 QA packet을 생성합니다. |
| Candidate/Canonical 경계 | live/replay output을 candidate/serving preview로 다루고, canonical write는 approval과 controlled promotion 이후로 제한합니다. |
| Service/API | FastAPI는 quick status, read-only query, lightweight chat, manual job registration, artifact download interface를 담당합니다. |
| Workflow | Airflow/scheduler/background worker가 batch, replay, scheduled report를 소유하며 LangGraph는 optional async review layer로 둡니다. |
| 분석/모델링 | 15min/1h canonical 또는 명시적 preview source를 기반으로 CMS scenario, feature, model dry-run을 검증합니다. |

---

## 2. 기준 데이터

| 항목 | 내용 |
|---|---|
| 데이터셋 | Smart company building EMS optimization and machine learning용 실측 에너지 관리 데이터셋 |
| 논문 | Scientific Data 2025, DOI `10.1038/s41597-025-05186-3` |
| 데이터 저장소 | Dryad DOI `10.5061/dryad.73n5tb363` |
| 수집 시설 | Honda R&D Europe, Offenbach am Main, Germany |
| 수집 기간 | 2018-01-01 00:00 GMT+1 ~ 2024-01-01 00:00 GMT+1 |
| 로컬 시간대 | Europe/Berlin |
| 계측 범위 | 전력, 열·냉방, PV, CHP, 기상 |
| 계량기 수 | 81개 URN |
| current physical DB | AWS PostgreSQL `cms` database/user 확인됨. `timescaledb 2.27.1` 설치, `vector` extension은 아직 미설치 |
| runtime convention | PostgreSQL database/user `cms`, observed canonical tables `canonical.measurement_1min/15min/1h`, reference corrected/resampled tables `reference.corrected_resampled_15min/1h` |
| legacy 분석 저장소 | `ems` schema, `ems.cr_measurement_15min/1h` mart |

분석 grain은 다음 조합을 기본으로 둡니다.

```text
(ts, meter_urn 또는 meter_id, measurement, resolution)
```

주요 해상도는 `15min`, `1h`입니다. `1min`과 `5min`은 live equalization 또는 scratch branch에서 evidence level을 명시한 뒤 사용합니다.

---

## 3. Current architecture

현재 구조는 Data plane, Service plane, Workflow plane을 분리합니다.

```text
Source archive / live input
  -> MongoDB raw buffer or PostgreSQL staging
  -> processor and interval/gap logic
  -> candidate output + QA evidence
  -> ops.promotion_request
  -> approval + controlled promotion role
  -> canonical.measurement_15min / canonical.measurement_1h
  -> mart / API / report / model read paths
```

FastAPI는 일반 사용자 요청의 낮은 latency를 위해 lightweight router와 read-only service 중심으로 둡니다. LangGraph는 일반 `/chat` path에 기본 삽입하지 않고, report review, QA evidence packet review, replay planning, approval review, incident review, model inference dry-run 같은 비동기 workflow에만 선택적으로 사용합니다.

DB naming cutover는 완료된 상태를 기준으로 합니다. 2026-06-02 AWS read-only smoke 기준은 `current_database=cms`, `current_user=cms`, PostgreSQL `16.14`입니다. `vector` extension과 `archive`/`mart` schema는 아직 적용되지 않은 target contract입니다. 비밀번호 값은 repository 문서와 evidence file에 저장하지 않습니다.

정기 report와 replay/backfill은 FastAPI가 아니라 Airflow, scheduler, report worker가 소유합니다. FastAPI는 report 상태 조회, artifact download, manual job registration interface를 제공합니다.

---

## 4. 저장소 구조

```text
SKN25-FINAL-4Team/
├── README.md
├── pyproject.toml
├── requirements.txt
├── docker-compose.yml
├── docker/
│   └── Dockerfile
├── docs/
│   ├── specs/              # current project specifications
│   ├── qa/                 # QA contracts, evidence matrices, test gates
│   ├── reference/          # domain and policy references
│   └── ontology/           # RDF/OWL/SHACL ontology artifacts
├── reports/
│   └── mermaid_20260601/  # retained historical Mermaid report package
├── scripts/
│   ├── ontology/           # ontology generation, validation, query scripts
│   ├── live/               # live/replay dry-run and QA latency smoke scripts
│   ├── migrations/         # offline migration draft generators
│   ├── scratch/            # local scratch DB integration scripts
│   └── verify/             # skeleton/query/migration verification scripts
├── src/
│   └── cms/                # active CMS package (plane-separated subpackages)
│       ├── contracts/      # data/agent/timestamp contract models
│       ├── data/           # Data plane: live/replay, timestamp QA, scratch DB
│       ├── service/        # Service plane: FastAPI/query planner
│       ├── workflow/       # Workflow plane: Airflow / LangGraph skeletons
│       └── ontology/       # ontology helper module
└── tests/                  # unit and integration tests
    ├── contracts/
    ├── data/
    ├── integration/
    ├── live/
    ├── migrations/
    ├── service/
    ├── verify/
    ├── workflow/
    └── test_api_dry_run.py
```

Folder-local `.hermes.md` navigation maps are not active project files. If local agents regenerate them, Git ignores them and they should not be treated as shared deliverables. `HERMES.md` is kept in `.gitignore` only as a legacy safety net.

Project-root `images/` is not an active folder. `graphify-out/` is restored as the local specs-only Graphify context graph for future MCP/agent pipeline calls; `/home/viowlet/wiki/graphify/skn25_cms/` remains the durable synced copy.

---

## 5. Git 추적 기준

| 경로 | 기준 |
|---|---|
| `docs/specs/`, `docs/qa/`, `docs/reference/`, `docs/ontology/` | 공유 기준 문서, QA contract, reference, ontology artifact. `docs/specs/diagrams/*.svg` render는 현재 diagram source의 shareable artifact로 함께 추적 |
| `reports/mermaid_20260601/` | retained historical Mermaid report package; current pipeline diagram canon is `docs/specs/diagrams/` |
| `scripts/ontology/`, `scripts/live/`, `scripts/migrations/`, `scripts/scratch/`, `scripts/verify/` | ontology, dry-run, migration draft, smoke, scratch guard, contract verification code |
| `src/cms/` | active CMS Python package |
| `tests/` | unit and integration tests |
| `.env`, `.venv`, `data/`, `outputs/`, `notebooks/`, `docs/plans/`, `**/_archive/`, `.hermes.md` | local-only, generated, superseded planning history, external archive, or ignored navigation metadata |

---

## 6. 실행 환경

### Python

`pyproject.toml`은 Python `>=3.12,<3.13`을 선언합니다. 따라서 project verification은 Python 3.12 virtualenv 기준이 가장 안전합니다. 현재 shell에서 더 높은 Python 버전으로 syntax check가 통과하더라도, 이를 3.12 compatibility 검증으로 보고하지 않습니다.

```bash
python --version
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`pytest`는 pytest-based tests를 실행하기 위한 dev/test dependency입니다. 현재 환경에 없으면 pytest test suite는 `not installed`로 보고해야 합니다.

### Docker compose 검증

```bash
docker compose config --quiet
```

Docker compose 파일에는 legacy service naming이 남을 수 있습니다. Runtime package namespace는 `cms`입니다. Service/container/image name을 변경할 때는 Docker volume/data compatibility를 별도 확인합니다.

---

## 7. 검증 명령

### Skeleton contract smoke

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python scripts/verify/verify_skeleton_contracts.py
```

성공 기준:

```text
cms skeleton contracts ok
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
uv run --with rdflib --with pyshacl --with 'psycopg[binary]' --with python-dotenv \
  python scripts/ontology/generate_ontology.py

uv run --with rdflib --with pyshacl \
  python scripts/ontology/validate_ontology.py

uv run --with rdflib --with pyshacl \
  python scripts/ontology/query_ontology.py
```

---

## 8. 주요 문서

| 경로 | 내용 |
|---|---|
| `docs/specs/project_overview.md` | current active architecture overview |
| `docs/specs/data_contract.md` | source, staging, candidate, QA evidence, canonical data boundary |
| `docs/specs/database_schema.md` | `cms` database schema layout and controlled promotion boundary |
| `docs/specs/pipeline_skeleton.md` | Data/Service/Workflow plane skeleton |
| `docs/specs/application_skeleton.md` | FastAPI, Airflow, LangGraph responsibility boundary |
| `docs/specs/mongo_live_replay_contract.md` | MongoDB live/replay raw buffer and candidate boundary |
| `docs/qa/live_stream_qa_latency_matrix.md` | evidence level and latency test matrix |
| `docs/specs/feature_spec.md` | production feature vs candidate preview feature boundary |
| `docs/specs/ontology_schema.md` | ontology class/property/artifact coverage 기준 |
| `docs/specs/knowledge_db_contract.md` | vector DB 기준, pgvector 준비, Graphify/MCP hook 기준 |
| `docs/specs/llm_pipeline_contract.md` | pipeline별 LLM 역할과 prompt boundary |
| `docs/qa/anomaly_service_data_qa_contract.md` | observed/canonical QA contract와 leakage block rule |
| `docs/qa/qa_report_chat_policy.md` | report/chat route와 QA policy |
| `docs/reference/source_inventory.md` | Honda Nature/Dryad source tier와 legacy 선별 기준 |
| `docs/reference/domain_concepts.md` | EMS/CMS 전기 measurement와 전력 개념 참조 |
| `reports/mermaid_20260601/` | retained historical Mermaid report package; current pipeline diagram canon is `docs/specs/diagrams/` |

---

## 9. 보안 및 운영 경계

1. `.env`, DB password, SSH key, token은 Git에 커밋하지 않습니다.
2. Production/canonical write, destructive SQL, 권한 변경은 사전 확인 후 실행합니다.
3. Scratch DB write는 default-deny guard와 isolated target naming을 통과해야 합니다.
4. Local dry-run이나 Docker scratch evidence를 AWS/live/production evidence로 승격하지 않습니다.
5. Candidate output은 canonical이 아니며, approval과 controlled promotion 전에는 preview로만 다룹니다.
6. 작업 산출물, 노트북, 발표·중간 산출물, 대용량 outputs는 archive 또는 ignored output 경로에 보존합니다.

---

## 10. 현재 산출 방향

- Current specs를 기준으로 CMS source, candidate, QA evidence, canonical boundary를 고정합니다.
- Markdown-first report package와 rendered diagram을 팀 공유 산출물로 유지합니다.
- FastAPI quick service path와 Airflow/scheduler workflow path를 분리합니다.
- LangGraph는 optional async review layer로만 배치합니다.
- Future production/canonical work는 DB evidence, test gate, approval workflow를 먼저 정의한 뒤 진행합니다.
