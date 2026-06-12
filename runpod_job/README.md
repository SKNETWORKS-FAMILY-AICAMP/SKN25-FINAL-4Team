# Import P-Max RunPod Serverless Worker

This worker runs only model training. Inference, candidate validation, backup,
and promotion remain on the local backend.

Flow:

```text
local FastAPI /training/start
  -> RunPod Serverless endpoint
  -> scripts.forecasting.train_import_pmax
  -> candidate tar.gz
  -> local FastAPI /model-artifacts/upload
```

Required RunPod endpoint environment variables:

```text
DB_HOST
DB_PORT
DB_NAME
DB_USER
DB_PASS
MODEL_ARTIFACT_UPLOAD_URL
ARTIFACT_UPLOAD_TOKEN
RUNPOD_ALLOWED_UPLOAD_HOSTS
```

Build and push:

```bash
docker build -f Dockerfile.runpod -t <dockerhub-user>/import-pmax-trainer:latest .
docker push <dockerhub-user>/import-pmax-trainer:latest
```

Configure the RunPod Serverless endpoint to use that image. For a complete
candidate, omit `meters`; partial meter lists are intended only for connection
tests and cannot pass local promotion validation.
