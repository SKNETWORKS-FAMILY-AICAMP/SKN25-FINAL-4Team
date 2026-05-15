FROM python:3.13-slim

WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성
COPY requirements.backend.txt .
RUN pip install --no-cache-dir -r requirements.backend.txt

# 소스 복사
COPY src/ ./src/

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
