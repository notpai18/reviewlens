# Multi-stage Dockerfile for ReviewLens

# -------------------------------------------------------------
# Stage 1: Build virtual environment
# -------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir .

# -------------------------------------------------------------
# Stage 2: Runtime image
# -------------------------------------------------------------
FROM python:3.12-slim AS runner

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m -s /bin/bash appuser

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/app/src:$PYTHONPATH"
ENV FASTEMBED_CACHE_PATH="/app/.fastembed_cache"
ENV APP_ENV="prod"
ENV PORT=8080

# Copy application source code and data assets
COPY src/ /app/src/
COPY data/processed/meta.json /app/data/processed/meta.json
COPY data/warehouse/reviewlens.duckdb /app/data/warehouse/reviewlens.duckdb

# Pre-download fastembed models so runtime containers start fast
RUN mkdir -p /app/.fastembed_cache && \
    python -c "from fastembed import TextEmbedding, SparseTextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/.fastembed_cache'); SparseTextEmbedding('Qdrant/bm25', cache_dir='/app/.fastembed_cache')" && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["sh", "-c", "uvicorn reviewlens.api.main:app --host 0.0.0.0 --port ${PORT}"]
