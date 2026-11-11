# VeriDiplo app image: api + worker (+ scripts/tests). CPU only.
#
# Targets:
#   core : OS tools + light python deps (api up quickly; no torch / OCR python libs)
#   full : core + CPU torch + sentence-transformers + OCR/vision libs (default)
# BASE_IMAGE can point at any python:3.11 image that already has tesseract(fra,eng),
# ghostscript and poppler (the apt step is skipped when tesseract is present).
ARG BASE_IMAGE=python:3.11-slim

FROM ${BASE_IMAGE} AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/home/app/.cache/huggingface \
    TOKENIZERS_PARALLELISM=false

# ocrmypdf comes from pip (the apt package drags in a second python3 + mesa/llvm, ~400MB).
# libgl1 is not needed by opencv-python-headless; libglib2.0-0 is.
RUN if ! command -v tesseract >/dev/null 2>&1; then \
        echo 'Acquire::Retries "10";' > /etc/apt/apt.conf.d/80retries \
        && apt-get update && apt-get install -y --no-install-recommends \
            tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
            ghostscript poppler-utils libglib2.0-0 fonts-dejavu-core make \
        && apt-get clean; \
    fi

ARG APP_UID=1000
RUN id app >/dev/null 2>&1 || useradd --create-home --uid ${APP_UID} --shell /bin/bash app; \
    mkdir -p /app /home/app/.cache/huggingface \
    && chown -R app:app /app /home/app

WORKDIR /app

# ---- core ----
FROM base AS core
COPY requirements-core.txt /tmp/requirements-core.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r /tmp/requirements-core.txt
# Extra layer: numpy/pymupdf/reportlab/openpyxl/pytesseract (small; needed for ingestion + PDF report)
COPY requirements-extra.txt /tmp/requirements-extra.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r /tmp/requirements-extra.txt
COPY --chown=app:app . /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---- full ----
FROM base AS full
COPY requirements-core.txt /tmp/requirements-core.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r /tmp/requirements-core.txt
# Heavy layers: CPU-only torch, then ML deps, then the rest.
RUN --mount=type=cache,target=/root/.cache/pip pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements-ml.txt /tmp/requirements-ml.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r /tmp/requirements-ml.txt
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r /tmp/requirements.txt
COPY --chown=app:app . /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
