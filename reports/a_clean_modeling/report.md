# A-clean 1시간 예측 baseline 결과

## 1. 실행 범위

A-clean 소비 target 4개에 대해 baseline과 독립 LSTM을 실행했다.

```text
T1_group__central_cooling__P
T1_group__local_cooling__P
T1_group__server_power__P
T1_group__ventilation__P
```

입력 dataset은 다음 경로만 사용했다.

```text
outputs/modeling/a_clean_targets_1h/
```

signed/net review target은 포함하지 않았다.

```text
T1_group__emission_lab__P
T2_building__H1__P
T2_building__V__P
```

## 2. RunPod 환경

```text
SSH: root@213.173.99.11:16213
GPU: NVIDIA RTX 2000 Ada Generation, 16GB VRAM
Python: 3.12.3
Torch: 2.8.0+cu128
CUDA: available
```

작업 디렉터리:

```text
/workspace/ems_a_clean
```

local 회수 위치:

```text
outputs/modeling/a_clean_baselines_1h/
outputs/modeling/a_clean_lstm_1h/
```

## 3. Baseline

실행 script:

```text
scripts/modeling/train_a_clean_baselines_1h.py
```

baseline 정의:

| baseline | 정의 |
|---|---|
| `last_value` | 직전 1시간 값 |
| `seasonal_24h` | 24시간 전 값 |
| `seasonal_168h` | 168시간 전 값 |

A-clean test 구간에서는 4개 target 모두 `last_value`가 MAE 기준 최선 baseline이었다.

## 4. LSTM

실행 script:

```text
scripts/modeling/train_a_clean_lstm_1h.py
```

학습 방식:

```text
target별 independent LSTM 4개
seq_len = 24
horizon = 1
hidden_size = 64
num_layers = 2
dropout = 0.2
batch_size = 256
optimizer = AdamW
loss = MSELoss
max_epochs = 50
patience = 10
```

입력 feature:

```text
target_scaled
Ta_scaled
Igm_scaled
Ta_observed_float
Igm_observed_float
hour_sin, hour_cos
dow_sin, dow_cos
month_sin, month_cos
```

scaler는 `a_clean_targets_1h/scaler_manifest.json`의 target별/feature별 min-max 기준을 사용했다.

## 5. Test non-gateway 결과

| target_id | best_naive | naive_mae | naive_rmse | naive_mape | lstm_epochs | lstm_mae | lstm_rmse | lstm_mape | MAE 변화 | RMSE 변화 |
|:---|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| T1_group__central_cooling__P | last_value | 3064.09 | 5846.01 | 27.15 | 50 | 3136.97 | 5215.61 | 44.31 | +72.88 | -630.41 |
| T1_group__local_cooling__P | last_value | 926.78 | 2078.64 | 11.00 | 38 | 1025.70 | 1982.95 | 15.45 | +98.92 | -95.69 |
| T1_group__server_power__P | last_value | 1480.46 | 2042.45 | 2.38 | 50 | 1636.33 | 2323.32 | 2.66 | +155.87 | +280.87 |
| T1_group__ventilation__P | last_value | 2212.48 | 6089.44 | 14.12 | 50 | 2995.56 | 5048.76 | 13.98 | +783.08 | -1040.68 |

## 6. 해석

첫 LSTM recipe는 smoke/full run 모두 정상 동작했지만, MAE 기준으로는 아직 `last_value` baseline을 넘지 못했다.

다만 RMSE 기준으로는 다음 target에서 개선이 있었다.

```text
central_cooling: RMSE 개선
local_cooling: RMSE 소폭 개선
ventilation: RMSE 개선
```

이는 LSTM이 큰 오차 일부를 줄였지만, 평균 절대오차 관점에서는 직전값 persistence를 아직 이기지 못했다는 의미다. A-clean 첫 benchmark에서는 `last_value`가 강한 기준선으로 확인되었다.

## 7. 시각화

다음 그림을 생성했다.

```text
outputs/modeling/a_clean_lstm_1h/figures/metric_comparison_test_non_gateway.png
outputs/modeling/a_clean_lstm_1h/figures/T1_group__central_cooling__P_test_14d_prediction.png
outputs/modeling/a_clean_lstm_1h/figures/T1_group__local_cooling__P_test_14d_prediction.png
outputs/modeling/a_clean_lstm_1h/figures/T1_group__server_power__P_test_14d_prediction.png
outputs/modeling/a_clean_lstm_1h/figures/T1_group__ventilation__P_test_14d_prediction.png
```

14일 prediction plot은 전체 test metric 계산과 별개로, 패턴 확인용 zoom view다.

## 8. 산출물

Baseline:

```text
outputs/modeling/a_clean_baselines_1h/baseline_metrics.csv
outputs/modeling/a_clean_baselines_1h/baseline_best_test_non_gateway.csv
outputs/modeling/a_clean_baselines_1h/*/baseline_predictions.parquet
```

LSTM:

```text
outputs/modeling/a_clean_lstm_1h/lstm_metrics.csv
outputs/modeling/a_clean_lstm_1h/*/model.pt
outputs/modeling/a_clean_lstm_1h/*/loss_history.csv
outputs/modeling/a_clean_lstm_1h/*/predictions_validation.parquet
outputs/modeling/a_clean_lstm_1h/*/predictions_test.parquet
outputs/modeling/a_clean_lstm_1h/*/run_manifest.json
```

## 9. 다음 결정 필요 사항

다음 단계부터는 사용자 결정이 필요하다.

1. LSTM을 계속 튜닝할지, 먼저 baseline 분석과 시각화를 볼지
2. `seq_len=24` 외에 `168` 또는 `24+168 seasonal feature`를 추가할지
3. `grid_import_P`, PV, CHP 등 외생 feature를 추가할지
4. weather missing policy를 단순 0-fill+mask에서 보간/forward-fill로 바꿀지
5. metric 우선순위를 MAE 중심으로 둘지, peak 대응을 위해 RMSE도 함께 볼지

현재 결과만 보면, 다음 작업은 곧바로 복잡한 튜닝으로 가기보다 `last_value` 대비 LSTM이 어디서 지는지 시각화하는 것이 안전하다.
