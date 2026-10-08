"""
Sentiment Prediction API.

Endpoints:
    GET  /health          — service health status
    POST /predict         — single headline sentiment
    POST /predict/batch   — batch headline sentiment
    GET  /metrics         — Prometheus metrics
"""

import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from utils import load_classifier

# Structured JSON logs: the formatter only prints the message, which log()
# already serialises as a JSON object (one object per line).
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("finbert-api")

# Inputs longer than this are truncated by the 512-token model limit (warned).
MAX_TEXT_CHARS = 2000
# Hard limit: longer inputs are rejected (422) — tokenising multi-MB strings
# would pin the single vCPU before truncation even happens.
MAX_INPUT_CHARS = 10_000
# Upper bound on a single batch request to protect the CPU-only service.
MAX_BATCH_SIZE = 64
MODEL_MAX_TOKENS = 512
EXPECTED_LABELS = {"positive", "negative", "neutral"}

# HF pipelines are not guaranteed thread-safe and FastAPI runs sync endpoints
# in a thread pool; on a 1-vCPU task serialising inference is also faster
# than oversubscribing the CPU with concurrent torch calls.
_inference_lock = threading.Lock()


def log(level: str, message: str, **kwargs) -> None:
    """Emit one structured log entry as a JSON object."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level.upper(),
        "message": message,
        **kwargs,
    }
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        json.dumps(entry, default=str),
    )


PREDICTION_REQUESTS = Counter(
    "prediction_requests_total",
    "Total number of predictions served, labelled by predicted sentiment.",
    ["sentiment"],
)
PREDICTION_LATENCY = Histogram(
    "prediction_latency_ms",
    "Model inference latency in milliseconds.",
    buckets=(10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
)
PREDICTION_ERRORS = Counter(
    "prediction_errors_total",
    "Total number of failed prediction calls.",
)

load_dotenv()

classifiers = {}


InputText = Annotated[str, Field(max_length=MAX_INPUT_CHARS)]


class PredictRequest(BaseModel):
    text: InputText


class PredictBatchRequest(BaseModel):
    texts: Annotated[list[InputText], Field(max_length=MAX_BATCH_SIZE)]


class PredictionResult(BaseModel):
    text: str
    sentiment: str
    confidence: float
    latency_ms: float


@asynccontextmanager
async def lifespan(app: FastAPI):
    log("INFO", "Loading model...", model_source=os.getenv("MODEL_SOURCE", "mlflow"))
    try:
        classifier = load_classifier()
        check_labels(classifier)
        classifiers["sentiment"] = classifier
    except Exception as e:
        # Keep the process up so /health reports 503 instead of crash-looping.
        log("ERROR", "Model failed to load", error=str(e))
    else:
        log("INFO", "Model loaded successfully")
    yield
    classifiers.clear()
    log("INFO", "Model unloaded")


app = FastAPI(title="Sentiment Analysis API", lifespan=lifespan)


def check_labels(classifier) -> None:
    """Refuse a model whose id2label isn't the patched sentiment labels.

    An unpatched FinBERT config yields LABEL_0/1/2, which would otherwise be
    served with HTTP 200 and silently break every downstream metric.
    """
    labels = {str(v).lower() for v in classifier.model.config.id2label.values()}
    if labels != EXPECTED_LABELS:
        raise ValueError(
            f"Unexpected model labels {sorted(labels)}; expected "
            f"{sorted(EXPECTED_LABELS)}. Run scripts/smoke_test.py to patch the config."
        )


def run_prediction(text: str | list[str]) -> tuple[list[dict], float]:
    """Run the model on a text (or a list of texts) and measure latency.

    Returns the raw model outputs (one dict with "label" and "score" per
    input text) and the wall-clock latency of the call in milliseconds.
    """
    texts = [text] if isinstance(text, str) else text
    with _inference_lock:
        start = time.perf_counter()
        outputs = classifiers["sentiment"](
            texts, truncation=True, max_length=MODEL_MAX_TOKENS
        )
        latency_ms = (time.perf_counter() - start) * 1000
    return outputs, latency_ms


def run_predictions(texts: list[str]) -> list[PredictionResult]:
    try:
        too_long = [i for i, t in enumerate(texts) if len(t) > MAX_TEXT_CHARS]
        if too_long:
            log(
                "WARNING",
                "Input text exceeds max length and will be truncated",
                max_chars=MAX_TEXT_CHARS,
                indices=too_long,
            )

        outputs, latency_ms = run_prediction(texts)
        PREDICTION_LATENCY.observe(latency_ms)

        results = [
            PredictionResult(
                text=text,
                sentiment=output["label"],
                confidence=round(float(output["score"]), 6),
                latency_ms=round(latency_ms, 3),
            )
            for text, output in zip(texts, outputs, strict=True)
        ]

        for result in results:
            PREDICTION_REQUESTS.labels(sentiment=result.sentiment).inc()
            log(
                "INFO",
                "prediction",
                sentiment=result.sentiment,
                confidence=result.confidence,
                latency_ms=result.latency_ms,
                text_chars=len(result.text),
            )
        return results
    except Exception as e:
        PREDICTION_ERRORS.inc()
        log("ERROR", "Prediction failed", error=str(e), batch_size=len(texts))
        raise


def ensure_model_loaded() -> None:
    if "sentiment" not in classifiers:
        raise HTTPException(status_code=503, detail="Model not loaded")


def validate_text(text: str) -> None:
    if not text or not text.strip():
        raise HTTPException(status_code=422, detail="Text must not be empty")


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
def health():
    ensure_model_loaded()
    return {"status": "ok"}


@app.post("/predict", response_model=PredictionResult)
def predict(request: PredictRequest):
    ensure_model_loaded()
    validate_text(request.text)
    return run_predictions([request.text])[0]


@app.post("/predict/batch", response_model=list[PredictionResult])
def predict_batch(request: PredictBatchRequest):
    ensure_model_loaded()
    if not request.texts:
        raise HTTPException(status_code=422, detail="Texts list must not be empty")
    for text in request.texts:
        validate_text(text)
    return run_predictions(request.texts)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
    )
