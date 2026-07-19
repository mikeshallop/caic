# cAIc — FastAPI application
# Multi-stage build for smaller production image

# ── Stage 1: build ──────────────────────────────────────────
FROM python:3.13-slim-bookworm AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: runtime ────────────────────────────────────────
FROM python:3.13-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app
COPY . .

# Persist DB and uploads outside the code layer
ENV CAIC_DB_PATH=/app/data/caic.db \
    CAIC_UPLOAD_DIR=/app/data/uploads \
    CAIC_HOST=0.0.0.0 \
    CAIC_PORT=8080 \
    CAIC_SYSLOG_ADDRESS=""

RUN mkdir -p /app/data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fs http://localhost:8080/ || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
