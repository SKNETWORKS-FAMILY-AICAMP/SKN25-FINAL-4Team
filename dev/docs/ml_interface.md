# ML 모델 인터페이스 계약 (ML Interface Contract)

ML 팀이 만든 **전력 예측 / 이상탐지** 모델이 백엔드에 그대로 꽂히도록 지켜야 할 인터페이스 규약입니다.
이 계약만 지키면 라우터·에이전트·CMS(설비 상태/진단/예지보전/작업지시) 코드를 **건드리지 않고** 모델을 교체할 수 있습니다.

> 핵심 원칙: **CMS 전 기능(① 설비 상태 · ② 진단 · ③ 예지보전 · ④ 작업지시)은 `anomaly_results` 테이블을 소비**합니다.
> 따라서 이상탐지 모델 교체가 CMS에 가장 큰 영향을 줍니다. 전력 예측은 수요 예측 탭·제어 권고에만 영향.

---

## 0. 모델 파일 배치

- 컨테이너 내부 경로: `ML_MODEL_DIR` (기본 `/app/ml_models`)
- 도커 마운트: `../ML/outputs/models` → `/app/ml_models` (read-only)
- 각 모델의 `is_available()`가 필요한 `.pkl`/아티팩트 존재 여부로 "교체 완료"를 판단합니다. 파일명을 바꾸면 `is_available()`도 같이 맞춰야 합니다.

---

## 1. 입력 데이터 계약 (공통)

모든 모델의 입력 `df`는 `data/loader.py`의 `load_range(start, end)` / `load_reduced(...)` 출력입니다.

| 컬럼 | 의미 | 단위 |
|---|---|---|
| `ts` | 타임스탬프 (tz-aware, Europe/Berlin) | datetime |
| `grid_P` | 계통 인입 전력 | W |
| `pv_P` | 태양광 생산 (abs 적용) | W |
| `chp_P` | 열병합 전기 생산 (abs) | W |
| `heat_total_P` / `chp_heat_P` | 총 열 / 열병합 열 | W |
| `cool_output_P` / `cool_elec_P` | 냉방 출력 / 냉방 전기 | W |
| `Igm` / `Ta` | 일사량 / 외기온 | W/m², °C |
| `cop` | 냉방 성능계수 = `cool_output_P / cool_elec_P` | – |
| `self_sufficiency` | (pv+chp)/(grid+pv+chp) | 0~1 |

- **부호 규칙**: 양수=소비(inflow), 음수=생산(outflow). PV·CHP는 이미 abs 적용됨.
- 결측은 NaN으로 들어올 수 있음. 모델 내부에서 방어할 것 (특히 `cool_elec_P=0` → COP 0나누기).

---

## 2. 이상탐지 모델 계약 ⭐ (CMS의 심장)

### 2-1. 주 모델 — `models/anomaly/residual_model.py`

```python
def predict_anomaly(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame: ...
def is_available() -> bool: ...
```

**반환 DataFrame 컬럼 (필수 — 이 이름/의미를 바꾸지 말 것):**

| 컬럼 | 타입 | 의미 |
|---|---|---|
| `ts` | datetime | 시점 |
| `actual_w` | float | 실측 전력 (W) |
| `predicted_w` | float | 모델 예측 (W) |
| `residual_w` | float | 잔차 절댓값 = \|실측-예측\| (W) |
| `res_flag` | int(0/1) | `residual_w / threshold ≥ 1.0` 이면 1 |
| `if_flag` | int(0/1) | IsolationForest 플래그 (**현재 구현에서 항상 0**, 미사용) |
| `vote` | int(0/1) | 현재 `res_flag`와 동일 (if_flag 미사용으로 0~1) |
| `anomaly_level` | str | `HIGH` / `MEDIUM` / `LOW` / `NORMAL` |

**`anomaly_level` 판정 기준** (`ratio = residual_w / threshold`):
- ratio ≥ 2.0 → `HIGH`
- ratio ≥ 1.5 → `MEDIUM`
- ratio ≥ 1.0 → `LOW`
- ratio < 1.0 → `NORMAL`

- `is_available()`은 v84 pipeline artifacts(`ml/pipeline/artifacts/1h/{meter_urn}/lstm_*.pt` 등)가 하나 이상 있으면 `True`.
- 모델이 없으면(`is_available()==False`) 자동으로 아래 앙상블 폴백을 사용.

### 2-2. 폴백 — `models/anomaly/ensemble.py`

```python
def run(df: pd.DataFrame, save_to_db: bool = True) -> pd.DataFrame: ...
```
- 반환: 이상 행만 + `vote_count`, `severity`, `anomaly_type` 컬럼.

### 2-3. 영속 계약 — `anomaly_results` 테이블 ⭐⭐

**CMS·대시보드·챗봇이 실제로 읽는 곳은 이 테이블입니다.** 모델 결과는 여기에 저장되어야 소비됩니다.

| 컬럼 | 의미 | CMS가 쓰는가 |
|---|---|---|
| `timestamp` | 이상 발생 시각 (tz-aware) | ✅ 윈도우·추세 |
| `anomaly_type` | 유형 (아래 어휘 고정) | ✅ **설비 매핑** |
| `severity` | `HIGH`/`MEDIUM`/`LOW` (`CRITICAL` 허용) | ✅ 헬스 가중 |
| `gateway_failure` | 게이트웨이 장애 구간 여부 (bool) | ✅ 기본 제외 |
| `description` | 사람이 읽는 설명 | ✅ 진단 근거 |
| `actual_w`/`predicted_w`/`residual_w` | 잔차 3종 (W) | ✅ 진단 사례 |
| `score_stat`/`score_iso`/`score_lstm`/`vote_count` | 점수/투표 | 표시용 |
| `meter_id`/`source` | 모델 출처 태그 | – |

**`anomaly_type` 고정 어휘 (설비 매핑에 직결 — 새 값 추가 시 알려줄 것):**

| anomaly_type | 귀속 설비(CMS) |
|---|---|
| `PowerSpike`, `NightConsumption` | 계통/수전 (grid) |
| `COPDrop` | 냉방설비 (cooling) |
| `CHPOutage` | 열병합 (chp) |
| `PVNightNonZero` | 태양광 (pv) |
| `Unknown` | 미분류 (어느 설비에도 안 잡힘) |

> ⚠️ **`anomaly_type` 값이 바뀌면 CMS 설비 매핑이 깨집니다.** 새 유형을 추가하려면 `backend/src/api/routers/cms.py`의 `EQUIPMENT[*].types`에 매핑을 추가해야 하니 keun에게 공유.
> ⚠️ `severity`는 `HIGH/MEDIUM/LOW`(+`CRITICAL`)만. 다른 문자열은 헬스 점수에서 기본 가중(0.3) 처리됨.

---

## 3. 전력 예측 모델 계약 (보조 — 수요 예측·제어 권고)

### 3-1. 주 모델 — `ml/pipeline/inference.py` (v84 앙상블)

```python
def is_available(meter_urn: str, horizon: int) -> bool: ...
# 추론 진입점: backend/src/api/routers/forecast.py → ml.pipeline.inference
```

**앙상블 구성**: v63·v67·v71 세 LSTM 버전의 예측을 **median**으로 결합 후 시간대별 shrunk bias correction(gain 1.30) 적용.

**아티팩트 위치**: `ml/pipeline/artifacts/{1h|3h}/{meter_urn}/`
- `lstm_{version}.pt` — LSTM 가중치 (v1·v2·v3·v4·v6·v7 중 상위 2개 사용)
- `catboost.cbm`, `lightgbm_t_plus_{N}.txt` — 부스팅 모델
- `input_scaler.joblib`, `target_scaler.joblib` — 스케일러
- `routing.json` — 계량기별 앙상블 구성 및 이상탐지 threshold

**반환 컬럼** (`GET /forecast/predict/v84-ensemble`):
- `ts`, `predicted_kw`, `actual_kw` (historical), `horizon`

### 3-2. 폴백 — Seasonal Naive

artifacts 없을 때 사용. 동일 요일·시간대의 과거 평균으로 예측.

---

## 4. 예지보전(③) — 현재는 별도 트랙

- 현재 `/cms/predictive`는 **추세 외삽**(냉방=월 COP, 그 외=월 이상 발생률)이며 ML 모델을 쓰지 않음.
- 팀이 **RUL/열화 예측 모델**을 만들면 `backend/src/api/routers/cms.py`의 `compute_predictive()` 내부만 교체.
- 입력 후보: `anomaly_results` 추세 + `monthly_report.avg_cop` + 전기 시그니처(전압/전류/역률, `ems.cr_measurement_*`).
- **수요 예측 모델 ≠ RUL 모델** — 혼동 주의.

---

## 5. 절대 깨면 안 되는 것 (요약)

1. `predict_anomaly` 반환 컬럼명 8종 (특히 `anomaly_level`, `residual_w`).
2. `anomaly_results`의 `timestamp / anomaly_type / severity / gateway_failure`.
3. `anomaly_type` 고정 어휘 (변경 시 CMS 매핑 동기화 필요).
4. 예측 반환 컬럼·단위 (`predicted_kw` 단위 kW).
5. `is_available()` — `ml/pipeline/artifacts/` 내 `.pt` / `.cbm` / `.joblib` 파일 존재 여부와 일치.

문의/변경은 인터페이스 레이어 담당(keun)과 사전 공유.
