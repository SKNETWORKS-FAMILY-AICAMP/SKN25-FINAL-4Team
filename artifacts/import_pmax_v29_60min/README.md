# Import P-Max Production Artifacts

Runtime artifacts for four logical meters:

- input: 24 hours, 96 rows at 15-minute resolution
- output: four 15-minute forecasts over the next 60 minutes
- features: 22, without weather inputs
- ensemble: two LightGBM models, XGBoost, and CatBoost

Required layout:

```text
input_24h/predict_60min/{logical_meter}/
  _candidate_models/*.joblib
  v29/manifest.json
  v29/ensemble_weights.csv
```

Evaluation reports and saved validation/test predictions are kept in candidate
folders and are not part of the deployed runtime artifact.
