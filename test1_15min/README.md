# Test1 15min P-Max Forecast

This folder is an independent experiment workspace for forecasting the next
15-minute peak power value from `mart.peak_feature_15min`.

## Goal

Forecast `P_max`, defined as `measurement = 'P'` and `max_value` in each
15-minute window.

Logical meters:

- `V.Z81`
- `V.Z82`
- `H2.Z35x`: `H2.Z35` followed by `H2.Z351`
- `H2.Z36x`: `H2.Z36` followed by `H2.Z361`

The H2 replacement meters are kept as separate continuous segments so windows do
not cross the replacement gap.

## Inputs

Default feature set:

- `P_mean`, `P_max`, `P_std`
- `U1_mean`
- `PF_mean`
- `Ta_mean`
- `Igm_mean`
- cyclic hour, weekday, month features
- `is_weekend`

Weather features are joined from `WeatherStation.Weather` by `window_ts`.

## Outputs

Run:

```bash
python test1_15min/run_mart_pmax_model_comparison.py --device gpu
```

Artifacts are written under `comparison_outputs_gpu/` or
`comparison_outputs/`, depending on the selected device. Each meter/method folder
contains CSV metrics, prediction CSVs, PNG plots, and an HTML report.

The root output folder includes summary CSV files and a summary HTML report.
