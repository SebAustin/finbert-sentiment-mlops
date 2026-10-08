# Production image for the FinBERT sentiment API.
#
# Build:  docker build -t finbert-api .
# Run:    docker run -p 8000:8000 finbert-api            (model baked in)
#
# Size notes (see docs/IMAGE_SIZE.md):
#   * CPU-only torch from requirements-prod.txt instead of the CUDA wheel.
#   * pip --no-cache-dir, no build toolchain.
#   * Files are created by the runtime user up front: a later `chown -R`
#     would copy the 440 MB model into a second layer.
FROM python:3.12-slim

ARG HF_MODEL_ID=baptle/FinBERT_market_based

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/.hf \
    HF_MODEL_ID=${HF_MODEL_ID} \
    MODEL_SOURCE=huggingface \
    API_HOST=0.0.0.0 \
    API_PORT=8000

RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /app/mlruns /app/mlartifacts /app/.hf \
 && chown -R appuser:appuser /app
WORKDIR /app

# Dependencies first so code changes don't invalidate this layer.
COPY requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

USER appuser

# Bake the model into the image (no download on container start) and patch
# its broken id2label config, reusing the project's smoke-test helper.
COPY --chown=appuser:appuser scripts/smoke_test.py ./scripts/smoke_test.py
RUN python -c "from huggingface_hub import snapshot_download; \
snapshot_download('${HF_MODEL_ID}', allow_patterns=['*.json', '*.safetensors', '*.txt'])" \
 && python -c "import sys; sys.path.insert(0, 'scripts'); \
from smoke_test import download_and_patch_model; download_and_patch_model('${HF_MODEL_ID}')" \
 && python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('${HF_MODEL_ID}')"

# Everything the model needs is now in the image: never call the Hub at runtime.
ENV HF_HUB_OFFLINE=1

COPY --chown=appuser:appuser app/ ./app/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"API_PORT\", \"8000\")}/health', timeout=4)" || exit 1

# API_PORT defaults to 8000 (docker-compose); the ECS task sets API_PORT=80.
CMD ["sh", "-c", "exec uvicorn main:app --app-dir app --host ${API_HOST} --port ${API_PORT}"]
