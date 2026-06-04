# Test4 15min Import P-Max Experiment

This folder is an independent experiment workspace based on `test2_15min`.
It uses the same source table as `test1_15min`, `test2_15min`, and
`test3_15min`: `mart.peak_feature_15min`.

## Goal

Forecast import-side peak power instead of signed `P_max`.

The paper's sign convention is:

- positive `P`: consumption / inflow / import
- negative `P`: production / outflow / export

So this experiment clips negative P values to zero after pivoting the EAV rows:

```text
P_import_mean = max(P_mean, 0)
P_import_max  = max(P_max, 0)
target        = P_import_max
```

The source table is still `mart.peak_feature_15min`; `mart.peak_input_15min` is
not used.

## Controlled Setup

- method: `v29`
- input window: `24h`
- prediction horizon: `15min`
- meters: `V.Z81`, `V.Z82`, `H2.Z35x`, `H2.Z36x`

## Features

This experiment keeps the expanded feature set from `test2_15min`:

- clipped P features: `P_mean`, `P_max`, `P_std`
- `U1_mean`, `PF_mean`
- `Ta_mean`, `Igm_mean`
- cyclic time features and business-time flags
- P lag, rolling, and trend features generated from clipped import P

## Outputs

Run:

```bash
python test4_15min/run_import_pmax_experiment.py --device gpu
```

Artifacts are written under `import_pmax_outputs_gpu/` or
`import_pmax_outputs/`, depending on the selected device.

## Result Summary

Compared with `test2_15min` signed P_max, this experiment removes negative
targets entirely and improves absolute error metrics because the target now
matches import-side demand.

Test4 import P_max WAPE:

| meter | WAPE | persistence WAPE | WAPE improvement |
|---|---:|---:|---:|
| H2.Z35x | 7.93% | 8.85% | 10.47% |
| H2.Z36x | 7.86% | 8.71% | 9.75% |
| V.Z81 | 33.48% | 33.81% | 0.98% |
| V.Z82 | 28.75% | 29.11% | 1.26% |

H2 meters become more stable under the import target. V meters still have high
WAPE, but import clipping removes negative target ambiguity and reduces absolute
MAE/RMSE versus the signed target setup.

The signed-vs-import row comparison is stored in
`import_vs_signed_comparison.csv`.
