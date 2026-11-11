# VeriDiplo app image: api + worker (+ scripts/tests). CPU only.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/home/app/.cache/huggingface \
    TOKENIZERS_PARALLELISM=false

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
        ocrmypdf ghostscript poppler-utils \
        libgl1 libglib2.0-0 fonts-dejavu-core \
        make \
    && apt-get clean

ARG APP_UID=1000
RUN useradd --create-home --uid ${APP_UID} --shell /bin/bash app \
    && mkdir -p /app /home/app/.cache/huggingface \
    && chown -R app:app /app /home/app

WORKDIR /app

# Heavy layers first (cached): CPU-only torch, then ML deps, then the rest.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements-ml.txt /tmp/requirements-ml.txt
RUN pip install -r /tmp/requirements-ml.txt
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

COPY --chown=app:app . /app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
