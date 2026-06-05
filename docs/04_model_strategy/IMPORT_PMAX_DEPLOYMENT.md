# Import P-Max Deployment

## Structure

```text
src/forecasting/import_pmax/
  training.py
  inference.py

scripts/forecasting/
  train_import_pmax.py
  predict_import_pmax.py

artifacts/import_pmax_v29_60min/
  input_24h/predict_60min/{meter}/
```

The `test*_15min` directories remain experiment archives. Production commands
and imports do not depend on those directories.

## Inference

```bash
python scripts/forecasting/predict_import_pmax.py --meter V.Z81
```

Write JSON output:

```bash
python scripts/forecasting/predict_import_pmax.py \
  --meter H2.Z35x \
  --output outputs/import_pmax_prediction.json
```

The command queries only source meters mapped to the requested logical meter,
validates model metadata and feature order, selects 96 continuous input rows,
and returns four future predictions.

## Training

```bash
python scripts/forecasting/train_import_pmax.py --device gpu
```

The default production experiment is fixed to:

- 24-hour input
- four-step 60-minute output
- v29 ensemble
- v20, v23, v25, v27 candidates

Training writes new artifacts to
`artifacts/import_pmax_v29_60min_candidate/` by default. Validate that directory
before promoting it to the deployed `artifacts/import_pmax_v29_60min/` path.
