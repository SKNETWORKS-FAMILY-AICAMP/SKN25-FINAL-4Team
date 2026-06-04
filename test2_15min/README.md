# Test2 15min P-Max Feature Expansion

This folder is an independent experiment workspace copied from `test1_15min`.
It tests whether explicit lag, rolling, trend, and business-hour features improve
next-15-minute `P_max` forecasting from `mart.peak_feature_15min`.

## Goal

Compare this experiment against `test1_15min` for the same setup:

- model method: `v29`
- input window: `24h`
- prediction horizon: `15min`
- logical meters: `V.Z81`, `V.Z82`, `H2.Z35x`, `H2.Z36x`

`H2.Z35x` is `H2.Z35` followed by `H2.Z351`, and `H2.Z36x` is `H2.Z36`
followed by `H2.Z361`. Replacement meter segments stay separate so windows do
not cross replacement gaps.

## Inputs

Base features inherited from `test1_15min`:

- `P_mean`, `P_max`, `P_std`
- `U1_mean`
- `PF_mean`
- `Ta_mean`, `Igm_mean`
- cyclic hour, weekday, month features
- `is_weekend`

Added in this experiment:

- business-time flags: `is_business_hour`, `is_morning_ramp`, `is_lunch_time`, `is_evening`
- P lags: `P_max_lag_1`, `P_max_lag_2`, `P_max_lag_4`, `P_max_lag_8`, `P_max_lag_96`, `P_max_lag_192`
- rolling P features: `P_max_roll_1h_mean`, `P_max_roll_1h_max`, `P_max_roll_1h_std`, `P_max_roll_3h_mean`, `P_max_roll_3h_max`, `P_max_roll_6h_mean`, `P_max_roll_24h_max`
- trend features: `P_max_diff_1`, `P_max_diff_4`, `P_mean_diff_1`, `U1_mean_diff_1`, `PF_mean_diff_1`

Weather features are joined from `WeatherStation.Weather` by `window_ts`.

## Outputs

Run:

```bash
python test2_15min/run_mart_pmax_feature_expansion.py --device gpu
```

Artifacts are written under `feature_expansion_outputs_gpu/` or
`feature_expansion_outputs/`, depending on the selected device. Each meter/method
folder contains CSV metrics, prediction CSVs, PNG plots, and an HTML report.

The root output folder includes summary CSV files and a summary HTML report.
