# 데이터 분석 및 앱 업그레이드 가이드

> 출처: `dev/data/energy_agent_project_plan.pdf`, `dev/data/energy_agent_project_supplement.pdf`, `dev/data/paper.pdf` (Gruner et al. Scientific Data 2025)  
> 작성 기준: 2026-06-05 / 최종 업데이트: 2026-06-05

---

## 1. 데이터 구조 요약

### 1.1 데이터셋 개요

| 항목 | 내용 |
|------|------|
| 시설 | Honda R&D Europe GmbH, 독일 오펜바흐 암 마인 |
| 기간 | 2018-01-01 ~ 2024-01-01 (6년) |
| 전기 계량기 | 72개 (Z/ZE 접두사) |
| 열/냉각 계량기 | 9개 (K/W 접두사) |
| 기상 관측소 | 1개 (WeatherStation.Weather) |
| DB 호스트 | `13.209.98.228` / DB명: `cms` |
| DB 테이블 | `reference.corrected_resampled_1h` / `reference.corrected_resampled_15min` |
| 해상도 | 1분(원시), 15분, 1시간 집계 제공 |
| 총 계량기 (DB) | 81개 (ml 파이프라인 대상: 45개) |

### 1.2 측정 변수 분류

#### 전력 도메인

| 변수 | 의미 | 분석 역할 |
|------|------|-----------|
| P (kW) | 순간 유효전력 | 예측 target, 피크 탐지, KPI |
| W (kWh) | 누적 전기에너지 | 월별 사용량, 비용 산정 |
| I1/I2/I3 (A) | 3상 전류 | 상불균형, 과부하, 설비 가동 패턴 |
| U1/U2/U3 (V) | 3상 전압 | 전압 강하, 전력 품질 |
| PF | 역률 | 모터성 부하, 무효전력 증가 탐지 |
| f (Hz) | 전력망 주파수 | 시간 동기화 검증, 데이터 품질 |

#### 열/HVAC 도메인 (냉난방 원인 분석의 핵심)

| 변수 | 의미 | 해석 |
|------|------|------|
| qv (m³/h) | 유량(volume flow) | 펌프·밸브 상태, 열 운반량 |
| V (m³) | 누적 유량 | qv 검증, reset/leap 탐지 |
| Tvl (°C) | 공급온도 | 냉동기/보일러 출구 온도 |
| Trl (°C) | 환수온도 | 건물·설비를 순환 후 돌아온 온도 |
| Tdiff = Tvl-Trl (°C) | 공급-환수 온도차 | 열교환 강도, 냉난방 부하 원인 분석 |
| P/W | 열출력/누적 열에너지 | 냉난방 부하 예측 및 효율 분석 |

> **물리 관계**: P ≈ qv × Tdiff  /  ΔW ≈ P × Δt  /  ΔV ≈ qv × Δt

#### 변수별 권장 용도 요약

| 변수군 | 예측 역할 | 이상탐지 역할 | 주의 |
|--------|-----------|---------------|------|
| P, W | 핵심 target | spike/drop, residual 이상 | W는 누적값 (순간값 아님) |
| qv, Tdiff | 냉난방 설명 feature | 펌프·유량 이상, 열교환 이상 | 미래 사용 시 데이터 누수 주의 |
| Trl/Tvl | HVAC 상태 feature | 환수/공급온도 이상 | 냉방/난방별 해석 다름 |
| I, PF | 설비 가동 feature | 과부하, 상불균형 | 3상 각각 확인 필요 |
| Ta, Igm | 외부 환경 feature | 날씨 영향 분리 | PV 야간 = NaN (결측 아님) |

---

## 2. 핵심 계량기 그룹

### 2.1 전체 전력 (예측·비용의 중심)

| 계량기 | 의미 | 활용 | 주의 |
|--------|------|------|------|
| V.Z81, V.Z82 | 주차장 변압기 (H1·H2·H3 담당) | 전체 전력 집계, 피크 분석 | V.Z81의 W_in = 오버플로우 (-55억 kWh) — P 컬럼만 사용 |
| H2.Z35/Z36 | 오피스 변압기 구형 | 2020.9 이전 전력 | H2.Z351/Z361로 교체됨 |
| H2.Z351/Z361 | 오피스 변압기 교체 버전 | 2020.9 이후 전력 | 동일 ts에 구형+신형이 공존하지 않으므로 합산 가능 |

**그리드 집계 공식**: `V.Z81 + V.Z82 + H2.Z35 + H2.Z36 + H2.Z351 + H2.Z361`

### 2.2 에너지 생산 (자급률 계산)

| 계량기 | 의미 | 주의 |
|--------|------|------|
| H1.Z310, H2.Z311, H3.Z312, V.Z84 | PV 4그룹 | 야간 = NaN (결측 아님). H2.Z311은 2020.11-16 기간 비현실적 값 제거됨 |
| H1.ZE20 | CHP 전기 (2023~ 교정 설치) | H1.Z20과 **동일 선로** — 절대 합산 금지 |
| H1.Z20 | CHP 전기 (~2022 원본) | ZE20 없는 타임스탬프에서만 사용 (coalesce) |
| H1.W12 | CHP 열 생산 | |
| H1.W11 | 총 열 생산 (보일러+CHP) | |

**자급률 공식**: `(PV + CHP) / (Grid + PV + CHP)`

### 2.3 냉방 시스템 (이상탐지의 핵심 대상)

| 구분 | 계량기 | 의미 | 활용 |
|------|--------|------|------|
| 냉방 열량 target | V.K21 | CM1+2+3 냉각 통합 | 냉방 P 예측, 이상탐지. 고장 기간은 서브미터 재구성값 사용 |
| 하위 냉방 (열) | H1.K11 | Emission lab HVAC 3/5 | 실험실 냉방 원인 추적 |
| | H1.K12 | Emission lab HVAC 1/2 | |
| | H1.K14 | Emission lab → office | 냉방 부하 drill-down |
| | H1.K16 | Server room O1 | 서버 냉방 기저부하 분석 |
| | H2.K21 | HVAC office | 오피스 냉방 부하 분석 |
| 냉동기 전력 | H1.Z11, H1.Z12 | CM 2.1 전기 | COP 계산 (cool_output / cool_elec) |
| | H1.Z16 | CM 1 전기 | |
| | H1.Z24, H1.Z25 | CM 3.1, CM 3.2 전기 | |

**COP 공식**: `V.K21.P / (H1.Z11 + H1.Z12 + H1.Z16 + H1.Z24 + H1.Z25)`  
> COP 중앙값 ≈ 2.06 (논문 기준). COP < 1.5이면 효율 저하 의심.

### 2.4 v84 파이프라인 대상 계량기 (45개)

| 그룹 | 역할 | 계량기 수 | 특징 |
|------|------|-----------|------|
| electric / representative | 클러스터 대표 | 15개 | C1~C13, P1~P2 클러스터 |
| electric / singleton | 독립 예측 | 21개 | 클러스터 편입 불가 계량기 |
| thermal / singleton | 열/냉방 | 9개 | 냉방 6개, 난방 2개, K15 1개 |

### 2.5 기상 데이터

| 변수 | 의미 | 활용 |
|------|------|------|
| Ta (°C) | 외기온 | 냉난방 부하 예측, 날씨 영향 분리 |
| Igm (W/m²) | 평균 일사량 | PV 발전 예측, 냉방 부하 보정 |

---

## 3. 데이터 품질 이슈 (주의 계량기)

| 계량기 | 이슈 | 권장 처리 |
|--------|------|-----------|
| H1.Z19 | 2022.3~12.31 — 인근 CHP 배선 간섭으로 비정상 측정 | 해당 기간 제외 또는 품질 이슈 사례로 활용 |
| H2.T.Z33 | 2018.1.17~29 — 잘못된 설정으로 비정상 데이터 | corrected 데이터 또는 기간 제외 |
| H2.Z311 | 2020.11.12~16 — PV conversion factor 오류 + 비현실적 측정 | 보정 데이터(corrected_resampled) 사용 |
| H4.ZE50, H4.ZE51 | Conversion factor 미보정 상태 | 핵심 MVP에서 제외 권장 |
| H2.Z35/Z36 → H2.Z351/Z361 | 2020.9.15 변압기 교체 | 전체 전력 집계 시 교체 전후 계열 연결 |
| V.K21 | 냉각 flow sensor 2회 고장 | corrected_resampled 우선 사용 |

---

## 4. 이상 원인 분류 규칙 (물리 관계 기반)

| 관찰 패턴 | 가능한 원인 | 리포트 문장 예시 |
|-----------|-------------|-----------------|
| 냉방 P 높음 + Ta 높음 | 날씨 영향 → 정상 부하 증가 가능 | "외기온 상승이 냉방 부하 증가의 주요 원인으로 보입니다" |
| 냉방 P 높음 + Ta 평소 수준 | 제어 이상, 내부 부하 증가, 설비 효율 저하 | "외기온으로 설명되지 않는 냉방 부하 증가가 감지됩니다" |
| qv 높음 + Tdiff 낮음 | 펌프 과가동 또는 열교환 효율 저하 | "유량은 증가했지만 온도차가 낮아 냉방 효율 저하 가능성이 있습니다" |
| P 높음 + ΔW 정상~작음 | 순간 P 이상 또는 스케일링 오류 | "누적 에너지와 순간 전력 간 불일치가 있어 계량 이상 가능성이 있습니다" |
| I 상만 지속적으로 높음 | 상불균형 또는 배선/부하 편중 | "특정 상 전류가 높아 전기적 불균형 점검이 필요합니다" |
| PF 급락 + I 증가 | 모터성 부하, 역률 보상 문제 | "역률 저하와 전류 증가가 동시에 나타났습니다" |
| qv=0 + P ≠ 0 | 열량계 오류 가능 (물리적 불가) | "유량=0인데 열출력이 기록되어 센서 이상 의심됩니다" |

---

## 5. ML 파이프라인 구조 (v84 앙상블)

### 5.1 위치 및 실행

```
ml/
└── pipeline/
    ├── train.py        ← 학습 진입점
    ├── inference.py    ← 추론 진입점
    └── common/
        ├── config.py        ← 계량기 스펙 45개, 하이퍼파라미터
        ├── preprocessing.py ← 잔차 타겟 생성, 슬라이딩 윈도우
        ├── model.py         ← LSTM/GRU 모델 정의
        ├── catboost_model.py
        ├── lightgbm_model.py
        ├── ridge.py
        ├── naive.py         ← seasonal naive
        ├── ensemble.py      ← median 앙상블 + bias 보정
        ├── router.py        ← v63 그룹 라우팅
        ├── artifacts.py     ← 저장/로드
        └── db.py            ← DB 조회
```

**학습 실행:**
```bash
# 단일 스레드
.venv-train/bin/python -m ml.pipeline.train --horizon 1

# 병렬 학습 (권장 — M2 Mac 기준 workers=4)
.venv-train/bin/python -m ml.pipeline.train --horizon 1 --workers 4
.venv-train/bin/python -m ml.pipeline.train --horizon 3 --workers 4

# 단일 계량기 디버깅
.venv-train/bin/python -m ml.pipeline.train --horizon 1 --meters H2.Z66
```

> **`--workers N`** — ThreadPoolExecutor로 계량기를 N개 병렬 학습. M2 Mac에서는 4~6이 최적.  
> CPU 백엔드 고정 (MPS 사용 시 CatBoost/LightGBM native thread와 Metal 컨텍스트 충돌 → segfault).

### 5.2 모델 구성 (v84 앙상블)

| 단계 | 모델 | 설명 |
|------|------|------|
| 패스 1 | LSTM v1~v7 | window 24h/168h, LSTM/GRU, dropout/smooth_l1 variant |
| v10/v12/v15 | LSTM 앙상블 | top-2/3 가중 평균, stepwise topk |
| v19 | 그룹 선택 | electric/thermal × representative/singleton 별 최적 앙상블 |
| v24 | convex grid | v10/v12/v15 볼록 조합 최적화 |
| v52 | broad-source gate | LSTM 계열 중 최적 + bias 보정 |
| v53 | CatBoost | flattened window (24h × n_features) |
| v57 | 계량기별 라우팅 | v52/v53/v36 중 val MAE 기준 선택 |
| v61 | LightGBM | step별 독립 모델 (horizon=1: 1개, horizon=3: 3개) |
| v63 | 그룹 라우팅 | v57 vs v61 (그룹 단위 결정) |
| v67 | Ridge | flattened window |
| v71 | Seasonal Naive | 24h 전 같은 시각 P |
| **v84** | **최종 앙상블** | **median(v63, v67, v71) + shrunk hour bias 보정** |

**타겟**: `P(t) - P(t-1)` (잔차) → 복원: `P̂(t) = P(t-1) + ŷ`

### 5.3 아티팩트 구조 (계량기별)

```
ml/pipeline/artifacts/{1h|3h}/{meter_urn}/
├── lstm_v1.pt ~ lstm_v7.pt
├── catboost.cbm
├── lightgbm_t_plus_1.txt (~ t_plus_3.txt)
├── ridge.joblib
├── input_scaler.joblib
├── target_scaler.joblib
├── routing.json           ← v19/v57/v63/lstm_top2/anomaly_threshold
├── hour_bias_corrections.csv
├── feature_columns.json
├── validation_predictions.csv
├── test_predictions.csv
└── metrics.csv
```

---

## 6. 현재 앱 구현 현황 vs. 계획 대비 갭 분석

### 6.1 예측 (Forecast)

| 항목 | 계획 | 현재 구현 | 갭 |
|------|------|-----------|-----|
| 예측 target | 전체 전력 P → 냉방 P → PV | **계량기별 P (45개)** — v84 앙상블 | 냉방 P는 thermal 계량기로 커버됨 ✅ |
| 해상도 | 15분 또는 1시간 | **1h / 3h** | 15분 옵션 없음 |
| 모델 | LightGBM/XGBoost 강력 추천 | **LSTM+CatBoost+LightGBM+Ridge+Naive** | ✅ 초과 달성 |
| Baseline 비교 | Seasonal Naive 필수 | **v71 Naive 앙상블 내 포함** | ✅ 구현됨 |
| 잔차 타겟 | — | **P(t)-P(t-1) 잔차 학습** | lag-1 자기상관 높은 계량기 개선 목적 |
| 이상탐지 연동 | residual 기반 threshold | **anomaly_threshold 학습 시 결정** | 추론 시 is_anomaly 플래그 필요 |

### 6.2 이상탐지 (Anomaly)

| 항목 | 계획 | 현재 구현 | 갭 |
|------|------|-----------|-----|
| 탐지 방식 | residual 기반 동적 threshold | Isolation Forest + LSTM AE 앙상블 | 물리 관계 검증(P-qv-Tdiff) 없음 |
| 결과 형식 | **이벤트 단위** (start/end, 원인 후보) | **포인트 단위** (타임스탬프별) | 연속 이상 → 이벤트 병합 로직 없음 |
| 원인 분류 | 데이터 품질 / 설비 / 물리 불일치 | HIGH/MEDIUM/LOW 심각도만 | 원인 유형 분류 없음 |
| 대상 계량기 | 냉방 설비(V.K21) 중심 | 전체 (anomaly_results 테이블) | 핵심 계량기 필터링 UI 없음 |

### 6.3 보고서 (Report)

| 항목 | 계획 | 현재 구현 | 갭 |
|------|------|-----------|-----|
| KPI | 총 소비, 자급률, COP, 이상 건수 | 월별 KPI 5가지 + PDF | ✅ 잘 구현됨 |
| 냉방-외기온 상관 | 냉방 P vs. Ta 산점도 | ❌ 없음 | 추가 필요 |
| 절감 인사이트 | 피크 절감, COP 개선 제안 | ❌ 없음 | 핵심 누락 |
| 전월 대비 | MoM, YoY 비교 차트 | 데이터 있음, 차트 없음 | 비교 차트 없음 |

### 6.4 대시보드 (Dashboard)

| 항목 | 계획 | 현재 구현 | 갭 |
|------|------|-----------|-----|
| COP 실시간 | 냉동기 효율 live 표시 | 보고서 내 avg_cop만 | 대시보드 위젯 없음 |
| 피크 전력 경보 | 임박 시 실시간 경보 | ❌ 없음 | 미구현 |
| 계량기별 예측 차트 | v84 결과 시각화 | ❌ (학습 완료 후 연결 필요) | inference 연결 후 추가 필요 |

---

## 7. 업그레이드 우선순위

### 🔴 HIGH

#### 7-1. v84 학습 완료 → 백엔드 연결 검증

🔄 **진행 중 (2026-06-05)**: `--horizon 1 --workers 4` 실행 중 (PID 28359)  
- 패스 1 (LSTM v1~v7): ✅ 45/45 완료  
- 패스 2a (CatBoost/LightGBM): 🔄 진행 중 (1/45)  
- 패스 2b (앙상블/라우팅): 대기 중

학습 완료 후 `GET /forecast/predict/v84-ensemble?meter_urn=H2.Z66&horizon=1` 로 추론 동작 확인.  
프론트엔드 예측 차트에 새 모델 결과 연결.

#### 7-2. 이상탐지: 포인트 → 이벤트 단위 변환

연속된 이상 포인트를 하나의 이벤트로 묶기. gap ≤ 2h이면 동일 이벤트.

```python
# 예시 로직
def consolidate_events(df, gap_hours=2):
    # meter_id, severity 기준 연속 이상 → {start, end, duration_h, peak_residual}
```

#### 7-3. 3h 학습 실행

⏳ **대기 중**: 1h 학습 완료 후 실행:
```bash
.venv-train/bin/python -m ml.pipeline.train --horizon 3 --workers 4
```

---

### 🟡 MEDIUM

#### 7-4. 보고서: 냉방-외기온 상관 차트

`/report` 응답에 `cooling_vs_temp` 배열 추가 (월별 Ta 평균 vs. cool_output_P).

#### 7-5. 대시보드: COP 위젯

`loader.py`에서 이미 계산됨. 대시보드에 카드만 추가하면 됨.  
기준값: 중앙값 2.06 / 임계값: 1.5 이하 경보.

#### 7-6. 보고서: 전월 대비 MoM 비교 차트

`monthly_report` 테이블 데이터 이미 있음. 바 차트만 추가.

---

### 🟢 LOW

#### 7-7. 절감 인사이트 섹션

보고서 자동 생성: 피크 절감 시간대, COP 저하 기간 → 연간 절감 kWh 추정.

#### 7-8. 15분 해상도 옵션

`reference.corrected_resampled_15min` 접근 가능. 프론트엔드 토글만 추가.

#### 7-9. 이상탐지 원인 유형 뱃지

`anomaly_type` 필드 활용:
- 데이터 품질 이상 → 회색
- 운영 이상 → 주황
- 물리 불일치 → 빨강

---

## 8. 권장 MVP 범위

| 영역 | 권장 범위 |
|------|-----------|
| 예측 target | v84 앙상블 계량기별 P (1h/3h) |
| 이상탐지 target | 냉방 설비 V.K21.P + 냉동기 전력 합계 중심 |
| 원인 분석 변수 | qv, Tdiff, Trl, Tvl, W, V, 냉동기 전력 P, Ta, Igm |
| 모델 구조 | v84 앙상블 예측 + residual 기반 이상탐지 + rule-based 물리 검증 |
| 최종 산출물 | 계량기별 예측 그래프, 이상 이벤트 테이블, 원인 분석, 자동 리포트, 대화형 질의응답 |

---

## 9. 발표 핵심 메시지

- **81개 중 45개를 목적 기반으로 선별** — 대표 계량기(클러스터 중심)와 독립 계량기로 구분
- 예측 모델은 계량기별 개인화 v84 앙상블 — `P(t)-P(t-1)` 잔차 학습으로 persistence 편향 제거
- 설비 운전 이상탐지는 냉방 설비를 대표 사례로 선정 (전력+열량+유량+온도차 복합 분석)
- 최종 플랫폼은 이상을 탐지하는 데서 끝나지 않고, **원인 후보와 조치 제안을 자동 리포트로 제공**

---

---

## 10. sLLM 설정 및 성능 평가 (2026-06-05)

### 10.1 현재 설정

| 항목 | 값 |
|------|-----|
| 프로바이더 | Ollama (RunPod 호스팅) |
| 엔드포인트 | `https://zbd52qc1mj1soq-11434.proxy.runpod.net` |
| 현재 모델 | `exaone3.5:7.8b` (`.env` → `LLM_MODEL`) |
| API 방식 | Ollama native `/api/chat` (`think: false`) |

> `.env` 파일에서 `LLM_PROVIDER=ollama` / `OLLAMA_URL=.../v1` / `LLM_MODEL=exaone3.5:7.8b` 로 관리.

### 10.2 llm_client.py 수정 사항

gemma4:12b 같은 **thinking 모델**은 OpenAI 호환 API(`/v1/chat/completions`)로 호출 시 응답의 `content` 필드가 항상 빈 문자열 `""` — 실제 답변이 `reasoning` 필드에 들어감.

**해결책**: Ollama provider는 native API(`/api/chat`)로 호출, `think: false` 옵션으로 thinking 모드 비활성화.

```python
# backend/src/agents/llm_client.py
def _ollama_base_url() -> str:
    url = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
    return url.rstrip("/").removesuffix("/v1")

def chat(messages, max_tokens=1024):
    if LLM_PROVIDER == "ollama":
        resp = httpx.post(
            f"{_ollama_base_url()}/api/chat",
            json={"model": LLM_MODEL, "messages": messages,
                  "stream": False, "think": False,
                  "options": {"num_predict": max_tokens}},
            timeout=120,
        )
        return resp.json()["message"]["content"]
```

`backend/src/api/routers/settings.py`의 test-llm 엔드포인트도 동일 방식으로 수정됨.

### 10.3 모델 성능 비교

테스트: 의도 분류 15건 + 응답 품질 4건 + 한국어 능력 3건  
(`backend/scripts/test_sllm_perf.py`)

| 모델 | 의도 분류 정확도 | 분류 평균 레이턴시 | 생성 평균 레이턴시 | 비고 |
|------|:--------------:|:----------------:|:----------------:|----|
| **gemma4:12b** | **93.3%** (14/15) | 1,682ms | 3,634ms | 응답 품질 높음, 한국어 구조화 우수 |
| exaone3.5:7.8b | 86.7% (13/15) | ~835ms\* | 1,420ms | 빠름, 한국어 자연스러움 |

\* 첫 호출 cold start(27초) 제외 시 약 835ms. 포함 시 2,517ms.

**오답 패턴**:
- 공통 오답: `자급률이 낮아진 원인` → `rag` 대신 `anomaly` 분류 (두 모델 모두)
- exaone 추가 오답: `작업지시 내역` → `cms` 대신 `report` 분류

**현재 선택**: exaone3.5:7.8b (속도 우선 — 응답성이 사용자 경험에 직결)  
**대안**: gemma4:12b (정확도 우선 시 교체 — `.env`의 `LLM_MODEL` 값만 변경)

---

*참고: `dev/data/` 디렉터리는 로컬 평가/분석 자료로 관리되며, `.gitignore`에서 제외되어 추적되지 않습니다.*
