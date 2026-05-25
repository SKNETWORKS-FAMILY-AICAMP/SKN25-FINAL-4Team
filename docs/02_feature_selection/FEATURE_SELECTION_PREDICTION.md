# Feature Selection — 예측 모델 기준 (LSTM / Boosting)

> 이상탐지 기준 feature 선정은 `FEATURE_SELECTION.md` 참고
> 본 문서는 **LSTM, XGBoost/LightGBM 계열** 예측 모델을 가정하여 작성

---

## 이상탐지 vs 예측 — feature 선정 원칙 차이

| 항목 | 이상탐지 (STL 기반) | 예측 (LSTM / Boosting) |
|---|---|---|
| 누적값 (W) | 단조증가 → 제거 필수 | 예측 목적 따라 사용 가능하나 rate(P) 우선 |
| 외부변수 Ta (전기) | Ta↔P=0.19 → 제거 | 계절성 패턴 포착에 유효 → **유지 권장** |
| 고상관 feature 묶기 | 강하게 권장 | Tree 계열은 자체 처리, LSTM도 유연 → 완화 가능 |
| Tdiff vs Tvl/Trl | Tdiff 우선 (직접 이상신호) | 모델이 Tvl-Trl 관계 스스로 학습 가능 → 개별도 가능 |
| 시계열 시간 feature | STL이 seasonality 분리 처리 | **필수** — hour, day_of_week, month 등 |
| Lag feature | 불필요 (STL이 패턴 분리) | **필수** — P(t-1), P(t-24), P(t-168) 등 |

---

## 전기 계량기 (Electric) — 예측 feature

### 모델별 특성

| 모델 | 특성 | 영향 |
|---|---|---|
| LSTM | 시계열 순서 자체를 학습, 내부적으로 lag 처리 | raw sequence 입력 가능, 시간 feature 보조로 유용 |
| XGBoost / LightGBM | 순서 개념 없음, 수동으로 lag/window 생성 필요 | lag feature, rolling mean/std 등 명시적 생성 필수 |

### Consumption (소비) 예측 feature

| # | Feature | 유형 | 이상탐지 대비 | 비고 |
|---|---|---|---|---|
| 1 | **P** | 계량기 (타겟 또는 입력) | 동일 | 예측 타겟이 되는 경우가 많음 |
| 2 | **U1** | 계량기 | 동일 | 전압 변동이 부하 예측에 보조 신호 |
| 3 | **PF** | 계량기 | 동일 | 역률 패턴이 소비 특성 반영 |
| 4 | **f** | 계량기 | 동일 | 계통 상태 |
| 5 | **Ta** | 외부 | **이상탐지에서 제거 → 예측에서 유지** | 계절별 냉난방 수요 패턴 포착 |
| 6 | **hour** | 시간 | 신규 | 시간대별 소비 패턴 (필수) |
| 7 | **day_of_week** | 시간 | 신규 | 주중/주말 패턴 (필수) |
| 8 | **month** | 시간 | 신규 | 계절성 (필수) |
| 9 | **P(t-1)** | lag | 신규 | 직전 시간 소비 (Boosting 필수) |
| 10 | **P(t-24)** | lag | 신규 | 전일 동시간 소비 |
| 11 | **P(t-168)** | lag | 신규 | 전주 동시간 소비 |

> Igm(일사량): 전기 계량기에서 Ta보다 더 약한 신호(Igm↔P=0.211). 태양광 발전 계량기(Production)에서는 유지 검토

### Production (발전) 예측 feature

| # | Feature | 비고 |
|---|---|---|
| 1 | **P** | 발전량 (타겟) |
| 2 | **U1** | 전압 |
| 3 | **PF** | 역률 |
| 4 | **f** | 주파수 |
| 5 | **Ta** | 외기온도 (냉각 효율, 계절성) |
| 6 | **Igm** | 일사량 — **발전에서는 유지** (태양광 발전량 직접 영향) |
| 7 | **hour, day_of_week, month** | 시간 feature |
| 8 | **P(t-1), P(t-24), P(t-168)** | lag feature |

---

## 열계량기 (Thermal) — 예측 feature

### Tdiff vs Tvl/Trl 선택

- 이상탐지: Tdiff 우선 (직접 신호, anomaly_target)
- 예측: 모델이 Tvl - Trl 관계를 학습할 수 있으므로 **Tvl, Trl 개별 입력도 가능**
  - LSTM: Tvl, Trl 개별 입력 → 모델이 내부적으로 관계 학습
  - Boosting: Tdiff를 명시적으로 파생 feature로 추가하는 것이 효율적
  - 예측 타겟이 P인 경우: Tvl, Trl, Tdiff 모두 후보. 실험으로 검증 권장

### Cooling (냉방) 예측 feature

| # | Feature | 유형 | 이상탐지 대비 | 비고 |
|---|---|---|---|---|
| 1 | **P** | 계량기 (타겟) | 동일 | 냉방 열량 |
| 2 | **qv** | 계량기 | 동일 | 유량 |
| 3 | **Tdiff** | 계량기 | 동일 | 온도차 |
| 4 | **Tvl** | 계량기 | **이상탐지에서 제거 → 예측에서 유지 검토** | 공급온도 개별값이 예측에 추가 정보 제공 가능 |
| 5 | **Trl** | 계량기 | **이상탐지에서 제거 → 예측에서 유지 검토** | 환수온도 개별값 |
| 6 | **Ta** | 외부 | 동일 (유지) | 냉방 부하 설명 변수 (Ta↔P=0.453) |
| 7 | **hour, day_of_week, month** | 시간 | 신규 | 운영 패턴 |
| 8 | **P(t-1), P(t-24), P(t-168)** | lag | 신규 | Boosting 필수 |

### Heating (난방) 예측 feature

| # | Feature | 유형 | 이상탐지 대비 | 비고 |
|---|---|---|---|---|
| 1 | **P** | 계량기 (타겟) | 동일 | 난방 열량 |
| 2 | **qv** | 계량기 | 동일 | 유량 |
| 3 | **Tdiff** | 계량기 | 동일 | 온도차 |
| 4 | **Tvl** | 계량기 | **이상탐지에서 제거 → 예측에서 유지 검토** | 공급온도 |
| 5 | **Ta** | 외부 | 동일 (유지) | 핵심 예측 변수 (Ta↔P=0.692) |
| 6 | **hour, day_of_week, month** | 시간 | 신규 | 운영 패턴 |
| 7 | **P(t-1), P(t-24), P(t-168)** | lag | 신규 | Boosting 필수 |

> Trl은 Tvl과 높은 상관(0.774~0.910)이므로 Tvl만 유지하고 Trl 제거해도 무방
> Tdiff는 Boosting에서 명시적 파생 feature로 추가 권장 (Tvl - Trl 직접 계산)

---

## LSTM vs Boosting — 입력 구성 차이 요약

| 항목 | LSTM | XGBoost / LightGBM |
|---|---|---|
| 시계열 순서 | sequence 그대로 입력 | lag feature 수동 생성 필수 |
| 시간 feature | sin/cos 인코딩 권장 (hour → sin(2π·h/24)) | 정수값 그대로 사용 가능 |
| 누적값 | 사용 안 함 | 사용 안 함 |
| 외상관 외부변수 (Ta 전기) | 유지 — 장기 시퀀스에서 계절성 학습 가능 | 유지 — feature importance로 자동 선별 |
| 고상관 feature | 묶어도 되고 개별 넣어도 됨 | 묶어도 되고 개별 넣어도 됨 (tree는 multicollinearity 강건) |
| Tvl/Trl vs Tdiff | 개별 입력 후 모델이 학습 | Tdiff 파생 feature 명시적으로 추가 |

---

## 요약

| 계량기 유형 | 이상탐지 feature 수 | 예측 feature 수 (시간/lag 제외) | 추가된 것 |
|---|---|---|---|
| 전기 Consumption | 4개 (P, U1, PF, f) | **5개** | Ta 추가 |
| 전기 Production | 4개 (P, U1, PF, f) | **6개** | Ta, Igm 추가 |
| 열 Cooling | 4개 (P, qv, Tdiff, Ta) | **6개** | Tvl, Trl 추가 검토 |
| 열 Heating | 4개 (P, qv, Tdiff, Ta) | **5개** | Tvl 추가 검토 |

> 시간 feature (hour, day_of_week, month) 및 lag feature (P(t-1), P(t-24), P(t-168))는 모든 예측 모델에 공통 추가
