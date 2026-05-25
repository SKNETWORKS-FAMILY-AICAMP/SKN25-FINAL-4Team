# Multi-horizon A-clean 데이터 감사

## 감사 범위
- Target file: `/home/viowlet/Projects/SKN25-FINAL-4Team/outputs/modeling/a_clean_targets_1h/target_timeseries_1h.parquet`
- Feature file: `/home/viowlet/Projects/SKN25-FINAL-4Team/outputs/modeling/a_clean_targets_1h/feature_timeseries_1h.parquet`
- Model script: `/home/viowlet/Projects/SKN25-FINAL-4Team/scripts/modeling/train_a_clean_huang2022_multi_horizon.py`
- Horizon: 1h, 24h, 168h
- 대상: 중앙 냉방, 국소 냉방, 서버 전원, 환기 계통

## 원천 frame 점검
- target shape: `[210332, 21]`
- feature shape: `[52583, 14]`
- target duplicate key rows: `0`
- feature duplicate ts rows: `0`
- feature split counts: `{'test': 8759, 'train': 35064, 'validation': 8760}`
- gateway outage rows: `3456`
- gateway outage periods: `[{'start': '2020-02-13 00:00:00+00:00', 'end': '2020-03-05 23:00:00+00:00', 'rows': 528}, {'start': '2020-08-20 00:00:00+00:00', 'end': '2020-09-16 23:00:00+00:00', 'rows': 672}, {'start': '2021-11-15 00:00:00+00:00', 'end': '2021-12-09 23:00:00+00:00', 'rows': 600}, {'start': '2022-05-06 00:00:00+00:00', 'end': '2022-07-13 23:00:00+00:00', 'rows': 1656}]`

## 누수/정렬 검사 결과
- target_ts offset failures: `0`
- split target_ts mismatch usable rows: `0`
- gateway target_ts mismatch usable rows: `0`
- target value lookup mismatch rows: `0`
- lag mismatch rows: `{'target_lag_1': 0, 'target_lag_2': 0, 'target_lag_24': 0}`

## 확인된 주의 사항
- `medium` LSTM 시간 피처 anchor가 tabular 모델과 다름: SVR/XGBoost use target-time hour features, while LSTM uses origin-time hour features. This is not target-value leakage, but it is an inconsistent model-family comparison for 24h/168h.
- `medium` LSTM 학습 진단 로그 부족: Current artifacts do not persist epoch loss, best epoch, or train RMSE, so LSTM underfit/overfit cannot be diagnosed from saved run alone.
- `medium` baseline 부족: 장기 horizon 해석에는 origin persistence / seasonal persistence baseline을 후보군에 명시적으로 포함해야 함.

## 2023년 보고 구간 origin persistence baseline 대조
| horizon_hours | label | selected_model_rmse | origin_persistence_rmse | selected_model_minus_origin_rmse |
| --- | --- | --- | --- | --- |
| 1 | 중앙 냉방 | 4974.1 | 5846.0 | -871.9 |
| 1 | 국소 냉방 | 1922.5 | 2078.6 | -156.1 |
| 1 | 서버 전원 | 1782.6 | 2042.5 | -259.9 |
| 1 | 환기 계통 | 3957.8 | 6089.4 | -2131.7 |
| 24 | 중앙 냉방 | 9357.4 | 10138.7 | -781.3 |
| 24 | 국소 냉방 | 3588.3 | 3890.5 | -302.2 |
| 24 | 서버 전원 | 3916.0 | 4153.1 | -237.0 |
| 24 | 환기 계통 | 11902.9 | 14121.4 | -2218.5 |
| 168 | 중앙 냉방 | 12053.4 | 12964.5 | -911.2 |
| 168 | 국소 냉방 | 5681.6 | 6663.0 | -981.3 |
| 168 | 서버 전원 | 7309.7 | 7579.3 | -269.5 |
| 168 | 환기 계통 | 8235.7 | 8505.9 | -270.2 |

## 산출물
- JSON: `/home/viowlet/Projects/SKN25-FINAL-4Team/outputs/modeling/a_clean_huang2022_multi_horizon/audit/multi_horizon_data_audit.json`
- frame audit CSV: `/home/viowlet/Projects/SKN25-FINAL-4Team/reports/a_clean_huang2022_benchmark/tables/multi_horizon_frame_audit.csv`
- baseline CSV: `/home/viowlet/Projects/SKN25-FINAL-4Team/reports/a_clean_huang2022_benchmark/tables/multi_horizon_origin_persistence_baseline.csv`