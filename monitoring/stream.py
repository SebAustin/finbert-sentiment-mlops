"""
Production monitoring stream for FinBERT sentiment API.

Reads headlines from data/stream.csv, sends them to the /predict endpoint,
and logs aggregated metrics to MLflow every WINDOW_SIZE predictions.

Each observation window logs:
    - Sentiment distribution (% positive, % negative, % neutral)
    - Average confidence score
    - Average latency (ms)

Run from the project root:
    python monitoring/stream.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mlflow
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

# API_URL overrides the host/port pair, e.g. to stream against the ECS task.
API_URL = os.getenv("API_URL") or (
    f"http://{os.getenv('API_HOST', 'localhost')}:{os.getenv('API_PORT', '8000')}"
)
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = os.getenv("MLFLOW_EXPERIMENT_NAME", "finbert-evaluation")
WINDOW_SIZE = 50  # number of predictions per observation window
SLEEP_MS = 100    # delay between requests to simulate real traffic (ms)
STREAM_PATH = os.path.join("data", "stream.csv")


def predict(text: str) -> dict:
    response = requests.post(
        f"{API_URL}/predict",
        json={"text": text},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()

SENTIMENTS = ("positive", "negative", "neutral")


def log_window(window: list[dict], window_idx: int) -> None:
    """Log aggregated metrics for one observation window to MLflow."""
    if not window:
        return

    n = len(window)
    metrics = {
        f"pct_{sentiment}": 100.0 * sum(r["sentiment"] == sentiment for r in window) / n
        for sentiment in SENTIMENTS
    }
    metrics["avg_confidence"] = sum(r["confidence"] for r in window) / n
    metrics["avg_latency_ms"] = sum(r["latency_ms"] for r in window) / n
    metrics["window_size"] = n

    mlflow.log_metrics(metrics, step=window_idx)
    print(
        f"[window {window_idx}] n={n} "
        f"pos={metrics['pct_positive']:.1f}% "
        f"neg={metrics['pct_negative']:.1f}% "
        f"neu={metrics['pct_neutral']:.1f}% "
        f"conf={metrics['avg_confidence']:.3f} "
        f"latency={metrics['avg_latency_ms']:.1f}ms"
    )


def load_headlines(path: str = STREAM_PATH) -> list[str]:
    df = pd.read_csv(path)
    return [t for t in df["text"].dropna().astype(str) if t.strip()]


def main():
    headlines = load_headlines()
    print(f"Loaded {len(headlines)} headlines from {STREAM_PATH}")
    print(f"Streaming to {API_URL} (window size {WINDOW_SIZE})")

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

    window: list[dict] = []
    window_idx = 0
    n_errors = 0

    with mlflow.start_run(run_name="monitoring-stream"):
        mlflow.set_tag("stage", "monitoring")
        mlflow.log_params(
            {
                "api_url": API_URL,
                "window_size": WINDOW_SIZE,
                "sleep_ms": SLEEP_MS,
                "n_headlines": len(headlines),
            }
        )

        for text in headlines:
            try:
                window.append(predict(text))
            except requests.RequestException as e:
                n_errors += 1
                print(f"[WARN] request failed ({n_errors} so far): {e}")

            if len(window) == WINDOW_SIZE:
                log_window(window, window_idx)
                window, window_idx = [], window_idx + 1

            time.sleep(SLEEP_MS / 1000)

        # Flush the last, partially filled window.
        if window:
            log_window(window, window_idx)
            window_idx += 1

        mlflow.log_metrics({"n_requests_failed": n_errors, "n_windows": window_idx})

    print(f"Done: {window_idx} windows logged, {n_errors} failed requests.")


if __name__ == "__main__":
    main()
