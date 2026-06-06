# Import P-Max 후속 테스트 및 시간 기준 결정사항

## 현재 확정된 모델

- 대상: `P`의 15분 구간별 import `P_max`
- 계량기: `V.Z81`, `V.Z82`, `H2.Z35x`, `H2.Z36x`
- 입력: 최근 24시간, 15분 간격 96개 시점
- 출력: 4-step 멀티스텝 예측
  - 요청 시점 기준 향후 15분 구간
  - 요청 시점 기준 향후 30분 구간
  - 요청 시점 기준 향후 45분 구간
  - 요청 시점 기준 향후 60분 구간
- 모델: v29 앙상블
- 후보 모델: v20, v23, v25, v27
- v16: 제외
- 모델 선택 기준: validation RMSE 최소화
- 운영 예측값: 음수 예측을 0으로 보정

## 완료된 재학습 및 추론 검증

- candidate artifact 재학습 완료
- 출력 경로: `artifacts/import_pmax_v29_60min_candidate`
- 기존 운영 artifact는 덮어쓰지 않음
- 전체 학습 시간: 2,198.574초, 약 36분 39초
- test 기간의 각 계량기 마지막 검증 행으로 추론 재현성 확인

| 계량기 | 추론 시간 | 최대 절대 오차 | 허용 오차 | 결과 |
|---|---:|---:|---:|---|
| `V.Z81` | 11.152초 | 0.0000577 | 0.01 | 통과 |
| `V.Z82` | 11.251초 | 0.0000993 | 0.01 | 통과 |
| `H2.Z35x` | 11.936초 | 0.0077412 | 0.01 | 통과 |
| `H2.Z36x` | 12.252초 | 0.0066855 | 0.01 | 통과 |

검증 결과:

- 4개 계량기 모두 학습 시 저장된 raw test 예측을 재현
- 추론 결과 JSON 생성 정상
- 검증 출력 경로: `outputs/candidate_verification`
- 시간 기록: `outputs/candidate_verification/timings.txt`
- 현재 약 11~12초의 실행 시간은 모델 계산뿐 아니라 DB 조회와 feature 생성 시간을 포함

## 후속 비교 테스트

### 1. Feature 축소 테스트

목적:

- 현재 시점당 36개 feature를 줄여도 성능이 유지되는지 확인
- 모델 복잡도, 학습 시간, 추론 시간 및 운영 입력 의존성 감소

비교 지표:

- RMSE
- MAE
- WAPE
- persistence 대비 RMSE 개선율
- persistence 대비 MAE/WAPE 개선율
- 계량기별 학습 시간
- 계량기별 추론 시간
- 음수 예측 비율

테스트 방향:

1. 현재 전체 feature를 기준선으로 사용
2. 중복도가 높은 lag/rolling feature 축소
3. 중요도가 낮은 시간 파생 feature 축소
4. 기상 feature 제외 여부는 별도 비교
5. 성능이 사실상 같으면 더 단순한 feature 구성을 선택

주의:

- train/validation/test 기간과 seed를 기존 실험과 동일하게 유지
- 한 번에 여러 조건을 바꾸지 말고 feature 구성만 변경
- 최종 판단은 4개 계량기 전체 결과를 기준으로 수행

#### 36개에서 24개 축소 실험 결과

실행 조건:

- 테스트 폴더: `test4_15min_no_v16`
- 출력 경로: `test4_15min_no_v16/feature_reduction_reduced24_60min_gpu`
- 입력: 24시간
- 출력: 60분 4-step
- 모델: no-v16 v29
- train/validation/test, seed 및 후보 모델은 기준선과 동일

제거한 12개 feature:

- `dayofweek_cos`
- `month_sin`, `month_cos`
- `is_weekend`
- `is_business_hour`
- `is_morning_ramp`
- `is_lunch_time`
- `is_evening`
- `P_max_lag_2`, `P_max_lag_4`, `P_max_lag_8`
- `P_max_roll_24h_max`

변화:

- 시점당 feature: 36개에서 24개, 33.3% 감소
- flatten 입력 차원: 3,456에서 2,304
- 학습 시간: 2,198.574초에서 1,740.552초
- 학습 시간 20.8% 감소
- 후보 모델 파일 합계: 약 108MB에서 106MB

Clipped 운영 지표 비교:

| 계량기 | RMSE 변화 | MAE 변화 | WAPE 변화 | 판단 |
|---|---:|---:|---:|---|
| `V.Z81` | +0.0445% | +0.0580% | +0.0580% | 사실상 동일 |
| `V.Z82` | +0.0962% | +0.0716% | +0.0716% | 사실상 동일 |
| `H2.Z35x` | -0.1244% | -0.0332% | -0.0332% | 소폭 개선 |
| `H2.Z36x` | -0.0104% | -0.0468% | -0.0468% | 소폭 개선 |

4개 계량기 평균 변화:

- RMSE: +0.0015%
- MAE: +0.0124%
- WAPE: +0.0124%

결론:

- 성능 차이는 사실상 없음
- 더 단순하고 학습이 빠른 `reduced_24` 구성이 유리
- 결측 처리 실험까지 끝난 후 최종 운영 feature로 확정
- 상세 비교:
  `test4_15min_no_v16/feature_reduction_reduced24_60min_gpu/feature_reduction_comparison.csv`

#### 22개 no-weather 및 20개 compact 추가 실험

산출물:

- `no_weather_22`:
  `test4_15min_no_v16/feature_reduction_no_weather22_60min_gpu`
- `compact_20`:
  `test4_15min_no_v16/feature_reduction_compact20_60min_gpu`
- 종합 비교:
  `test4_15min_no_v16/feature_reduction_comparison`

학습 시간:

- `reduced_24`: 1,740.552초
- `no_weather_22`: 1,490.015초
- `compact_20`: 1,340.080초

공통 test timestamp 기준 4개 계량기 평균:

| Profile | RMSE 변화 | MAE/WAPE 변화 |
|---|---:|---:|
| `no_weather_22` | -0.0643% | +0.1702% |
| `compact_20` | +0.0062% | +0.0897% |

`no_weather_22`는 `Ta_mean`, `Igm_mean`을 제거한다.

- V 계량기 dropped rows: 5,198에서 192
- H2 계량기 dropped rows: 5,390에서 384
- 계량기별 학습 윈도우 약 6,500개 증가
- 최신 `Igm_mean` 결측이 추론 윈도우를 막는 문제 제거 가능

`compact_20`은 `U1_mean`, `P_max_lag_1`, `P_max_roll_3h_max`,
`P_max_roll_6h_mean`을 제거한다.

- 성능은 `reduced_24`와 사실상 동일
- 기상 feature는 유지하므로 최신 기상 결측 문제는 해결하지 못함

Persistence 분석:

- actual-vs-predicted 이미지에서 persistence 선 제거
- 각 v29 폴더에 `persistence_behavior_metrics.csv` 생성
- persistence 대비 RMSE 개선: horizon별 평균 약 11~18%
- 실제 변화 방향 일치율: 약 68~73%
- 실제 변화량 대비 예측 변화량: 약 59~72%
- 이전값 1% 이내 복사 비율: 약 6.5~14.8%

판단:

- 이전값을 그대로 출력하는 persistence 모델은 아님
- 변화 방향과 일부 크기를 학습함
- 다만 실제 변화 크기보다 보수적으로 예측하는 평활화 성향은 있음
- 현재 운영 후보는 결측 의존성을 제거하는 `no_weather_22`가 가장 유리

### 2. 짧은 결측 처리 테스트

현재 방식:

- 필수 feature 중 하나라도 NaN인 15분 행은 제거
- 제거 후 15분 연속성이 끊기면 해당 구간을 입력 윈도우로 사용하지 않음
- 보간, ffill, 평균값 대체를 하지 않음

비교할 정책:

1. 현재 방식: NaN 행 제거
2. 과거 입력 윈도우 내부의 최대 1개 구간만 제한적으로 보간
3. `Ta_mean`, `Igm_mean` 같은 기상 feature만 최대 1개 구간 ffill
4. 계량기 핵심 feature인 `P_mean`, `P_max`, `P_std`, `U1_mean`, `PF_mean`은
   최신 시점 결측 시 추론 실패 처리

금지할 처리:

- 최신 입력 시점에 미래값을 사용하는 선형 보간
- 긴 결측 구간 보간
- 결측을 무조건 0으로 대체
- 학습과 추론에서 서로 다른 결측 처리 적용

비교 지표:

- 기존 성능 지표 전체
- 사용 가능한 추론 윈도우 비율
- 최신 시점 추론 성공률
- 보간된 행과 feature 수

## 모델 재학습과 무관한 필수 코드 수정

### 1. Step별 timestamp 수정

현재 문제:

- `t+15` 예측값의 시각 기반 그래프와 일별 피크 시각 평가에
  `target_end_ts`, 즉 `t+60` 시각을 사용

영향:

- RMSE, MAE 및 앙상블 가중치에는 영향 없음
- 그래프가 45분 이동
- 자정 경계의 일별 peak-time 평가 왜곡

수정:

- 각 step에 독립적인 target timestamp 생성
- 최소 수정 시 `t+15` 그래프와 평가는 `target_start_ts` 사용
- 장기적으로 `target_ts_t_plus_1`부터 `target_ts_t_plus_4`까지 저장

### 2. Raw/Clipped 운영 지표 분리

현재 문제:

- 추론에서는 음수 예측을 0으로 보정
- 학습 리포트 metric은 보정 전 raw 예측으로 계산

수정:

- `model_raw` 지표는 모델 진단용으로 유지
- `model_clipped` 지표를 운영 성능으로 추가
- 운영 리포트와 persistence 비교는 clipped 기준을 기본으로 표시

### 3. 과거 실험 경로와 문서

- 과거 `test4_15min_no_v16` 스크립트의 기본 출력 경로는 결과가 혼재될 수 있음
- 과거 README는 15분 단일 horizon 설명이 남아 있음
- 현재 운영 `src` 학습 코드는 candidate 경로를 사용하므로 직접 영향 없음
- 테스트 폴더를 다시 사용할 때 별도 출력 경로를 반드시 지정

## window_ts 확인 결과

DB에서 확인한 사실:

- 테이블: `mart.peak_feature_15min`
- `peak_ts - window_ts`가 0분 이상 14분 이하로 분포
- 표본상 `window_ts=23:30` 행의 `peak_ts`는 `23:30~23:44`에 존재

현재 가장 유력한 해석:

- `window_ts`는 15분 집계 구간 시작 시각
- `window_ts=23:30`은 `[23:30, 23:45)` 원천 구간 집계

단, DB 관리자의 공식 정의를 확인한 후 코드 의미를 최종 확정한다.

## DB 관리자에게 확인할 내용

```text
15분 등간격 데이터를 생성할 때 구간 집계 기준을 어떻게 설정했는지
확인 부탁드립니다.

예를 들어 P의 최댓값을 집계할 때,
- [23:30, 23:45) 구간을 window_ts=23:30으로 저장하는지
- [23:15, 23:30) 구간을 window_ts=23:30으로 저장하는지
- 또는 앞뒤 구간을 포함해 집계하는지

해당 구간에 원본 데이터가 없거나 일부 누락된 경우의 처리 방식도
함께 확인 부탁드립니다.
```

추가로 확인하면 좋은 항목:

- `peak_ts`가 구간 내 실제 최대 P 발생 시각인지
- 구간 경계 포함 규칙
- `observed_points`, `expected_points`, `coverage_ratio` 허용 기준
- P/U1/PF와 Ta/Igm이 동일한 `window_ts`에 모두 완료되는 시점

## DB 답변에 따른 코드 수정안

### 경우 A: window_ts가 구간 시작 시각

예:

- `window_ts=22:30`은 `[22:30, 22:45)` 구간
- 22:45 요청 시 최신 완료 입력 구간은 `window_ts=22:30`

사용자 출력:

- `[22:45, 23:00)` 예측의 표시 시각: 23:00
- `[23:00, 23:15)` 예측의 표시 시각: 23:15
- `[23:15, 23:30)` 예측의 표시 시각: 23:30
- `[23:30, 23:45)` 예측의 표시 시각: 23:45

필요한 수정:

1. `requested_as_of`를 요청 시각 또는 최신 완료 구간 종료 시각으로 정의
2. 최신 입력 `window_ts`가 `requested_as_of - 15분`인지 검사
3. 현재 `data_lag_minutes=15`를 정상 지연으로 오해하지 않도록 이름과 계산 수정
4. 각 예측에 `target_start_ts`, `target_end_ts`를 저장
5. 사용자용 `target_ts`는 구간 종료 시각으로 저장
6. horizon은 요청 시점 기준 15, 30, 45, 60분으로 저장
7. 최신 완료 구간보다 더 이전 데이터로 자동 후퇴하지 않음

이 경우 기존 4-step 모델을 그대로 사용하며 5-step 재학습은 필요 없다.

### 경우 B: window_ts가 구간 종료 시각

예:

- `window_ts=22:45`는 `[22:30, 22:45)` 구간
- 22:45 요청 시 최신 완료 입력 행은 `window_ts=22:45`

필요한 수정:

1. `input_end_ts == requested_as_of` 강제
2. 불완전한 최신 행이면 추론 실패 또는 Airflow 재시도
3. 각 예측의 표시 시각을 23:00, 23:15, 23:30, 23:45로 저장
4. 더 이전 행로 자동 후퇴하지 않음

이 경우도 기존 4-step 모델을 그대로 사용할 수 있다.

### 경우 C: 앞뒤 구간 또는 중앙 정렬 집계

필요한 조치:

- 미래 원천값이 feature에 포함되는지 먼저 확인
- 미래값이 포함된다면 학습 feature leakage 가능성 재검토
- 구간 정의에 맞춰 학습/추론 timestamp 전체를 다시 정렬
- 필요하면 데이터셋 재생성 및 재학습

## 운영 추론 정책

- 사용자 요청 시각 22:45의 반환 시각은 23:00, 23:15, 23:30, 23:45
- 모델은 최근 24시간 96개 구간을 입력으로 사용
- 최신 완료 구간이 기대 시각과 맞지 않으면 조용히 이전 구간을 사용하지 않음
- 필수 feature 적재 완료 후 Airflow가 추론을 실행
- 입력 품질 오류는 재시도 가능한 실패로 반환
- 보간을 적용한다면 어떤 feature와 시각이 보간됐는지 기록

## 최종 진행 순서

1. DB 관리자에게 window_ts 공식 정의 확인
2. timestamp 및 평가 코드 수정
3. feature 축소 비교 실험
4. 결측 처리 비교 실험
5. 최종 feature/결측 정책 선택
6. candidate artifact 검증 후 운영 artifact 승격
