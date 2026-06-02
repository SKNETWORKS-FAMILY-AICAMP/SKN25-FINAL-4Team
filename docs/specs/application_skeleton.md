# 애플리케이션 스켈레톤

이 문서는 `src/cms`에 추가한 최소 애플리케이션 골격의 경계를 정리한다. Application skeleton 파일(`contracts/core.py`, `contracts/agent.py`, `data/live_replay.py`, `service/api.py`, `workflow/airflow_skeleton.py`, `workflow/langgraph_skeleton.py`, `contracts/measurement.py`, `contracts/qa.py`, `contracts/job.py`, `data/db_scratch_guard.py`)은 선택 의존성(FastAPI, Airflow, LangGraph, Mongo/PyMongo)이 설치되어 있지 않아도 import 가능하도록 작성했다. Ontology helper는 `rdflib` 계열 선택 의존성이 필요한 별도 artifact lane이다.

## 원칙

- 정본 데이터 소스: `canonical.measurement_1min`, `canonical.measurement_15min`, `canonical.measurement_1h`
- MongoDB: 최근 live/replay 조회를 빠르게 하기 위한 캐시 역할만 담당
- Airflow: 현재 코드는 비활성화 스켈레톤이며 스케줄 등록 없음. 운영 설계에서는 정기 리포트와 batch/replay job의 소유자
- LangGraph: 일반 사용자 chat path에는 넣지 않고, report / QA review / replay planning / approval workflow에서 선택적으로 사용
- 마트 생성: 이번 단계에서는 보류
- DB write, Mongo write, 네트워크 호출, 원격 명령 실행 없음

## 파일별 역할

- `src/cms/contracts/core.py`
  - stdlib dataclass 기반 공통 계약
  - canonical 테이블 상수, live/replay 요청/결과, agent route 계약 정의
- `src/cms/data/live_replay.py`
  - Mongo read shape를 설명하는 skeleton
  - 실제 Mongo client 생성/접속/쓰기 없음
  - 테스트용 `InMemoryRecentCache`만 제공
- `src/cms/service/api.py`
  - FastAPI 선택 의존성 처리
  - FastAPI가 없으면 `ApiSkeleton` 반환
  - 현재 구현은 `/health`, `/contracts`, `/live-replay/plan`, `/latency/probe`, `/reports/email/dry-run` 최소 계약을 정의
  - `/latency/probe`와 `/reports/email/dry-run`은 API-level latency/report handoff 검증용 dry-run이며 SMTP, DB, network write를 수행하지 않음
  - 목표 shell에서는 상태 조회, artifact 조회, 수동 job 등록을 담당하되 정기 리포트 실행 주체는 아님
- `src/cms/workflow/airflow_skeleton.py`
  - Airflow import 없이 비활성 DAG 계약 제공
  - `make_airflow_dag(enabled=True)`를 명시 호출할 때만 Airflow import 시도
- `src/cms/workflow/langgraph_skeleton.py`
  - LangGraph import 없이 background review workflow 계약 제공
  - 일반 `/chat`의 필수 dependency가 아니며, FastAPI router가 5-route(`quick_answer`, `evidence_answer`, `needs_job`, `approval_required`, `report_shell`)로 1차 라우팅한 뒤 비동기 가지만 담당
  - `classify` → {`approval` | `job` | `report` | `qa_gate` → {`evidence` | `report`}} → `finalize` 토폴로지의 결정론 노드. `run_review()`가 테스트 경로, `make_langgraph(enabled=True)`만 LangGraph import
  - 라우트 분류기 `classify_route()`와 evidence/응답 계약은 plane-neutral `src/cms/contracts/agent.py`에 위치
  - side effect가 필요한 요청은 `approval` 노드에서 `needs_human`으로 정지(승인 전 미실행). 상세 설계는 `langgraph_review_workflow.md`

## 금지된 작업

이 스켈레톤 단계에서는 다음을 수행하지 않는다.

- `.env` 또는 secret 파일 수정/조회
- 운영 DB/MongoDB 접속 또는 write
- Airflow scheduler 등록/실행
- LangGraph에서 LLM/API 네트워크 호출
- canonical mart 생성
