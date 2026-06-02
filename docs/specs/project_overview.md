# CMS 프로젝트 개요

**갱신일:** 2026-06-01
**상태:** 현재 기준 개요

## 1. 범위

SKN25-FINAL-4Team의 CMS 프로젝트는 건물·설비 계량 시계열을 수집하고, 품질 검증과 보정 경계를 통과한 데이터만 분석·서빙·보고에 사용하는 시스템이다. 현재 active architecture는 legacy 분석 산출물 중심 구조가 아니라, 데이터 계층과 서비스 계층, workflow 계층을 분리한 contract-first 구조를 기준으로 한다.

본 문서는 저장소의 현재 기준을 설명한다. 과거 PostgreSQL `ems` schema와 `cr_measurement_15min/1h` mart는 legacy 분석·원천 맥락으로 남을 수 있으나, 신규 live/replay와 serving contract의 기준은 CMS runtime contract이다.

## 2. 현재 active architecture

데이터 계층은 source archive와 live/replay input을 수집한 뒤 staging 또는 raw buffer에 보존하고, processor와 QA gate를 통해 candidate output과 QA evidence를 만든다. Candidate는 canonical이 아니며, dashboard나 model dry-run에서 serving preview로만 사용할 수 있다. Canonical layer로의 승격은 `ops.promotion_request`, QA evidence, approval, controlled promotion role을 통과한 뒤에만 허용한다.

서비스 계층은 FastAPI를 중심으로 빠른 상태 조회, read-only query, manual job registration, report artifact download, lightweight chat interface를 제공한다. 일반 사용자 chat path에는 LangGraph를 기본 삽입하지 않는다.

Workflow 계층은 Airflow, scheduler, background worker가 소유한다. 정기 report, replay/backfill planning, QA evidence packet, approval review, incident review, model inference dry-run 검증은 workflow plane에서 실행하며, LangGraph는 이 plane의 optional async review layer로만 사용한다.

## 3. 현재 repository map

```text
SKN25-FINAL-4Team/
├── docker/                         # local PostgreSQL/TimescaleDB development stack
├── docs/
│   ├── ontology/                   # RDF/OWL/SHACL ontology artifacts and competency questions
│   ├── reference/                  # domain and policy references
│   └── specs/                      # current architecture, DB, data, feature, pipeline specs
├── reports/
│   └── cms_md_reports_20260601/   # Markdown-first live streaming and pipeline report package
├── scripts/                        # dry-run, smoke, scratch guard, ontology, contract verification scripts
├── src/cms/                       # active CMS Python package
└── tests/                          # unit/integration tests
```

Folder-local `.hermes.md` navigation map은 local-only이며 active shared project file이 아니다. `HERMES.md`는 legacy/ignored fallback으로만 취급한다.

## 4. Naming 및 용어

Active Python package는 `src/cms`이다. Project-facing prose에서는 `CMS`를 사용한다. `EMS`라는 용어는 original dataset, legacy database schema, ontology namespace, historical artifact를 가리킬 때만 유지할 수 있다. Active `src/ems` package를 다시 도입하지 않는다.

PostgreSQL runtime convention은 `cms` database와 `archive`, `staging`, `reference`, `canonical`, `qa`, `ops`, `mart` 같은 schema다. 현재 canonical observed fact는 `canonical.measurement_1min`, `canonical.measurement_15min`, `canonical.measurement_1h`이다. Corrected/resampled product는 `reference.corrected_resampled_*`에 속한다. 문서가 `ems.cr_measurement_*`를 언급한다면 반드시 legacy analysis context라고 표시해야 한다.

## 5. 검증 baseline

현재 최소 검증 기준은 다음과 같다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
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

`pytest`는 test dependency다. 설치되어 있지 않으면 pytest 기반 integration test를 실행할 수 없으며, 그 결과는 passed가 아니라 environment-limited로 보고해야 한다.
