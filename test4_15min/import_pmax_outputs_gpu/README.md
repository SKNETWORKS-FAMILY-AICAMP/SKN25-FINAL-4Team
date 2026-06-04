# P-max Model Comparison Outputs

This output folder contains model comparison artifacts for forecasting
`max(measurement='P'.max_value, 0)` from `mart.peak_feature_15min`.

Root files:

- `pmax_model_comparison_summary.csv`: all completed method rows.
- `pmax_model_comparison_best_rmse.csv`: best method by test RMSE for each meter/window/horizon.
- `pmax_model_comparison_best_persistence_improvement.csv`: best method by RMSE improvement over persistence.
- `pmax_model_comparison_summary.html`: readable summary report.

Nested folders follow:

`input_{hours}h/predict_{range}/{meter}/{method}/`
