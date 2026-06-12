# 모델 입력/출력 정보

> **최종 업데이트**: 2026-06-09
> **기준 파이프라인**: `energy_v84` v84 (horizon=3h 서비스 기준)

---

## 1. 대상 데이터
- 계량기별 1시간 단위 시계열 데이터 필요
- 예측 대상 컬럼은 `P` (residual 모델을 사용하더라도 최종 예측 대상은 P)

---

## 2. 시간 기준
- 데이터는 1시간 등간격 필요
- timestamp는 UTC 기준
- 운영 추론의 `timestamp`는 **forecast origin 시각**으로 사용
  - 예: `timestamp = 2023-01-03 04:00:00+00:00`, `horizon=3`
  - 출력 target은 `t+1`, `t+2`, `t+3`
  - 즉 예측 대상 시각은 `05:00`, `06:00`, `07:00`
- 동일 계량기/동일 timestamp 중복 row는 없어야 함
- 누락 timestamp는 가능하나 누락 여부가 식별 가능해야 함
  - 모델 전처리 단계에서 누락 구간은 ffill(limit=3) 처리
- raw 초단위 데이터를 1시간 bucket으로 집계할 경우, bucket label 기준은 DB 담당자와 별도 합의 필요
  - 현재 모델은 제공된 학습 데이터의 timestamp 라벨을 그대로 사용
  - 운영 데이터도 학습 데이터와 동일한 bucket label 규약을 유지하는 것이 중요

---

## 3. 입력 컬럼

**전기 계량기:**

| 컬럼 | 설명 |
|------|------|
| `ts` | 타임스탬프 |
| `meter_urn` | 계량기 식별자 |
| `P` | 전력 (예측 대상, W) |
| `U1` | 전압 (V) |
| `PF` | 역률 |

**열량계:**

| 컬럼 | 설명 |
|------|------|
| `ts` | 타임스탬프 |
| `meter_urn` | 계량기 식별자 |
| `P` | 열량 (예측 대상, W) |
| `qv` | 유량 |
| `Tdiff` | 온도차 |

---

## 4. 모델 내부 생성 Feature

모델 전처리에서 자동 생성되며 DB 제공 불필요.

**시간 Feature:**

| Feature | 설명 |
|---------|------|
| `hour_sin` / `hour_cos` | 시간 사인/코사인 인코딩 |
| `day_of_week_sin` / `day_of_week_cos` | 요일 사인/코사인 인코딩 |
| `month_sin` / `month_cos` | 월 사인/코사인 인코딩 |

**파생 변수:**

| Feature | 설명 |
|---------|------|
| `diff_lag24` | `P(t) - P(t-24)` |
| `diff_lag168` | `P(t) - P(t-168)` |
| `is_workday` | 평일 여부 |
| `rolling_mean_24h` | 최근 24시간 P 이동평균 |

---

## 5. 필요한 최소 History
- 안정적인 추론을 위해 **최소 최근 168시간** 이상의 데이터 필요
- 이유:
  - 일부 모델이 168시간 window 사용
  - `diff_lag168` 파생변수 계산 시 최소 168시간 전 P값 필요
- 현재 구현은 v3(168h window)와 `diff_lag168` 계산을 모두 안정적으로 지원하기 위해 최근 약 **340시간 이상**을 조회
  - 3h 기준 내부 조회량: `168 + 168 + horizon + 4 ≈ 343시간`
  - 운영 관점에서는 약 14~15일 이상의 history가 있으면 안정적
- history가 부족하거나 보간 후에도 입력 NaN이 남으면 `status=insufficient_data`로 기록됨

---

## 6. 물리 이상치 처리 (1차 분류)

추론 실행 시 모델 입력 전에 물리 룰 기반으로 이상치를 탐지하고 보정한다.

### 6-1. 물리 룰 정의

| 피처 | 조건 | 룰 코드 |
|------|------|---------|
| `PF` | `abs(PF) > 1.0` | `PF_OUT_OF_RANGE` |
| `U1` | `U1 <= 0` or `U1 > 1000V` | `U1_INVALID` |
| `qv` | `qv < 0` | `QV_NEGATIVE` |

### 6-2. 처리 방식
1. 위반 셀 → `NaN` 변환
2. `ffill(limit=3)` 로 보간 후 모델 입력

### 6-3. 출력 컬럼 (물리 이상 관련)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `physical_flag` | bool | **최근 24시간 내** 물리 룰 위반 발생 여부 ⚠️ |
| `physical_issue_types` | str / null | 위반된 룰 코드. 복수 시 `\|` 구분. 예: `PF_OUT_OF_RANGE\|U1_INVALID` |
| `physical_issue_count` | int | 전체 입력 window 내 위반 행 수 (참고용) |
| `physical_issue_recent_count` | int | 최근 24시간 내 위반 행 수 |
| `physical_issue_pattern` | str | 위반 지속 패턴: `none` / `transient` / `short_sustained` / `sustained` / `long_sustained` |
| `physical_issue_detail` | str / null | 사람이 읽는 설명. 예: `PF abs > 1 in 2 rows; U1 invalid in 1 row` |

> ⚠️ **`physical_flag` 정의 변경 이력**
> - **변경 전 (v84 초기)**: 전체 입력 window(~300시간)에 위반이 하나라도 있으면 `True`
> - **변경 후 (2026-06-09~)**: **최근 24시간 내** 위반이 있을 때만 `True`
> - **변경 이유**: 수백 시간 전 물리 이상이 현재 예측 row에 `True`로 붙는 혼란 방지.
>   전체 window 기준은 `physical_issue_count`로 확인 가능.

**`physical_issue_pattern` 기준:**

| 값 | 기준 |
|----|------|
| `none` | 위반 없음 |
| `transient` | 최대 연속 위반 1~2시간 |
| `short_sustained` | 3~6시간 연속 |
| `sustained` | 7~23시간 연속 |
| `long_sustained` | 24시간 이상 연속 |

---

## 7. 모델 출력 (예측 + 경보)

현재 시점 `t` 기준. `status=success`인 행에만 유효한 값이 채워짐.

### 7-1. 식별자

| 컬럼 | 설명 |
|------|------|
| `meter_urn` | 계량기 ID |
| `model_urn` | 실제 사용한 모델 ID (전이 계량기는 대표 urn) |
| `timestamp` | 추론 기준 시각 (UTC) |
| `horizon` | 예측 horizon (현재 3) |
| `status` | `success` / `insufficient_data` / `no_artifact` / `error` |

운영 DB에 step별 row 형태로 적재하는 경우 권장 식별자는 아래와 같다.

| 컬럼 | 설명 |
|------|------|
| `forecast_origin_ts` | 추론 실행 기준 시각. 현재 CSV의 `timestamp`와 동일 |
| `target_ts` | 실제 예측 대상 시각. `forecast_origin_ts + lead_step hours` |
| `lead_step` | `1` / `2` / `3` |
| `horizon` | 현재 서비스 기준 `3` |

현재 CSV는 한 row에 `t+1~t+3`이 함께 들어가는 wide 형태이며, DB 적재 시 wide 형태 또는 step별 long 형태 중 하나를 선택하면 된다.

### 7-2. step별 예측 및 경보 (k = 1, 2, 3)

| 컬럼 | 설명 |
|------|------|
| `pred_t_plus_{k}` | t+k 시각 P 예측값 (W) |
| `target_hour_t_plus_{k}` | 예측 대상 시각의 hour (0~23) |
| `threshold_lower_t_plus_{k}` | 경보 하한 threshold (val 2 percentile 기준) |
| `threshold_upper_t_plus_{k}` | 경보 상한 threshold (val 98 percentile 기준) |
| `warning_t_plus_{k}` | t+k 경보 여부 (bool) |
| `warning_type_t_plus_{k}` | 경보 방향: `high` / `low` / `none` |
| `low_sample_t_plus_{k}` | threshold 산출 샘플 수 부족 여부 (bool) |

> **주/보조 구분**: `t+3`이 메인 경보 지표, `t+1` / `t+2`는 보조 지표.

### 7-3. 집계 경보

| 컬럼 | 설명 |
|------|------|
| `warning_flag` | `t+1` / `t+2` / `t+3` 중 하나라도 `True`면 `True` (OR 집계) |

### 7-4. 계량기 이슈 메타 (meter_tags.csv 기반)

| 컬럼 | 설명 |
|------|------|
| `meter_issue_types` | 알려진 이슈 유형. 예: `dormant`, `level_change`, `sign_flip` |
| `meter_issue_detail` | 이슈 상세 설명 |
| `meter_issue_severity` | `high` / `medium` / `low` / null |

> 이슈 태그가 없는 계량기는 세 컬럼 모두 `null`.

### 7-5. 입력 품질 및 경보 해석 보조

| 컬럼 | 설명 |
|------|------|
| `input_quality` | 모델 입력 window 품질 등급: `good` / `warning` / `bad` |
| `input_missing_count` | 입력 window 내 누락 timestamp 또는 입력 feature NaN 행 수 |
| `input_physical_count` | 입력 window 내 물리 룰 위반 행 수 |
| `input_imputed_count` | `ffill(limit=3)`로 보간된 row 수 |
| `warning_reason_code` | 경보 해석 보조 코드 (아래 우선순위 참고) |
| `warning_reason_detail` | 사람이 읽을 수 있는 경보 보조 설명 |

`warning_reason_code`는 원인 확정값이 아니라 경보 해석을 돕는 보조 정보이다.

**`warning_reason_code` 값 및 우선순위:**

| 코드 | 의미 | 우선순위 |
|------|------|---------|
| `NO_PREDICTION` | 추론 실패 (insufficient_data / error 등) | 0 (최우선) |
| `KNOWN_METER_ISSUE` | meter_tags에 등록된 알려진 계량기 이슈 | 1 |
| `INPUT_QUALITY_ISSUE` | 입력 데이터 품질 문제 (missing / physical 이상) | 2 |
| `HIGH_LOAD_VS_USUAL_HOUR` | 해당 시간대 상한 threshold 초과 | 3 |
| `LOW_LOAD_VS_USUAL_HOUR` | 해당 시간대 하한 threshold 미달 | 3 |
| `NONE` | 경보 없음 | — |

**`input_quality` 등급 기준:**

| 등급 | 조건 |
|------|------|
| `good` | 문제 row 없음 (missing=0, physical=0) |
| `warning` | 문제 row 1~3개 |
| `bad` | 문제 row 4개 이상, 또는 추론 실패 |

> `input_physical_count` 기준 window: 대부분 계량기 24h, H2.ZE66(3h)만 168h (v3 LSTM 사용).

---

## 8. Threshold 설계

| 항목 | 내용 |
|------|------|
| 기준 | val 기간(2022-01-01 ~ 2023-01-01) actual P |
| 방식 | 시간대별(hour 0~23) 독립 2~98 percentile |
| 방향 | 양방향 (상한 초과 / 하한 미달) |
| floor 적용 | `p_lower ≈ 0`인 행(abs < 10W)은 `p_lower = -50W`로 하향 (dormant 계량기 오탐 방지) |
| 저장 위치 | `artifacts/thresholds/val_thresholds.csv` |

### Threshold 운영 정책

- threshold는 **모델 재학습과 별도로 관리**한다. 재학습만으로는 threshold가 자동 재생성·승격되지 않는다.
- val 기간 데이터와 계량기 구성이 바뀌지 않는 한 기존 threshold를 그대로 유지한다.
- threshold 재생성이 필요한 경우:
  - 예측 대상 계량기 목록 변경
  - val/test split 기간 변경
  - floor 값 또는 percentile 기준 변경
- 재생성 시 `scripts/compute_thresholds.py`를 별도 실행하고, 결과를 수동으로 검토 후 배포한다.

---

## 9. 운영 추론과 검증 배치의 구분

운영 추론은 사용자가 설정한 단일 `forecast_origin_ts` 기준으로 실행한다.

```text
forecast_origin_ts = t
→ pred_t_plus_1, pred_t_plus_2, pred_t_plus_3 생성
→ step별 warning 및 warning_reason 생성
→ DB에 적재
```

2023년 전체 1년 배치 추론은 운영 기능이 아니라 threshold 민감도, 경보율, 모델 안정성 검증을 위한 개발/검증용 시뮬레이션이다.

---

## 10. 재학습 artifact 운영 정책

현재 실험 파이프라인은 재학습 결과를 `artifacts/`에 직접 저장한다. 운영에서는 아래 구조가 더 안전하다.

```text
artifacts/
  active/                 # 현재 운영 추론이 읽는 검증 완료 artifact
  candidate/run_xxx/      # 재학습 결과물
  archive/run_prev/       # 이전 active 백업
```

권장 흐름:

1. 재학습 결과를 `candidate/run_xxx/`에 저장
2. candidate artifact 누락 여부, 성능, threshold, 추론 결과 검증
3. 문제가 없으면 기존 `active/`를 먼저 `archive/`로 백업
4. 백업 성공 후 candidate를 `active/`로 승격
5. 승격 후 단일 timestamp smoke test 수행

중요: candidate 검증 후 active용으로 다시 학습하지 않고, **검증된 candidate artifact 자체를 승격**해야 한다.

---

## 11. 미구현 / 향후 추가 예정

| 컬럼 | 설명 | 상태 |
|------|------|------|
| `actual_t_plus_{k}` | 실측값 (사후 피드백용) | 미구현 |
| `error_t_plus_{k}` | `\|actual - pred\|` | 미구현 |
| `anomaly_t_plus_{k}` | 실측 기반 이상 여부 | 미구현 |
| `physical_issue_last_seen_hours_ago` | 마지막 물리 이상 발생 시점 | 미구현 |
