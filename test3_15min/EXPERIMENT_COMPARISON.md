# 24h/15min v29 Experiment Comparison

This comparison uses the same controlled setup across experiments:

- method: `v29`
- input window: `24h`
- prediction horizon: `15min`
- meters: `V.Z81`, `V.Z82`, `H2.Z35x`, `H2.Z36x`

## Experiments

- `test1_base`: base feature set from `test1_15min`
- `test2_features`: added lag, rolling, trend, and business-hour features
- `test3_peak_weighted`: same expanded features as test2, with peak sample weights and `v16` excluded

## Mean Results

| experiment | RMSE | MAE | RMSE improvement | MAE improvement | top5 F1 | daily peak time MAE |
|---|---:|---:|---:|---:|---:|---:|
| test1_base | 18364.92 | 11752.69 | 11.68% | 6.43% | 0.7807 | 165.06 min |
| test2_features | 18205.49 | 11682.86 | 12.30% | 7.26% | 0.7771 | 156.72 min |
| test3_peak_weighted | 18261.45 | 11758.43 | 12.11% | 6.77% | 0.7794 | 151.16 min |

## Interpretation

`test2_features` is the best value-forecasting setup by mean RMSE and MAE. It
improves RMSE on all four meters versus `test1_base`, but its top5 peak F1 drops
slightly.

`test3_peak_weighted` improves average daily peak timing the most and recovers
some top5 F1 relative to test2, but it does not beat test2 on RMSE or MAE.

For a single production-oriented choice, use `test2_features` with `v29` when
the main target is 15-minute `P_max` value accuracy. Keep `test3_peak_weighted`
as a follow-up candidate only if peak-time alerting becomes more important than
overall value accuracy.

The row-level comparison is stored in `experiment_comparison_24h_15min.csv`.
