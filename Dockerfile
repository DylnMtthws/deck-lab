FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.version="0.1.0"

ARG SABER_BUILD_SHA=unknown
ARG SABER_STRATEGY_SEMANTIC=0
ARG SABER_STRATEGY_MODEL_REVISION=d13954661f83248295ba75c1ed411eef3b7b936e
ENV SABER_BUILD_SHA=$SABER_BUILD_SHA
LABEL org.opencontainers.image.revision=$SABER_BUILD_SHA

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    SABER_BIND_HOST=0.0.0.0 \
    SABER_PORT=8080 \
    SABER_DB_PATH=/data/sabermetrics.db

RUN groupadd --gid 1001 app && \
    useradd --uid 1001 --gid 1001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY scripts/cache_strategy_model.py ./scripts/cache_strategy_model.py
RUN if [ "$SABER_STRATEGY_SEMANTIC" = "1" ]; then \
      pip install --no-cache-dir ".[postgres,strategy]" && \
      python scripts/cache_strategy_model.py /app/strategy-model "$SABER_STRATEGY_MODEL_REVISION"; \
    else pip install --no-cache-dir ".[postgres]"; fi
ENV DECK_LAB_STRATEGY_ONNX_PATH=/app/strategy-model

COPY config ./config
COPY --chown=1001:1001 fixtures ./fixtures
COPY scripts/setup_db.py ./scripts/setup_db.py
COPY scripts/ingest_strategy_corpus.py ./scripts/ingest_strategy_corpus.py
COPY scripts/seed_strategy_corpus.py ./scripts/seed_strategy_corpus.py
COPY scripts/import_combo_catalog.py ./scripts/import_combo_catalog.py

RUN mkdir -p /data && chown 1001:1001 /data

USER 1001:1001

EXPOSE 8080

CMD ["sabermetrics", "serve"]
