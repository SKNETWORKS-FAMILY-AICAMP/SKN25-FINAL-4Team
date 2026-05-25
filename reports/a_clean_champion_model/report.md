# A-clean 프로젝트형 후보군 대표 결과 리포트

## 1. 작업 범위

A-clean 4개 target을 대상으로 프로젝트형 후보군을 학습하고 validation 기준 대표 후보를 정리했다. 최종 champion model은 사용자 승인 전까지 미정으로 둔다. 작업은 실제 git branch에서 진행했다.

```text
branch: exp/a-clean-champion-models-20260520
base_commit: 4d4e7a8
run_label: a_clean_champion_models_20260520
```

입력 dataset:

```text
outputs/modeling/a_clean_targets_1h/
```

대상 target:

```text
T1_group__central_cooling__P
T1_group__local_cooling__P
T1_group__server_power__P
T1_group__ventilation__P
```

signed/net review target은 포함하지 않았다.

## 2. 논문 자료 반영 방식

확인한 Nature Scientific Data EMS 논문은 forecast benchmark 모델표를 제공하는 논문이 아니라 dataset descriptor다. 따라서 이 프로젝트형 후보군 run은 EMS 논문을 직접 재현한 결과가 아니라, 논문이 제시한 modeling/load prediction 활용 방향과 기존 Huang-style 기록을 바탕으로 구성한 A-clean 예측 benchmark다.

다만 별도 energy forecasting benchmark 논문인 Huang et al. 2022 Applied Sciences 논문에는 LSTM, SVR, XGBoost가 비교군으로 포함된다. 본 프로젝트형 후보군 run은 그 논문을 엄밀히 재현한 코드가 아니므로, 이후 논문 방식에 맞춘 별도 보정 benchmark를 추가했다.

```text
reports/a_clean_huang2022_benchmark/report.md
outputs/modeling/a_clean_huang2022_benchmark_1h/
```

초기 SVR-only 보완 산출물도 남아 있으나, 최종 논문 대응 기준은 위 Huang 2022 benchmark 산출물로 본다.

```text
reports/a_clean_svr_supplement/report.md
outputs/modeling/a_clean_svr_supplement_1h/
```

본 프로젝트형 후보군 run의 원칙:

- next-hour forecasting
- target별 independent model
- lag, seasonal lag, rolling statistics 기반 시계열 feature
- weather lag와 calendar cyclic feature 사용
- train split 및 non-gateway row 기준 학습
- validation split 기준 대표 후보 선정

## 3. 프로젝트형 후보군

실행 script:

```text
scripts/modeling/train_a_clean_champion_models_1h.py
```

후보군:

```text
last_value
seasonal_24h
seasonal_168h
Ridge 계열
RobustScaler + Ridge 계열
HistGradientBoosting 계열
ExtraTrees 계열
RandomForest 계열
MLP 계열
```

feature:

```text
target_lag: 1, 2, 3, 6, 12, 24, 48, 72, 168
rolling mean/std/min/max: 3, 6, 12, 24, 168시간
weather lag: Ta/Igm lag 1, 3, 24
weather rolling: Ta/Igm 24h rolling mean
weather mask: Ta_observed_float, Igm_observed_float
calendar: hour/dow/month sin-cos, hour, dow, month, weekend
thermal proxy: CDD18, CDD22, HDD18
```

결측 weather 입력은 target 복원 없이 입력 feature에 한해 causal forward-fill 후 train median fallback을 적용했다.

## 4. 대표 후보 선정 규칙

primary selection metric:

```text
validation_non_gateway_mae
```

단, validation 최저 후보를 그대로 선택하면 local cooling에서 MLP 과적합 위험이 확인되었다. 따라서 최종 선정은 다음 규칙을 사용했다.

```text
validation_non_gateway_mae 최저값의 3% 이내 후보 중 가장 단순한 후보 선택
```

단순성 우선순위:

```text
last_value < seasonal < Ridge < HGB < ExtraTrees < RandomForest < MLP
```

이 규칙은 test를 이용해 직접 고른 것이 아니라, validation 성능이 사실상 동률인 경우 더 단순한 모델을 택하는 one-standard-error style 선택이다.

## 5. Test non-gateway 성능

| target_id | validation 기준 대표 후보 | validation MAE | test MAE | test RMSE | test MAPE | best naive MAE | LSTM MAE | naive 대비 MAE |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| T1_group__central_cooling__P | hgb_iter_320_lr_0.06 | 3102.40 | 2769.05 | 4811.97 | 34.97 | 3064.09 | 3136.97 | 9.63% 개선 |
| T1_group__local_cooling__P | ridge_robust_alpha_0.1 | 973.38 | 902.50 | 1913.60 | 11.38 | 926.78 | 1025.70 | 2.62% 개선 |
| T1_group__server_power__P | ridge_alpha_1000 | 1079.00 | 1162.13 | 1762.53 | 1.86 | 1480.46 | 1636.33 | 21.50% 개선 |
| T1_group__ventilation__P | extra_trees_n_360_depth_none_leaf_3 | 896.48 | 1908.54 | 3480.85 | 10.91 | 2212.48 | 2995.56 | 13.74% 개선 |

## 6. 해석

이번 프로젝트형 후보군 run에서는 A-clean 4개 target 모두에서 `last_value` baseline 대비 test non-gateway MAE가 개선되었다.

개선 폭:

```text
central_cooling: 9.63% 개선
local_cooling: 2.62% 개선
server_power: 21.50% 개선
ventilation: 13.74% 개선
```

해석:

1. `server_power`는 선형 lag/rolling 구조가 잘 맞아 Ridge가 가장 안정적이다.
2. `ventilation`은 비선형 tree ensemble이 가장 좋다.
3. `central_cooling`은 HGB가 안정적인 validation/test 균형을 보였다.
4. `local_cooling`은 persistence가 매우 강하지만, 단순 Ridge 계열이 소폭 개선했다.
5. 단순 LSTM seq24보다 명시적 lag/rolling feature 기반 모델이 전반적으로 강하다.

## 7. 시각화

대표 metric 비교:

```text
outputs/modeling/a_clean_champion_models_1h/figures/champion_metric_comparison.png
outputs/modeling/a_clean_champion_models_1h/figures/champion_mae_improvement_vs_naive.png
```

시계열 예측:

```text
outputs/modeling/a_clean_champion_models_1h/figures/champion_timeseries_test_first_30d.png
outputs/modeling/a_clean_champion_models_1h/figures/champion_timeseries_full_test_daily_mean.png
```

잔차 진단:

```text
outputs/modeling/a_clean_champion_models_1h/figures/champion_residual_mae_by_hour.png
outputs/modeling/a_clean_champion_models_1h/champion_residual_by_time.csv
```

## 8. 산출물

```text
outputs/modeling/a_clean_champion_models_1h/champion_summary.csv
outputs/modeling/a_clean_champion_models_1h/champion_comparison.csv
outputs/modeling/a_clean_champion_models_1h/run_manifest.json
outputs/modeling/a_clean_champion_models_1h/<target_id>/candidate_metrics.csv
outputs/modeling/a_clean_champion_models_1h/<target_id>/champion_manifest.json
outputs/modeling/a_clean_champion_models_1h/<target_id>/champion_predictions.parquet
outputs/modeling/a_clean_champion_models_1h/<target_id>/feature_columns.json
outputs/modeling/a_clean_champion_models_1h/figures/*.png
```

RunPod에는 champion model joblib binary가 남아 있다. Local repo에는 용량 관리를 위해 joblib binary를 회수하지 않았고, 재현 가능한 script와 metrics/predictions/manifest/figures를 회수했다.

remote model binary 용량:

```text
약 156 MiB
```

## 9. Rollback

현재 작업 branch:

```text
exp/a-clean-champion-models-20260520
```

기존 작업 branch로 돌아가려면:

```bash
git switch won/workspace
```

이 champion 작업만 폐기하려면 이 branch의 commit을 revert/reset하거나 branch를 삭제하면 된다.

## 10. 다음 작업

다음 개선은 모델 구조보다 residual 분석이 우선이다.

1. central cooling: cooling season / non-season 분리 metric
2. local cooling: event/peak 구간에서 persistence 대비 개선 여부 확인
3. server power: Ridge 계열 champion 유지, 이상 시점 residual 점검
4. ventilation: hour별 residual과 운전 스케줄 관계 확인
5. 필요 시 champion별 model artifact를 RunPod에서 선별 회수
