# Import P-Max v29 60-Minute Model

Production model artifacts for four-step import P-max forecasting.

- input: 24 hours, 96 rows at 15-minute resolution
- output: t+15, t+30, t+45, t+60 minutes
- ensemble: v20 LightGBM, v23 LightGBM early stopping, v25 XGBoost, v27 CatBoost
- target: `max(P_max, 0)`

Artifact layout:

`input_24h/predict_60min/{logical_meter}/`

Each meter contains candidate model files under `_candidate_models/` and the
v29 manifest and ensemble weights under `v29/`.
