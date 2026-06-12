# Import P-Max Deployment

## Structure

```text
src/forecasting/import_pmax/
  training.py
  inference.py
  batch_inference.py
  validation.py
  promotion.py
  operations.py

scripts/forecasting/
  train_import_pmax.py
  predict_import_pmax.py
  predict_all_import_pmax.py
  promote_import_pmax.py
  rollback_import_pmax.py

artifacts/import_pmax_v29_60min/
  input_24h/predict_60min/{meter}/

artifacts/import_pmax_candidates/{run_id}/
artifacts/import_pmax_archives/{timestamp}_{previous_run_id}/
```

The `test*_15min` directories remain experiment archives. Production commands
and imports do not depend on those directories.

## Production Inference

Run all four logical meters with one command:

```bash
python scripts/forecasting/predict_all_import_pmax.py \
  --as-of 2026-06-06T10:45:00Z
```

The command performs the complete inference pipeline:

1. load the deployed artifacts
2. query all four logical meters
3. preprocess and validate each 24-hour input window
4. run the four-model ensemble for each meter
5. produce four forecast rows per meter
6. write one aggregate JSON and one 16-row CSV

Default outputs:

```text
outputs/import_pmax/import_pmax_{as_of}.json
outputs/import_pmax/import_pmax_{as_of}.csv
outputs/import_pmax/import_pmax_forecast_history.csv
```

The timestamped CSV is the immutable 16-row snapshot for one inference run.
`import_pmax_forecast_history.csv` is the cumulative DB-like validation file.
Every batch upserts rows using `(logical_meter, base_ts, target_ts)`, matching
the recommended database unique key. A new `base_ts` adds 16 rows; rerunning
the same `base_ts` replaces those 16 rows without creating duplicates.

Use `--history-csv PATH` to choose another cumulative file or
`--no-history-csv` to disable cumulative CSV storage.

Airflow should call this single command after the 15-minute source aggregation
is complete. DB INSERT or UPSERT can consume the resulting 16-row CSV or call
the same Python batch API.

For debugging one logical meter:

```bash
python scripts/forecasting/predict_import_pmax.py --meter V.Z81
```

Missing-data policy:

- linearly interpolate one internal gap of up to 60 minutes
- allow no more than four imputed rows in the 24-hour input window
- forward-fill only one missing latest bucket and mark the JSON result as `degraded`
- reject two or more missing latest buckets
- never replace missing input with the previous day's value

## Retraining

```bash
python -m scripts.forecasting.train_import_pmax \
  --device gpu \
  --run-id <run_id>
```

This single command queries, preprocesses, trains, validates, tests, and saves
artifacts for all four logical meters. Preprocessing and model saving are not
separate commands.

The training configuration is fixed to:

- 24-hour input
- four-step 60-minute output
- two LightGBM models, one XGBoost model, and one CatBoost model
- validation-RMSE-minimizing weighted ensemble
- 22 features without weather inputs

Training writes new artifacts to
`artifacts/import_pmax_candidates/{run_id}/`. Each training run is isolated
from the deployed `artifacts/import_pmax_v29_60min/` path.

Validate the candidate without changing production:

```bash
python -m scripts.forecasting.promote_import_pmax --run-id <run_id>
```

Promote an approved candidate:

```bash
python -m scripts.forecasting.promote_import_pmax \
  --run-id <run_id> \
  --execute \
  --approval-note "approved by model review"
```

Promotion requires a current `validation.json`. Validation verifies all four
manifests, feature order, ensemble weights, model files, and training summary.
It also compares candidate RMSE with active deployment metrics when available.
Promotion copies only runtime files to a staging directory, validates staging,
backs up the current deployed directory under
`artifacts/import_pmax_archives/`, and swaps staging into the deployed path.
It then runs the normal four-meter local inference path without writing output
files. A smoke failure automatically restores the previous deployment and
keeps the candidate. A smoke success records `promotion.json` and removes the
candidate directory.

Rollback:

```bash
python -m scripts.forecasting.rollback_import_pmax \
  --archive-root artifacts/import_pmax_archives/<archive_name> \
  --approval-note "approved rollback"
```

The complete RunPod, FastAPI, Cloudflare, validation, promotion, and rollback
procedure is documented in `IMPORT_PMAX_OPERATIONS.md`.

## Artifact Policy

Deployed runtime path:

```text
artifacts/import_pmax_v29_60min/
```

Required runtime files per meter:

```text
_candidate_models/*.joblib
v29/manifest.json
v29/ensemble_weights.csv
```

Candidate and legacy folders are local validation or rollback assets and are
excluded from Git. A successfully promoted candidate is deleted after the
inference smoke passes. Evaluation reports, plots, and saved test predictions
are excluded from the deployed runtime artifact.

The final 22-feature candidate was validated for all four meters and promoted
to the deployed runtime path on June 6, 2026. The previous runtime artifact is
preserved locally at:

```text
artifacts/import_pmax_v29_60min_legacy_pre_22f_20260606/
```
