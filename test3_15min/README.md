# Test3 15min P-Max Peak-Weighted Feature Expansion

This folder is an independent experiment workspace based on `test2_15min`.
It keeps the expanded lag, rolling, trend, and business-hour features, then adds
sample weighting so high `P_max` training windows matter more during model fit.

## Goal

Compare this experiment against:

- `test1_15min`: base feature set
- `test2_15min`: expanded feature set without peak sample weights

The controlled setup is:

- model method: `v29`
- input window: `24h`
- prediction horizon: `15min`
- logical meters: `V.Z81`, `V.Z82`, `H2.Z35x`, `H2.Z36x`

## Peak Weight Policy

Training samples are weighted by target `P_max` level:

- normal samples: `1.0`
- target at or above train-set 90th percentile: `2.0`
- target at or above train-set 95th percentile: `3.0`

The intent is to check whether peak-focused weighting improves `top5_peak_f1`,
`top5_peak_recall`, or `daily_peak_time_mae_minutes`.

`v16` is excluded in this experiment because peak-weighted fitting with the
expanded feature matrix was too slow, and `v16` was not selected by `best_single`
in `test1_15min`.

## Inputs

Base and expanded features are the same as `test2_15min`:

- `P_mean`, `P_max`, `P_std`
- `U1_mean`, `PF_mean`
- `Ta_mean`, `Igm_mean`
- cyclic time features and weekend/business-time flags
- P lags: `P_max_lag_1`, `P_max_lag_2`, `P_max_lag_4`, `P_max_lag_8`, `P_max_lag_96`, `P_max_lag_192`
- rolling P features
- P/U1/PF trend features

## Outputs

Run:

```bash
python test3_15min/run_mart_pmax_peak_weighted.py --device gpu
```

Artifacts are written under `peak_weighted_outputs_gpu/` or
`peak_weighted_outputs/`, depending on the selected device.
