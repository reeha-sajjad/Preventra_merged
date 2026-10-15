# syntax=docker/dockerfile:1
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall pandas and shap.
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# Only what the service reads at runtime. The 53 MB of parquet next to the
# model bundle is training output and is excluded by .dockerignore.
COPY api/ ./api/
COPY models/ ./models/
# The ingest API and its worker (python -m ingestion.worker, the same image
# with another start command - see railway.worker.json) build the 94 inputs
# and the weekly rows here.
COPY features/ ./features/
COPY ingestion/ ./ingestion/
# Three files, not two. model_card_phase1.json backs /api/model/metrics, which
# the whole Analytics page depends on - and its handler raises 503 rather than
# inventing figures when the card is absent, so omitting it takes the page down.
COPY data/mimic/model/results/phase1_model.joblib \
     data/mimic/model/results/mimic_feature_spec.json \
     data/mimic/model/results/model_card_phase1.json \
     ./data/mimic/model/results/
COPY docs/mimic/band_thresholds_mimic.json ./docs/mimic/

# /api/pipeline/upload writes here.
RUN mkdir -p data/input

# Most platforms inject the port to bind. Default to 8000 for `docker run`.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
