# RunPod Serverless Training Image

This package contains the one-shot RunPod Serverless handler for retraining the
`energy_v84` model pipeline.

## Build

```bash
docker build -f Dockerfile.runpod -t energy-v84-trainer:latest .
```

The default base image is `pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime`.
Override it if the RunPod CUDA environment requires a different PyTorch image:

```bash
docker build \
  --build-arg BASE_IMAGE=pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime \
  -f Dockerfile.runpod \
  -t energy-v84-trainer:latest .
```

## Required Runtime Environment

The Serverless endpoint must provide:

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

`MODEL_ARTIFACT_UPLOAD_URL` should point to the AWS/local API endpoint:

```text
https://<api-host>/model-artifacts/upload
```

`RUNPOD_ALLOWED_UPLOAD_HOSTS` must contain the host part of that URL. This is a
basic SSRF guard.

PyTorch is intentionally supplied by the Docker base image, not
`requirements-runpod.txt`. If the base image is changed to a non-PyTorch image,
add a compatible `torch` package explicitly.

For a short local tunnel smoke test only, `RUNPOD_ALLOW_ANY_UPLOAD_HOST=1` can be
used instead of `RUNPOD_ALLOWED_UPLOAD_HOSTS`. Do not use that setting in
production.

## Job Input Example

```json
{
  "input": {
    "horizon": 3,
    "run_id": "run_20260610T120000Z_manual",
    "meters": ["H1.Z10"],
    "epochs": 1,
    "overwrite_upload": true
  }
}
```

For the full production run, omit `meters` and `epochs`.

## Output

On success, the handler returns `status=uploaded` and includes the uploaded
candidate `run_id`, `horizon`, `meter_count`, and upload API response. Validation
and promotion still happen on the AWS/local API side after the uploaded candidate
is received.
