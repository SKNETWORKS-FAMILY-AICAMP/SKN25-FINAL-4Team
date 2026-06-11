# SKN25 FINAL 4Team — TTF-FMS

## Smart Factory Energy Insight & Facility Operations AI Copilot

TTF-FMS는 Honda R&D Europe EMS 데이터를 기반으로 전력 수요 예측, 이상 징후 탐지, 설비 상태 분석, 원인 추정, 작업지시 생성, 보고서 자동화를 수행하는 설비 운영 AI Copilot 프로젝트입니다.

본 프로젝트는 기존 FEMS(Energy Management System)의 에너지 관리 기능과 CMS(Condition Monitoring System)의 설비 상태 감시 개념을 결합하여, 추가 센서 설치 없이 기존 전력 계량기 데이터만으로 설비 이상 징후를 선별하는 CMS Lite 구조를 목표로 합니다.

---

# 프로젝트 목표

- 전력 사용량 모니터링
- 전력 수요 예측
- 이상 징후 탐지
- 설비 상태 분석
- 원인 추정
- 작업지시 생성
- 월간 보고서 자동 생성
- 자연어 기반 AI Copilot 제공

---

# 주요 모델

## 1. v84 Residual Forecast

Honda EMS 데이터를 기반으로 구축한 계량기별 개인화 수요 예측 모델입니다.

### 모델 구성

- LSTM Ensemble
- CatBoost
- LightGBM
- Ridge
- Seasonal Naive

### 특징

- 타겟: `P(t) - P(t-1)` Residual
- 계량기별 개인화 모델
- Persistence 대비 성능 평가
- 1시간 / 3시간 예측 지원

---

## 2. Import P-Max Forecast

향후 최대 수요전력을 예측하는 피크 예측 모델입니다.

### 모델 구성

- LightGBM
- XGBoost
- CatBoost

### 특징

- 15분 단위 입력
- 향후 60분 P-Max 예측
- Peak Alarm 지원

---

## 3. 이상탐지

### 주력 구조

```text
v84 Forecast
    ↓
Residual 계산
    ↓
이상 후보 탐지
```

### 보조 구조

- Isolation Forest

### 폴백 구조

- Statistical
- Isolation Forest
- LSTM AutoEncoder

3단계 투표 기반 이상탐지를 지원합니다.

---

# AI Hub 외부 검증

Honda EMS 기반 모델 구조가 국내 제조설비 데이터에서도 유효하게 동작하는지 확인하기 위해 AI Hub 전력 설비 에너지 품질 데이터셋을 활용한 외부 검증을 수행했습니다.

## 데이터셋

- AI Hub 데이터셋 #149
- 국내 63개 업체
- 461개 설비
- Mobile Energy Meter 기반 실측 데이터
- 에너지 효율 분석
- 설비 이상 감지
- 전력 피크 관리

## 검증 목적

본 검증은 국내 제조업 전체 적용을 입증하기 위한 것이 아니라,

Honda 데이터 기반 모델 구조가 국내 제조설비 데이터에서도 유효하게 동작하는지 확인하기 위한 전이 검증(Transfer Validation)입니다.

---

# 주요 검증 결과

## Import P-Max

- AI Hub 재학습 수행
- Persistence 대비 성능 개선 확인
- 중앙값 RMSE 개선율 약 17.5%

## v84 Forecast

- Honda 기반 구조 검증
- AI Hub 재학습 수행
- 다수 설비에서 Persistence 대비 개선 확인

## 이상탐지

- Honda 가중치 직접 적용 실험
- AI Hub 재학습 실험
- 오탐율 감소 확인
- 운영 가능한 수준의 경보율 확인

---

# 저장소 구조

```text
SKN25-FINAL-4Team/
├── docker/                # Docker 환경 설정
├── docs/                  # 분석 문서, 논문, 명세 자료
│   ├── analysis/
│   ├── ontology/
│   ├── papers/
│   ├── reference/
│   └── specs/
├── mlruns/                # MLflow 실험 로그
├── notebooks/             # EDA 및 실험 노트북
├── outputs/               # 검증 결과 및 산출물
├── reports/               # 발표 및 검증 보고서
├── scripts/               # 분석·검증 스크립트
├── src/                   # EMS 분석 공통 모듈
├── README.md
├── requirements.txt
├── pyproject.toml
└── docker-compose.yml
```

---

# 주요 보고서

| 구분 | 경로 | 설명 |
|------|------|------|
| BM 스토리 | `reports/BM_스토리_뼈대_v46_수정.md` | 최종 발표용 BM 문서 |
| AI Hub 검증 | `reports/AIHub_검증_내부보고서_v4.md` | 외부 검증 결과 |
| 예상 질문 | `reports/최종발표_예상질문_답변리스트_v3_주의사항추가.md` | 발표 Q&A |

---

# AI Hub 검증 스크립트

## Import P-Max

```text
scripts/
├── aihub_pmax_validation.py
├── aihub_pmax_validation_training.py
├── aihub_pmax_honda_inference_training.py
├── aihub_pmax_inference_v2.py
├── Aihub_pmax_honda_inference.py
├── aihub_pmax_honda_inference_Minmax.py
└── aihub_pmax_honda_inference_Standscaler.py
```

## v84 Forecast

```text
scripts/
├── aihub_v84_validation.py
├── aihub_v84_trainval_split.py
├── aihub_v84_retrain.py
├── aihub_v84_fullretrain.py
├── aihub_v84_fullretrain_v2.py
└── aihub_v84_inference_v2.py
```

## 이상탐지

```text
scripts/
├── aihub_v84_anomaly_validation.py
├── aihub_v84_anomaly_honda_inference.py
├── aihub_v84_anomaly_retrain_no_lstm.py
└── aihub_v84_anomaly_retrain_with_lstm.py
```

---

# 주요 결과 파일

## Import P-Max

```text
outputs/
├── aihub_pmax_per_device.csv
├── aihub_pmax_training_per_device.csv
├── aihub_pmax_inference_per_device.csv
├── aihub_pmax_honda_inference_per_device.csv
├── aihub_pmax_honda_inference_Minmax_per_device.csv
└── aihub_pmax_honda_inference_Standscaler_per_device.csv
```

## v84 Forecast

```text
outputs/
├── aihub_v84_validation_per_device.csv
├── aihub_v84_retrain_per_device.csv
├── aihub_v84_trainval_split_per_device.csv
├── aihub_v84_inference_per_device.csv
├── aihub_v84_fullretrain_per_device.csv
└── aihub_v84_fullretrain_v2_per_device.csv
```

## 이상탐지

```text
outputs/
├── aihub_v84_anomaly_per_device.csv
├── aihub_v84_anomaly_honda_per_device.csv
├── aihub_v84_anomaly_retrain_no_lstm.csv
└── aihub_v84_anomaly_retrain_with_lstm.csv
```

---

# 실행 환경

```bash
python -m venv .venv
source .venv/bin/activate

python -m pip install -U pip
python -m pip install -r requirements.txt
```

---

# 주의사항

- Honda EMS 원천 데이터는 저장소에 포함하지 않습니다.
- 대용량 데이터는 별도 관리합니다.
- API Key, DB Password, SSH Key 등 Credential은 저장소에 커밋하지 않습니다.
- AI Hub 검증 결과는 모델 구조의 전이 가능성 확인을 위한 실험 결과이며, 국내 제조업 전체 적용을 입증하는 결과는 아닙니다.

---

# 한 줄 요약

> Honda EMS 기반 예측·이상탐지 모델을 AI Hub 국내 제조설비 데이터로 외부 검증하고, 설비 운영 AI Copilot 구축을 목표로 수행한 SKN25 Final Project입니다.
