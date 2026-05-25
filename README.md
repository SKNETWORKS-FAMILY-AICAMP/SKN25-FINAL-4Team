# 스마트 건물 에너지 관리 시스템 (EMS)

> Honda R&D Europe 시설의 6년간 에너지 데이터를 분석하여, AI 기반 에너지 관리 플랫폼을 구축하는 프로젝트

[![Dataset](https://img.shields.io/badge/Dataset-Scientific%20Data%202025-blue)](https://doi.org/10.1038/s41597-024-04263-x)
[![DB](https://img.shields.io/badge/DB-TimescaleDB-orange)](https://www.timescale.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-green)](https://www.python.org/)

---

## 프로젝트 개요

독일 Offenbach에 위치한 Honda R&D Europe 시설의 **81개 계량기 × 6년(2018~2023)** 에너지 데이터를 기반으로:

1. **데이터 프로파일링** — 전수 품질 진단 및 결측/이상 패턴 파악
2. **에너지 흐름 시각화** — 전기/난방/냉방/기상 데이터의 거시적 패턴 검증
3. **머신러닝 모델링** — Grid 전력 예측 (VMD-LSTM), 이상 탐지 (IF + LSTM-AE)
4. **AI 에이전트 플랫폼** — Text-to-SQL 대화형 분석, 자동 리포팅 (예정)

### 기준 논문

> Gruner et al., *"Six years of multi-modal energy monitoring data from a commercial building in Germany"*  
> Scientific Data, 2025 — [DOI](https://doi.org/10.1038/s41597-024-04263-x)

---

## 핵심 발견 (Key Findings)

| 항목 | 내용 |
|------|------|
| **데이터 완결성** | 에너지 미터 결측률 **0.03%** — 보정 파이프라인이 Gap을 과거 데이터 복사로 채움 |
| **기상 데이터 결측** | 기상 관측소만 **2.35% 결측** (2018년 7.11%로 집중) |
| **설비 Regime 변화** | 6년간 **6회 주요 변경** (PV 설치/증설, CHP 로직, COVID, 계량기 교체, 난방 현대화) |
| **냉각-기온 상관** | 외기온 10°C 이상부터 냉각 전력 비선형 증가 — 예측 모델 핵심 피처 |
| **자체 발전 증가** | PV Phase2(2020-06) 이후 Grid 의존도 감소 추세 |

---

## 에너지 흐름 시각화

6년간의 건물 에너지 흐름을 6개 차트로 시각화했습니다.

<table>
<tr>
<td><b>전기 소비/생산 6년 추이</b><br><img src="outputs/figures/energy_flow/01_electricity_overview.png" width="400"></td>
<td><b>난방/냉방 계절 패턴</b><br><img src="outputs/figures/energy_flow/02_heating_cooling.png" width="400"></td>
</tr>
<tr>
<td><b>기상 데이터 (기온+일사량)</b><br><img src="outputs/figures/energy_flow/03_weather.png" width="400"></td>
<td><b>월별 전기 에너지 수지</b><br><img src="outputs/figures/energy_flow/04_monthly_energy_balance.png" width="400"></td>
</tr>
<tr>
<td><b>대표 주간 상세 (2021-03)</b><br><img src="outputs/figures/energy_flow/05_representative_week.png" width="400"></td>
<td><b>냉각 전력 vs 외기온 상관</b><br><img src="outputs/figures/energy_flow/06_cooling_vs_temperature.png" width="400"></td>
</tr>
</table>

> 상세 해석: [`docs/분석_기획/05_에너지_흐름_시각화.md`](docs/분석_기획/05_에너지_흐름_시각화.md)

---

## 머신러닝 모델

### 팀 공통 기준

| 항목 | 값 |
|------|-----|
| **입력 피처** | grid_P, pv_P, chp_P, Ta, Igm + 시간 sin/cos (hour/dow/month) = 11개 |
| **타겟** | grid_P (Grid 전력 소비량, W) |
| **데이터 분할** | Train: 2018~2021 / Val: 2022 / Test: 2023 |
| **정규화** | MinMaxScaler |
| **MLflow** | 예측: `SSA-IPSO-LSTM` / 이상탐지: `LSTM-AE-Anomaly` |

### 예측 모델 — VMD-LSTM (논문 3번)

- **아이디어**: VMD(Variational Mode Decomposition)로 Grid_P를 K=4 IMF 성분으로 분해 후 LSTM 예측
- **구조**: 15개 입력 피처(11 base + 4 IMF lag1h) → LSTM(hidden=128, 2층) → 1h 앞 예측
- **슬라이딩 윈도우**: 24h 입력 → 1h 예측
- **학습**: epochs=50, early stopping(patience=10), HuberLoss, ReduceLROnPlateau
- **저장**: `outputs/models/vmd_lstm_grid_electricity.pt`, `vmd_lstm_scaler.pkl`

### 이상 탐지 모델 — IF + LSTM-AE (논문 7번)

- **아이디어**: Isolation Forest(통계적) + LSTM AutoEncoder(시계열 패턴) 앙상블
- **구조**: LSTM Encoder → latent(16) → LSTM Decoder, MSE 재구성 오차 기반 탐지
- **임계값**: `train_MSE.mean() + 3 × train_MSE.std()` (MSD 방식)
- **앙상블**: ae_flag + if_flag → 2=HIGH / 1=LOW / 0=NORMAL
- **검증**: Gateway 장애 구간(2022-05-06~07-14) 의사 레이블로 Val 성능 평가
- **저장**: `outputs/models/anomaly_lstmae.pt`, `anomaly_iforest.pkl`, `anomaly_scaler.pkl`

### RunPod 학습 환경

ML 모델 학습은 GPU 환경(RunPod)에서 실행:

```bash
# 패키지 설치
pip install -r requirements_runpod.txt --ignore-installed blinker

# 예측 모델 학습 (~1시간)
python train_vmd_lstm.py

# 이상 탐지 모델 학습
python train_anomaly_ifae.py
```

> scripts/ml/ 하위 파일들(data_loader.py, train_vmd_lstm.py, train_anomaly_ifae.py)은  
> RunPod 서버에서 실행하며 로컬에는 보관하지 않음.

---

## 프로젝트 구조

```
EMS/
├── docs/
│   ├── 06_계량기별_상세_분석.docx
│   ├── 07_81개_계량기_개별_분석.docx
│   ├── 분석_기획/                          # 분석 및 기획 문서
│   │   ├── 00_진행현황.md
│   │   ├── 01_데이터_분석_전략.md
│   │   ├── 02_기획서_갭분석.md
│   │   ├── 03_프로파일링_결과.md
│   │   ├── 04_뷰생성_및_보완_결과.md
│   │   ├── 05_에너지_흐름_시각화.md
│   │   ├── 06_계량기별_상세_분석.md
│   │   └── EMS_데이터_분석_보고서.html
│   └── paper.pdf
│
├── scripts/
│   ├── profiling/
│   │   ├── meter_profiling.py              # 81개 미터 전수 프로파일링
│   │   ├── visualize_energy_flow.py        # 에너지 흐름 시각화 (6개 차트)
│   │   └── generate_report_html.py         # 마크다운 → HTML 변환
│   ├── ingest/
│   │   ├── sql/reduced_view.sql            # Reduced 합산 뷰 DDL
│   │   └── dwd_weather_ingest.py           # DWD 기상 데이터 보완
│   └── ml/
│       ├── train_lgbm.py                   # LightGBM 예측 모델 (로컬)
│       ├── train_lstm.py                   # Vanilla LSTM 예측 모델 (로컬)
│       └── export_data.py                  # DB → CSV 내보내기
│
├── outputs/
│   ├── figures/energy_flow/                # 시각화 차트 PNG (6개)
│   ├── profiling/                          # 프로파일링 CSV
│   └── models/                            # 학습된 모델 파일
│
└── README.md
```

---

## 데이터 아키텍처

```
┌─────────────────────────────────────────────────┐
│              TimescaleDB (PostgreSQL)            │
├─────────────────────────────────────────────────┤
│  Registry Layer                                  │
│    full_meter (81개 미터 메타데이터)              │
│                                                  │
│  CR Mart Layer                                   │
│    cr_measurement_15min (15분 해상도)             │
│    cr_measurement_1h    (1시간 해상도)            │
│                                                  │
│  Reduced Layer                                   │
│    reduced_measurement_15min  ← 범주별 합산 뷰   │
│    reduced_measurement_1h     ← 범주별 합산 뷰   │
│      ├── electricity (total/pv/chp)              │
│      ├── heating (total/chp_heat/chp_elec)       │
│      ├── cooling (total/cool_elec)               │
│      └── weather (Ta/Igm)                        │
└─────────────────────────────────────────────────┘
```

---

## 실행 방법

### 사전 요구사항

- Python 3.11+
- Docker (TimescaleDB 컨테이너)
- [uv](https://github.com/astral-sh/uv) 패키지 매니저

### 프로파일링 / 시각화

```bash
# 81개 미터 전수 프로파일링
uv run --with pandas --with "psycopg[binary]" --with python-dotenv \
  python scripts/profiling/meter_profiling.py

# 에너지 흐름 시각화 (6개 차트 생성)
uv run --with pandas --with "psycopg[binary]" --with python-dotenv --with matplotlib \
  python scripts/profiling/visualize_energy_flow.py
```

### ML 모델 (로컬)

```bash
# LightGBM 예측 모델
uv run --with lightgbm --with pandas --with "psycopg[binary]" \
       --with python-dotenv --with scikit-learn --with mlflow \
       python scripts/ml/train_lgbm.py

# LSTM 예측 모델
uv run --with torch --with pandas --with "psycopg[binary]" \
       --with python-dotenv --with scikit-learn --with mlflow \
       python scripts/ml/train_lstm.py
```

### 환경 변수 (.env)

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=ems
DB_USER=ems
DB_PASSWORD=<your_password>
MLFLOW_TRACKING_URI=http://...:5000
```

---

## 진행 현황

| Phase | 작업 | 상태 |
|-------|------|------|
| **Phase 0** | 데이터 프로파일링 (81개 미터 전수) | ✅ 완료 |
| **Phase 1** | Reduced 합산 뷰 생성 | ✅ 완료 |
| **Phase 1** | 에너지 흐름 시각화 검증 (6개 차트) | ✅ 완료 |
| **Phase 1** | DWD 기상 데이터 보완 스크립트 | ✅ 완료 |
| **Phase 2** | Grid 전력 예측 — VMD-LSTM (RunPod) | 🔄 학습 중 |
| **Phase 2** | Grid 전력 예측 — LightGBM / LSTM (로컬) | ✅ 완료 |
| **Phase 4** | 이상 탐지 — IF + LSTM-AE (RunPod) | 🔄 학습 중 |
| **Phase 3** | Text-to-SQL 에이전트 개발 | 🔜 예정 |
| **Phase 5** | 역률/비용 최적화 | 🔜 예정 |
| **Phase 6** | 자동 리포팅 | 🔜 예정 |

> 상세: [`docs/분석_기획/00_진행현황.md`](docs/분석_기획/00_진행현황.md)

---

## Team

**SKN25-FINAL-4Team**

---

## 참고 자료

- [Honda R&D Energy Dataset (Scientific Data, 2025)](https://doi.org/10.1038/s41597-024-04263-x)
- [TimescaleDB Documentation](https://docs.timescale.com/)
- [DWD Climate Data Center](https://opendata.dwd.de/climate_environment/CDC/)
