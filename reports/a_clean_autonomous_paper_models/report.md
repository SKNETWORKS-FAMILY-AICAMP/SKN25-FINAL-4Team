# A-clean paper-model 자율 실험 리포트

## 1. 실행 배경

사용자 승인에 따라 기존 A-clean 4개 target을 대상으로, RunPod를 유지한 상태에서 추가 모델 실험을 자율 수행했다. 이 작업은 기존 A-clean 첫 LSTM 결과와 분리된 branch-like run으로 기록한다.

```text
run_label: a_clean_autonomous_paper_models
input_dataset: outputs/modeling/a_clean_targets_1h/
remote_workdir: /workspace/ems_a_clean
local_output: outputs/modeling/a_clean_paper_models_1h/
```

대상 target은 A-clean 4개로 고정했다.

```text
T1_group__central_cooling__P
T1_group__local_cooling__P
T1_group__server_power__P
T1_group__ventilation__P
```

signed/net review target은 포함하지 않았다.

## 2. 논문 확인 사항

확인한 Nature Scientific Data 논문은 데이터셋 descriptor 성격이다.

```text
A Real-World Energy Management Dataset from a Smart Company Building for Optimization and Machine Learning
DOI: 10.1038/s41597-025-05186-3
```

논문 본문은 데이터 수집, 정정, resampling, issue labeling, validation을 중심으로 설명하며, 특정 forecast benchmark 모델 결과표를 제시하지 않는다. 따라서 이번 자율 실험에서는 논문이 언급한 `modeling`, `machine learning`, `load prediction` 활용 방향과 기존 프로젝트의 Huang-style forecasting 기록을 기준으로, 다음의 paper-adjacent forecasting suite를 구성했다.

## 3. 실행 모델

실행 script:

```text
scripts/modeling/train_a_clean_paper_models_1h.py
```

모델 suite:

| 모델 | 의도 |
|---|---|
| `ridge` | lag/calendar/weather 기반 선형 회귀 기준선 |
| `hist_gradient_boosting` | 비선형 tabular boosting 기준선 |
| `random_forest` | tree ensemble 기준선 |
| `extra_trees` | randomized tree ensemble 기준선 |
| `mlp` | feed-forward neural regression 기준선 |

이전 실행 결과와 비교 대상으로 사용한 모델:

```text
last_value / seasonal naive
LSTM seq_len=24 independent target model
```

## 4. Feature 정책

모든 모델은 next-hour target 예측을 위해 causal feature만 사용했다.

```text
target_lag: 1, 2, 3, 24, 48, 168
rolling_mean/std: 3, 24, 168시간, 모두 shift(1) 후 계산
weather: Ta/Igm lag_1, lag_24
weather observed mask: Ta_observed_float, Igm_observed_float
calendar: hour/dow/month sin-cos
```

학습 row 기준:

```text
train split
non_gateway_outage
target_observed = True
is_full_component_observed = True
is_replacement_gap = False
```

결측 weather 입력은 target 복원 없이 입력 feature에 한해 causal forward-fill 후 train median fallback을 적용했다.

## 5. RunPod 환경

```text
GPU: NVIDIA RTX 2000 Ada Generation, 16GB VRAM
Python: 3.12.3
Torch: 2.8.0+cu128
CUDA: available
```

sklearn 계열 모델은 CPU 기반으로 학습했다. 기존 LSTM은 CUDA로 학습된 결과를 비교에 사용했다.

## 6. Test non-gateway 성능 비교

| target_id | best naive | naive MAE | LSTM MAE | best paper-model | paper-model MAE | paper-model RMSE | paper-model MAPE | MAE 개선율 vs naive |
|---|---|---:|---:|---|---:|---:|---:|---:|
| T1_group__central_cooling__P | last_value | 3064.09 | 3136.97 | mlp | 2734.79 | 4757.80 | 33.73 | 10.75% 개선 |
| T1_group__local_cooling__P | last_value | 926.78 | 1025.70 | ridge | 942.68 | 1950.01 | 12.03 | 1.72% 악화 |
| T1_group__server_power__P | last_value | 1480.46 | 1636.33 | ridge | 1186.42 | 1772.15 | 1.90 | 19.86% 개선 |
| T1_group__ventilation__P | last_value | 2212.48 | 2995.56 | extra_trees | 1887.37 | 3534.89 | 10.88 | 14.69% 개선 |

## 7. 핵심 해석

첫 LSTM만 봤을 때는 MAE 기준으로 `last_value` baseline을 넘지 못했다. 그러나 lag/rolling/calendar/weather 기반의 paper-adjacent model suite를 돌리자 4개 중 3개 target에서 `last_value`를 MAE 기준으로 개선했다.

개선 target:

```text
central_cooling: MLP가 last_value 대비 MAE 10.75% 개선
server_power: Ridge가 last_value 대비 MAE 19.86% 개선
ventilation: ExtraTrees가 last_value 대비 MAE 14.69% 개선
```

개선 실패 target:

```text
local_cooling: Ridge가 최선이지만 last_value 대비 MAE 1.72% 악화
```

해석상 중요한 점은 다음과 같다.

1. A-clean에서는 복잡한 sequential LSTM보다, 명시적인 lag/rolling feature를 넣은 tabular model이 더 강했다.
2. `server_power`는 선형 lag 구조가 잘 맞아 Ridge가 가장 우수했다.
3. `ventilation`은 비선형 tree ensemble이 유리했다.
4. `central_cooling`은 MLP가 개선했지만 MAPE는 여전히 높아 season/low-load 구간 분리 평가가 필요하다.
5. `local_cooling`은 직전값 persistence가 매우 강해 추가 feature가 평균 절대오차를 거의 개선하지 못했다.

## 8. 시각화

대표 비교 그림:

```text
outputs/modeling/a_clean_paper_models_1h/figures/comparison_naive_lstm_paper_models.png
```

target별 모델 MAE 비교:

```text
outputs/modeling/a_clean_paper_models_1h/figures/T1_group__central_cooling__P_paper_model_mae.png
outputs/modeling/a_clean_paper_models_1h/figures/T1_group__local_cooling__P_paper_model_mae.png
outputs/modeling/a_clean_paper_models_1h/figures/T1_group__server_power__P_paper_model_mae.png
outputs/modeling/a_clean_paper_models_1h/figures/T1_group__ventilation__P_paper_model_mae.png
```

prediction 14일 zoom view:

```text
outputs/modeling/a_clean_paper_models_1h/figures/T1_group__central_cooling__P_best_prediction_14d.png
outputs/modeling/a_clean_paper_models_1h/figures/T1_group__local_cooling__P_best_prediction_14d.png
outputs/modeling/a_clean_paper_models_1h/figures/T1_group__server_power__P_best_prediction_14d.png
outputs/modeling/a_clean_paper_models_1h/figures/T1_group__ventilation__P_best_prediction_14d.png
```

14일 plot은 전체 test metric 산출과 별개로 패턴 확인용 zoom view다.

## 9. 산출물

Local에 회수한 산출물:

```text
outputs/modeling/a_clean_paper_models_1h/paper_model_metrics.csv
outputs/modeling/a_clean_paper_models_1h/paper_model_best_test_non_gateway.csv
outputs/modeling/a_clean_paper_models_1h/comparison_vs_baselines.csv
outputs/modeling/a_clean_paper_models_1h/run_manifest.json
outputs/modeling/a_clean_paper_models_1h/*/model_metrics.csv
outputs/modeling/a_clean_paper_models_1h/*/predictions_*.parquet
outputs/modeling/a_clean_paper_models_1h/figures/*.png
```

Remote RunPod에는 joblib model binary가 생성되어 있다. 전체 joblib 용량은 약 2.54 GiB라 local repo에는 회수하지 않았다. Local에는 metrics, predictions, manifest, figures만 회수했다.

## 10. 다음 권장 작업

현재 결과 기준으로 바로 더 복잡한 deep learning으로 가기보다 다음 순서를 권장한다.

1. target별 best model을 기준으로 residual을 hour/month/load-bin으로 분해한다.
2. `central_cooling`은 냉방 season과 low-load 구간을 분리 평가한다.
3. `local_cooling`은 persistence가 너무 강하므로 event/peak 구간 별도 metric을 확인한다.
4. LSTM은 단순 seq24 대신 lag/rolling feature가 들어간 hybrid MLP/LSTM 또는 TCN 계열을 검토한다.
5. champion recipe는 단일 모델이 아니라 target별 family로 둘 가능성을 열어둔다.

## 11. 결론

A-clean 첫 LSTM 결과만으로는 성능이 약했지만, paper-adjacent tabular/ML suite에서는 `central_cooling`, `server_power`, `ventilation` 3개 target에서 `last_value` 기준선을 명확히 개선했다. 현재 A-clean에서 가장 안전한 다음 기준선은 `last_value`가 아니라 다음 조합이다.

```text
central_cooling: MLP
local_cooling: last_value 유지 또는 Ridge 근접 후보
server_power: Ridge
ventilation: ExtraTrees
```
