# syntax=docker/dockerfile:1
FROM python:3.13-slim

WORKDIR /app

# 시스템 의존성 (BuildKit 캐시 마운트로 재빌드 시 다운로드 스킵)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev curl

# Python 의존성 (BuildKit 캐시 마운트로 재빌드 시 다운로드 스킵)
COPY requirements.backend.txt .
# torch CPU-only 먼저 설치 (GPU 없는 환경: ~200MB vs CUDA 포함 ~2GB)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch --index-url https://download.pytorch.org/whl/cpu
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.backend.txt

# 소스 복사
COPY src/ ./src/

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
