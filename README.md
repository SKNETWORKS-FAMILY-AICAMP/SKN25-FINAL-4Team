# SKN25 FINAL 4Team EMS 프로젝트

EMS는 Honda R&D Europe의 다년 에너지 계량 데이터를 기반으로 건물 에너지 사용, 설비 운전, 이상 패턴, 예측 가능성을 분석하는 프로젝트입니다. 저장소는 PostgreSQL/TimescaleDB `ems` schema를 기준 원천으로 사용하는 DB 중심 구조를 따릅니다.

## 1. 프로젝트 범위

- 81개 계량기와 기상 데이터를 기준으로 전기, 열, 냉방, 기상 측정값을 분석합니다.
- 원천 데이터와 대용량 로컬 데이터는 저장소에 포함하지 않습니다.
- 분석 입력, 계량기 metadata, feature 기준, ontology schema는 `docs/specs/`의 active 문서를 기준으로 합니다.
- 실험 노트북, 분석 스크립트, 재생성 가능한 산출물은 목적별 폴더에 분리합니다.

## 2. 저장소 구조

```text
SKN25-FINAL-4Team/
├── docs/
│   ├── specs/                 # 프로젝트 핵심 명세
│   ├── ontology/              # ontology artifact와 역량질문
│   ├── reference/             # 도메인 학습·참조 문서
│   ├── analysis/              # 분석 기획·발표·해석 문서
│   └── papers/                # 기준 논문과 추출 문서
├── notebooks/
│   ├── overview/              # 전체 품질·coverage·집계 탐색
│   ├── H1.Z16/                # H1.Z16 계량기 EDA/이상탐지
│   └── stl_eda/               # STL·상관·이상치 탐색 노트북
├── scripts/
│   ├── ontology/              # ontology 생성·검증·조회
│   ├── profiling/             # 미터 프로파일링과 에너지 흐름 시각화
│   ├── features/              # baseline feature 생성
│   ├── anomaly/               # 이상 탐지 실행 스크립트
│   ├── forecast/              # 예측 baseline 실행 스크립트
│   └── ingest/                # 보조 적재·뷰 SQL. DB write는 승인 후 실행
├── src/ems/                   # 재사용 가능한 EMS Python 모듈
├── outputs/
│   ├── figures/               # 공유 가능한 그림 산출물
│   └── tables/                # 공유 가능한 표 산출물
├── docker/                    # 로컬 컨테이너 이미지 정의
├── docker-compose.yml         # 로컬 EMS 컨테이너 compose
├── pyproject.toml
└── requirements.txt
```

## 3. 주요 문서

| 구분 | 경로 | 내용 |
|---|---|---|
| 프로젝트 개요 | `docs/specs/프로젝트_개요.md` | 목적, 기준 데이터, 분석 범위 |
| 데이터 계약 | `docs/specs/데이터_계약.md` | 분석 입력 grain, timestamp, 품질 규칙 |
| DB 구조 | `docs/specs/데이터베이스_구조.md` | `ems` schema relation, column, index 기준 |
| 계량기 metadata | `docs/specs/계량기_메타데이터.md` | 계량기 분류, 설비 그룹, redundancy, 부호 규약 |
| Feature 기준 | `docs/specs/피처_명세.md` | feature 입력, naming, 누수 방지 기준 |
| Ontology 기준 | `docs/specs/온톨로지_스키마.md` | ontology class/property/artifact coverage |
| 도메인 개념 | `docs/reference/도메인_개념.md` | 전기·열 계량기 기본 개념 |
| 분석 기획 | `docs/analysis/분석_기획/` | 팀 분석 진행 문서와 발표자료 |
| 기준 논문 | `docs/papers/` | 논문 원문, 번역, 전문, 요약 |

## 4. 주요 산출물

| 구분 | 경로 |
|---|---|
| 에너지 흐름 그림 | `outputs/figures/energy_flow/` |
| 프로파일링 테이블 | `outputs/tables/profiling/` |
| H1.Z16 노트북 | `notebooks/H1.Z16/` |
| 전체 탐색 노트북 | `notebooks/overview/` |
| STL EDA 노트북 | `notebooks/stl_eda/` |

## 5. 실행 환경

- Python 기준 버전은 3.12입니다.
- 공통 의존성은 루트 `requirements.txt`를 기준으로 설치합니다.
- RunPod CUDA image의 `torch==2.8.*` 계열은 이미지에서 관리합니다. `requirements.txt`에는 `torch`, `torchvision`, `torchaudio`를 넣지 않습니다.
- `.env`, DB password, token, SSH key 등 credential은 저장소에 커밋하지 않습니다.

예시:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

## 6. 대표 실행 명령

프로파일링:

```bash
python scripts/profiling/meter_profiling.py
```

에너지 흐름 시각화:

```bash
python scripts/profiling/visualize_energy_flow.py
```

Ontology artifact 생성과 검증:

```bash
python scripts/ontology/generate_ontology.py
python scripts/ontology/validate_ontology.py
python scripts/ontology/query_ontology.py
```

Baseline feature 생성:

```bash
python scripts/features/build_baseline_features.py
```

이상 탐지 baseline 실행:

```bash
python scripts/anomaly/run_anomaly_detection.py
```

예측 baseline 실행:

```bash
python scripts/forecast/run_forecast_baseline.py
```

## 7. DB write 주의사항

`scripts/ingest/` 아래의 SQL 또는 적재 스크립트는 DB 객체 생성, view 생성, 외부 데이터 적재를 포함할 수 있습니다. 다음 작업은 반드시 사전 승인 후 실행합니다.

- schema, table, view, index 생성 또는 변경
- 대용량 적재 또는 삭제
- 외부 원천 데이터 다운로드와 DB 반영
- 운영 DB credential 사용

## 8. Git 관리 기준

저장소에는 다음 항목을 포함하지 않습니다.

```text
.env
.env.*
.venv/
data/
HERMES.md
.github/
__pycache__/
*.pyc
.pytest_cache/
.obsidian/
_archive/
tests/
.cache/
tmp/
*.log
```

공유 대상 문서와 산출물은 목적별 폴더에 둡니다. 사람이 읽는 Markdown 문서는 한글 파일명을 우선하며, RDF/OWL/TTL 등 도구용 artifact 파일명은 기존 영문명을 유지합니다.
