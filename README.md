# SKN25-FINAL-4Team — 중소 제조공장 에너지 분석 AI 챗봇 플랫폼

> SK 네트웍스 Family AI Camp 25기 파이널 프로젝트 — 4팀

---

## 목차

1. [프로젝트 개요](#프로젝트-개요)
2. [문제 정의](#문제-정의)
3. [핵심 기능](#핵심-기능)
4. [시스템 아키텍처](#시스템-아키텍처)
5. [멀티 에이전트 구성](#멀티-에이전트-구성)
6. [데이터셋](#데이터셋)
7. [ML / DL 모델](#ml--dl-모델)
8. [기술 스택](#기술-스택)
9. [비기능 요구사항](#비기능-요구사항)
10. [팀 구성](#팀-구성)
11. [산출물 일정](#산출물-일정)

---

## 프로젝트 개요

중소 제조공장을 대상으로 **자연어 기반 에너지 분석 및 공정 관리 AI 챗봇 플랫폼**을 개발한다.

공장 운영자가 별도의 분석 도구나 전문 인력 없이도 자연어 질문만으로 에너지 데이터를 분석하고 표준화된 리포트를 즉시 산출할 수 있는 **멀티 에이전트 기반 플랫폼**이다.

| 구분 | 내용 |
|---|---|
| 타겟 | 중소 제조공장 운영자 |
| 비즈니스 모델 | B2B SaaS 구독 (베이직 / 스탠다드 / 프리미엄 3-Tier) |
| 정책 연계 | 중소벤처기업부 스마트 제조혁신 지원사업 |

---

## 문제 정의

- 한국 정부의 스마트공장 보급사업으로 **중소기업 3만 개** 이상이 센서와 시스템을 도입했으나, **75.7%가 여전히 기초 단계**에 머무르며 수집된 데이터를 의사결정에 활용하지 못하고 있다.
- 에너지 비용은 제조원가의 **10~30%**를 차지하지만, 체계적인 분석 인프라는 부재하다.
- 대기업용 FEMS 솔루션은 초기 도입 비용이 수억 원 이상으로 중소공장에는 진입 장벽이 높다.

### 경쟁사 비교

| 구분 | 포스프레임 | Nexplant | 네이버클라우드 | 기존 FEMS | **본 플랫폼** |
|---|---|---|---|---|---|
| 타겟 | 중대형 | 대기업 | 대기업 파트너 | 중대형 | **중소공장** |
| 자연어 질의 | 없음 | 없음 | 있음(미완) | 없음 | **있음** |
| 분석 오류 감지 | 없음 | 일부 | 없음 | 없음 | **있음 (Critic)** |
| 중소기업 접근성 | 낮음 | 매우 낮음 | 불가 | 중간 | **높음** |

---

## 핵심 기능

### 플랫폼 인프라

| 기능 ID | 기능명 | 설명 |
|---|---|---|
| INF-001 | 메인 대시보드 | React SPA. 에너지 현황 요약 위젯, 이상탐지 알림 배너, 최근 리포트 바로가기 |
| INF-002/003 | 회원가입 및 로그인 | JWT 토큰 인증. FastAPI `/auth/login` 엔드포인트. Django 회원 관리 |
| INF-004/005 | CSV 업로드 | 표준 CSV 템플릿 검증. 결측치 20% 초과 경고, 음수값 자동 감지 |

### 이상탐지 (3단계 앙상블)

```
1단계 — 통계 기반  : Z-score / IQR, STL 계절 분해, 24시간 이동평균
2단계 — 머신러닝   : Isolation Forest (다변량: 전력 + 역률 + 공급온도)
3단계 — 딥러닝     : LSTM Autoencoder (복원 오차 임계값 기반)

앙상블 판정
  - 2개 이상 일치 → 주의 (노란불)
  - 3개 모두 일치 → 위험 (빨간불)
```

- 보일러 효율 기준값 자동 적용 (노통 80% / 관류 93% / 가스 87%)

### 에너지 예측 모델

| 모델 | 입력 | 예측 범위 |
|---|---|---|
| Prophet | 계절성 + 트렌드 + 공휴일 | 단기 (1일/1주) |
| LSTM | lag + 외기온도 + 일사량 + 시간/요일/월 | 중장기 (1개월) |
| XGBoost | lag 피처 + 날씨 + 시간 피처 | 단기/중기 |

- MLflow로 실험 추적. 3개 모델 MAE / RMSE / MAPE 비교 후 최적 모델 자동 선정

### 자동 리포팅

- **월간 에너지 리포트** — 전력/냉난방 총량, 전년 동월 대비 증감, 이상 발생 내역, Plotly 차트 포함 PDF 자동 생성
- **KPI 보고서** — 에너지 원단위, 설비 가동률, 이상탐지 건수, 예측 정확도(MAPE), 피크 부하 절감률. 일간/주간/월간 자동 실행

### 대화형 챗봇

- 자연어 질의 → Orchestrator 라우팅 → 에이전트 분석 → 자연어 응답
- 채팅 응답 내 Plotly 차트 인라인 렌더링
- `anomaly_results` / `forecast_results` 테이블 실시간 조회

---

## 시스템 아키텍처

```
사용자 (웹 브라우저)
     │
     ▼
React SPA  ──────────────────────────────────────────────────┐
     │                                                         │
     ▼                                                         ▼
FastAPI (API Gateway)                                   Django (Auth/Admin)
     │         │
     │         ├── Redis (캐시, TTL 1h)
     │         │
     ▼         ▼
LangGraph 멀티 에이전트 시스템
     │
     ├── Orchestrator Agent
     ├── Anomaly Detection Agent
     ├── RAG Agent
     ├── Reporting Agent
     └── Critic Agent
           │
           ▼
PostgreSQL + TimescaleDB (ems schema)
     │
     └── pgvector (RAG 벡터 저장)
```

---

## 멀티 에이전트 구성

| 에이전트 | 역할 |
|---|---|
| **Orchestrator** | 사용자 질의 의도 파악 및 하위 에이전트 라우팅 (LangGraph 기반) |
| **Anomaly Agent** | 센서 이상탐지 + 설비 효율 판단. 3단계 모델 호출 후 결과 DB 저장 |
| **RAG Agent** | pgvector 검색. 에너지진단 가이드북 / 보일러 운영 매뉴얼 / 설비 효율 기준 문서 기반 개선 방안 제안 |
| **Reporting Agent** | KPI 집계 및 PDF 보고서 생성 후 다운로드 링크 반환 |
| **Critic Agent** | 분석 결과 재검토. 외기온도 미보정 / 설비 스케줄 변화 미반영 등 에너지 분석 오류 자동 탐지 및 보완 |

---

## 데이터셋

### 1. Honda R&D Europe 스마트 빌딩 에너지 데이터

- **출처**: Nature Scientific Data 2025, Dryad 공개 데이터셋 (Public Domain)
- **대상**: 독일 Offenbach 소재 Honda R&D Europe 시설의 전기, 열, 냉방, 기상 계측 데이터
- **규모**:

| 해상도 | 레코드 수 |
|---|---|
| 1시간 (cr_measurement_1h) | 68,169,983 건 |
| 15분 (cr_measurement_15min) | 272,679,348 건 |
| 1분 (cr_measurement_1min, 일부) | 862,382,708 건 |

### 2. 한국지역난방공사 열사용량 데이터

- **수집 방법**: 한국지역난방공사 직접 요청 제공
- **내용**: 고객·건물·설치 단위 월별 익명화 열사용량 (2021~2025, 총 16,868,714 행)
- **분류**: 업무 / 주택 / 공공 / 냉수 4종

### 3. 한국 기상 데이터

- **출처**: 기상청 공공데이터포털 API
- **항목**: 외기온도, 일조시간, 습도 (전국 평균, 날짜 기준 left join)

### 데이터베이스 설계

- **DBMS**: PostgreSQL + TimescaleDB (`ems` schema)
- **핵심 테이블**:

| 테이블 | 역할 |
|---|---|
| `full_meter` | 계량기 식별자 registry |
| `full_measurement_definition` | measurement code / unit / family 딕셔너리 |
| `full_source_file` | 파일 단위 적재 상태 및 품질 counter |
| `full_measurement` | 모든 processing level의 측정값 fact table |
| `cr_measurement_1h` | 1시간 해상도 mart (TimescaleDB hypertable) |
| `cr_measurement_15min` | 15분 해상도 mart (TimescaleDB hypertable) |
| `anomaly_results` | 이상탐지 결과 |
| `forecast_results` | 예측 결과 |

---

## ML / DL 모델

### A-clean 1시간 전력 예측 — Huang 2022 Benchmark

**입력**: 직전 24시간 전력값 (lag) + 시간 주기 (hour_sin, hour_cos)  
**데이터 분할**: 학습 2018–2021 (35,064행) / 검증 2022 (8,760행) / 평가 2023 (8,759행)

#### 실험 결과 (target별 최저 RMSE 모델)

| 설비군 | 최적 모델 | 2022 RMSE | 2023 RMSE |
|---|---|---|---|
| 중앙 냉방 | LSTM (hidden=32) | 6,963.95 | 4,974.17 |
| 국소 냉방 | XGBoost (n=300, depth=5, lr=0.06) | 1,739.83 | 1,922.54 |
| 서버 전원 | SVR linear (C=1, ε=0.1) | 1,444.62 | 1,782.56 |
| 환기 계통 | XGBoost (n=300, depth=5, lr=0.06) | 3,041.69 | 3,957.79 |

#### 모델 저장 경로

```
outputs/modeling/a_clean_huang2022_benchmark_1h/
  └── <target_id>/
        ├── huang2022_best_model.joblib  # SVR / XGBoost
        └── huang2022_best_model.pt      # LSTM (PyTorch checkpoint)
```

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| Frontend | React (SPA), Plotly |
| Backend | FastAPI, Django |
| DB | PostgreSQL + TimescaleDB, Redis, pgvector |
| AI / Agent | LangGraph, RAG (pgvector) |
| ML / DL | scikit-learn, XGBoost, PyTorch, Prophet |
| 데이터 처리 | Python 3.12, pandas, numpy, Dask, joblib |
| 실험 추적 | MLflow |
| 인프라 | Docker, config.yaml 기반 도메인 어댑터 |

---

## 비기능 요구사항

| 항목 | 기준 |
|---|---|
| API 응답 시간 | ≤ 500ms (95th percentile) |
| LSTM 예측 응답 | ≤ 5초 (단일 계량기, 24시간 예측) |
| CSV 업로드 처리 | 100MB 파일 ≤ 10초 |
| 동시 요청 처리 | 10건 이상, 단일 요청 응답의 2배 이내 |
| 서비스 업타임 | 월간 99% 이상 (다운타임 월 7.2시간 이내) |
| 계량기 확장성 | 현재 81개 → 최대 500개 (config.yaml 교체만으로 전환) |

---

## 팀 구성

| 이름 | 역할 |
|---|---|
| 여해준 | 프로젝트 기획, DB 설계, 데이터 수집·적재 |
| 최원준 | 데이터 전처리, ML/DL 모델 학습 및 실험 |

---

## 산출물 일정

| 주차 | 단계 | 산출물 |
|---|---|---|
| 1주차 | 기획 | WBS, 요구사항 정의서, 프로젝트 기획서 |
| 2주차 | 데이터 수집 및 저장 | 데이터베이스/저장소 설계 문서, 수집 데이터 보고서 |
| 3주차 | 데이터 전처리 | 전처리 결과서, ML/DL 학습 결과서, 학습된 모델 산출물 |

---

> GitHub: https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN25-FINAL-4Team
