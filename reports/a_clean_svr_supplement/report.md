# A-clean SVR 보완 실험 리포트

## 1. 배경

사용자 확인에 따라 논문 비교군에 SVR이 포함되었을 가능성을 재점검했다. Crossref metadata 기준 `Energy Forecasting in a Public Building: A Benchmarking Analysis on Long Short-Term Memory (LSTM), Support Vector Regression (SVR), and Extreme Gradient Boosting (XGBoost) Networks` 논문은 제목에 SVR을 명시한다. 기존 A-clean champion run에는 SVR 후보가 없었으므로 보완 실험을 별도 산출물로 추가했다.

## 2. 실행 범위

```text
input_dataset: outputs/modeling/a_clean_targets_1h/
output: outputs/modeling/a_clean_svr_supplement_1h/
script: scripts/modeling/train_a_clean_svr_supplement_1h.py
selection_metric: validation_non_gateway_mae
```

대상 target은 기존 A-clean 4개와 동일하다.

## 3. SVR 후보

- `linear_svr_C1_eps0.1`: 전체 eligible train row 사용
- `rbf_svr_C10_eps0.1_train5000`: exact RBF SVR 비용 때문에 deterministic train subset 5,000행 사용

RBF SVR은 전체 33,264개 train row에 대해 exact kernel을 적용하면 계산 비용이 커서 보완 비교용 sampled run으로 표시했다.

## 4. 결과 비교

| target_id | 기존 champion | champion test MAE | best SVR | SVR test MAE | SVR - champion MAE | 해석 |
|---|---|---:|---|---:|---:|---|
| `T1_group__central_cooling__P` | `hgb_iter_320_lr_0.06` | 2769.05 | `linear_svr_C1_eps0.1` | 3602.03 | 832.98 | SVR 열세 |
| `T1_group__local_cooling__P` | `ridge_robust_alpha_0.1` | 902.50 | `linear_svr_C1_eps0.1` | 972.04 | 69.54 | SVR 열세 |
| `T1_group__server_power__P` | `ridge_alpha_1000` | 1162.13 | `rbf_svr_C10_eps0.1_train5000` | 21284.62 | 20122.49 | SVR 열세 |
| `T1_group__ventilation__P` | `extra_trees_n_360_depth_none_leaf_3` | 1908.54 | `linear_svr_C1_eps0.1` | 4013.00 | 2104.47 | SVR 열세 |

## 5. 결론

SVR 계열은 이번 보완 실험에서 기존 champion을 넘지 못했다. 다만 기존 보고서의 “논문 자료 반영 방식”에는 SVR 누락 사실을 반영해야 한다. 논문 재현 표현은 `SVR 포함 benchmark 논문을 완전히 재현`이 아니라 `SVR을 보완 비교군으로 추가 점검`으로 쓰는 것이 안전하다.

## 6. 산출물

```text
outputs/modeling/a_clean_svr_supplement_1h/svr_summary.csv
outputs/modeling/a_clean_svr_supplement_1h/svr_vs_champion_summary.csv
outputs/modeling/a_clean_svr_supplement_1h/run_manifest.json
outputs/modeling/a_clean_svr_supplement_1h/<target_id>/svr_candidate_metrics.csv
outputs/modeling/a_clean_svr_supplement_1h/<target_id>/svr_manifest.json
outputs/modeling/a_clean_svr_supplement_1h/<target_id>/svr_best_predictions.parquet
outputs/modeling/a_clean_svr_supplement_1h/<target_id>/svr_best_model.joblib
```

