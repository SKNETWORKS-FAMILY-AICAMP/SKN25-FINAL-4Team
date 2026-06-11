
# SKN25 FINAL 4Team — TTF-FMS

**스마트팩토리 에너지 인사이트 · 설비 운영 AI Copilot**
**FEMS + CMS Lite 하이브리드 플랫폼**

TTF-FMS는 Honda R&D Europe EMS 6년치 에너지 계량 데이터를 기반으로 전력 수요 예측, 이상 징후 탐지, 설비 상태 분석, 원인 추정, 작업지시 생성, 월간 보고서 자동화를 통합한 설비 운영 AI Copilot입니다.

기존 FEMS가 에너지 사용량 모니터링에 머무르고, 기존 CMS가 별도 진동·전류 센서 설치를 요구하는 문제를 보완하기 위해, 본 프로젝트는 **기존 전력 계량기 데이터만으로 에너지 관리와 설비 이상 징후 선별을 동시에 수행하는 CMS Lite 구조**를 목표로 합니다.

---

## 1. 핵심 기능

* 전력 수요 예측

  * v84 앙상블 기반 1시간/3시간 수요 예측
  * Import P-Max 기반 15분 단위 피크 예측

* 이상탐지

  * v84 예측 잔차 기반 이상 후보 탐지
  * IsolationForest 보조 검증
  * 통계 + IF + LSTM-AE 폴백 구조

* AI Copilot

  * LangGraph 기반 멀티 에이전트
  * 설비 상태 질의
  * 이상 원인 추정
  * 작업지시 생성
  * 월간 보고서 자동 생성
  * RAG 기반 도메인 질의응답

* 대시보드

  * 설비 상태 감시
  * 이상탐지 현황
  * 예측 트렌드
  * 정비 작업지시
  * 보고서
  * 챗봇

---

## 2. 모델 및 검증 요약

### v84 수요 예측 모델

* 모델 구성: LSTM × 7 + CatBoost + LightGBM + Ridge + Seasonal Naive
* 타겟: 잔차 `P(t) - P(t-1)`
* 구조: 계량기별 개인화 학습
* 주요 지표:

  * 1h beats_persistence: 27/45
  * 3h beats_persistence: 16/45

### Import P-Max 피크 예측

* 모델 구성: LightGBM × 2 + XGBoost + CatBoost
* 입력: 최근 24시간, 15분 단위 96개 시점
* 출력: 향후 60분 P_max 예측
* 주요 지표:

  * Persistence 대비 RMSE 개선: 11~18%
  * 추론 70,660건 실패 0건

### 이상탐지

* 주력: v84 예측 잔차 기반 이상 후보 탐지 + IsolationForest 보조 검증
* 폴백: 통계 + IsolationForest + LSTM-AE 3단 투표
* 유형 해석:

  * v84는 이상 후보만 탐지
  * PowerSpike, COPDrop, NightConsumption, CHPOutage, PVNightNonZero 등 운영 유형은 anomaly_agent와 LLM이 해석

### AI Hub 외부 검증

Honda 데이터 기반 모델 구조가 국내 제조설비 데이터에도 적용 가능한지 확인하기 위해 AI Hub 전력 설비 에너지 품질 데이터셋으로 외부 검증을 수행했습니다.

* 대상: AI Hub #149, 펌프/일반모터 73개 설비
* 데이터 성격: 국내 63개 업체, 461개 설비에서 Mobile Energy Meter로 직접 계측한 제조 현장 데이터
* Import P-Max:

  * AI Hub 재학습 후 유효 설비 85%에서 Persistence 대비 개선
  * 중앙값 RMSE 개선율 17.5%
* v84 이상탐지:

  * Honda 가중치 그대로 오탐율 31.10%
  * AI Hub 재학습 후 오탐율 11.77%
  * 경보율 20.04%로 Honda 운영 수준에 근접

단, 이는 국내 제조업 전체 적용을 입증한 것이 아니라, **고객사 데이터 재학습을 전제로 모델 구조의 전이 가능성을 확인한 결과**입니다.

---

## 3. 저장소 구조

```text
SKN25-FINAL-4Team/
├── backend/                         # FastAPI, LangGraph, ML 추론 연동
│   ├── src/
│   │   ├── agents/                  # LangGraph 에이전트
│   │   ├── api/                     # 앱 진입점 및 라우터
│   │   ├── data/                    # 데이터 접근 계층
│   │   ├── knowledge/               # RAG 지식 및 임베딩
│   │   └── models/anomaly/          # 이상탐지 모델
│   ├── ml/pipeline/                 # v84 학습·추론 파이프라인
│   ├── docs/kb/                     # RAG 지식베이스
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                        # React/Vite 대시보드
│   └── src/
│       ├── api/
│       ├── components/
│       ├── data/
│       ├── App.jsx
│       └── theme.js
├── reports/                         # 최종 발표 및 검증 보고서
│   ├── BM_스토리_뼈대_v46_수정.md
│   ├── AIHub_검증_내부보고서_v4.md
│   └── 최종발표_예상질문_답변리스트_v3_주의사항추가.md
├── scripts/                         # 실험·검증·전처리 스크립트
├── outputs/                         # 실험 결과 CSV 및 산출물
├── docs/                            # 분석 문서 및 참고 자료
├── notebooks/                       # EDA 및 실험 노트북
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## 4. 주요 문서

| 구분        | 경로                                     | 내용                              |
| --------- | -------------------------------------- | ------------------------------- |
| BM 스토리    | `reports/BM_스토리_뼈대_v46_수정.md`          | 최종 발표용 사업·기술 스토리                |
| AI Hub 검증 | `reports/AIHub_검증_내부보고서_v4.md`         | Import P-Max, v84, 이상탐지 외부 검증   |
| 예상 질문     | `reports/최종발표_예상질문_답변리스트_v3_주의사항추가.md` | 최종 발표 Q&A 방어 논리                 |
| 도메인 지식    | `backend/src/knowledge/`               | FEMS/CMS 도메인 프롬프트 및 RAG 자료      |
| 이상탐지 모델   | `backend/src/models/anomaly/`          | residual, IF, LSTM-AE, ensemble |
| ML 파이프라인  | `backend/ml/pipeline/`                 | v84 학습·추론 코드                    |

---

## 5. 실행 환경

Python 3.12 기준입니다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

백엔드 환경은 `backend/requirements.txt`를 기준으로 별도 설치할 수 있습니다.

```bash
cd backend
python -m pip install -r requirements.txt
```

`.env`, DB 비밀번호, API 키, SSH 키 등 credential은 저장소에 커밋하지 않습니다.

---

## 6. 실행 예시

### 백엔드 실행

```bash
cd backend
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 프론트엔드 실행

```bash
cd frontend
npm install
npm run dev
```

### Docker Compose 실행

```bash
docker compose up --build
```

---

## 7. 주요 ML 실행 예시

### v84 학습

```bash
cd backend
python -m ml.pipeline.train --horizon 1
python -m ml.pipeline.train --horizon 3
```

### 이상탐지 배치 실행

```bash
cd backend
python src/models/anomaly/batch_detect_historical.py
```

### AI Hub 검증 스크립트

AI Hub 전이 검증 관련 스크립트는 `scripts/`와 `outputs/`에 보관합니다.

```bash
ls scripts/
ls outputs/
```

---

## 8. 데이터 및 DB 기준

* Honda EMS 원천 데이터는 저장소에 포함하지 않습니다.
* 대용량 로컬 데이터와 credential은 커밋하지 않습니다.
* 운영 데이터는 PostgreSQL/TimescaleDB 기반 DB를 기준으로 사용합니다.
* 주요 테이블 예시:

  * `reference.corrected_resampled_1h`
  * `anomaly_results`
  * `monthly_report`
  * `work_orders`
  * `cr_measurement_1h`

---

## 9. Git 관리 기준

저장소에는 다음 항목을 커밋하지 않습니다.

```text
.env
.env.*
.venv/
data/
__pycache__/
*.pyc
.pytest_cache/
.cache/
tmp/
*.log
```

대용량 모델 파일, 실험 중간 산출물, 원천 데이터는 별도 관리합니다.
공유가 필요한 최종 문서와 검증 보고서는 `reports/`에 정리합니다.

---

## 10. 한 줄 요약

> TTF-FMS는 기존 전력 계량기 데이터만으로 전력 수요 예측, 이상 징후 탐지, 원인 추정, 작업지시, 보고서 생성을 연결하는 FEMS + CMS Lite 기반 설비 운영 AI Copilot입니다.

