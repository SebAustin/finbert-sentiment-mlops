"""
Evaluate sentiment model on the test set and register it
in the MLflow Model Registry.

Run:
    python scripts/evaluate.py
"""

import os

import mlflow
import mlflow.transformers
import pandas as pd
from dotenv import load_dotenv
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    f1_score,
    precision_score,
    recall_score,
)
from transformers import pipeline

load_dotenv()

BATCH_SIZE = 32
MAX_LENGTH = 512


def load_test_data(test_path: str) -> tuple[list[str], list[str]]:
    df = pd.read_csv(test_path)
    return df["text"].tolist(), df["label"].tolist()


def build_classifier(model_id: str):
    print(f"Loading {model_id}...")
    return pipeline(
        "text-classification",
        model=model_id,
        tokenizer=model_id,
        truncation=True,
        max_length=MAX_LENGTH,
    )


def run_inference(classifier, texts: list[str]) -> list[str]:
    print(f"Running inference on {len(texts)} samples...")
    results = classifier(texts, batch_size=BATCH_SIZE)
    return [r["label"] for r in results]


def compute_metrics(y_true: list[str], y_pred: list[str]) -> dict:
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "f1_weighted": f1_score(y_true, y_pred, average="weighted"),
        "precision_weighted": precision_score(y_true, y_pred, average="weighted"),
        "recall_weighted": recall_score(y_true, y_pred, average="weighted"),
    }


def main():
    model_id = os.getenv("HF_MODEL_ID", "baptle/FinBERT_market_based")
    test_path = os.path.join("data", "test.csv")

    texts, y_true = load_test_data(test_path)
    classifier = build_classifier(model_id)
    y_pred = run_inference(classifier, texts)
    metrics = compute_metrics(y_true, y_pred)

    print("\nEvaluation Results:")
    for k, v in metrics.items():
        print(f"  {k}: {v:.4f}")
    report = classification_report(y_true, y_pred)
    print("\nClassification Report:")
    print(report)

    log_to_mlflow(classifier, model_id, test_path, len(texts), metrics, report)


def log_to_mlflow(
    classifier,
    model_id: str,
    test_path: str,
    n_samples: int,
    metrics: dict,
    report: str,
) -> None:
    """Track the evaluation run and register the pipeline in the Model Registry."""
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
    experiment_name = os.getenv("MLFLOW_EXPERIMENT_NAME") or "finbert-evaluation"
    model_name = os.getenv("MODEL_NAME") or "finbert"

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name)

    with mlflow.start_run(run_name=f"evaluate-{model_name}") as run:
        mlflow.set_tag("stage", "evaluation")
        mlflow.log_params(
            {
                "model_id": model_id,
                "task": "text-classification",
                "batch_size": BATCH_SIZE,
                "max_length": MAX_LENGTH,
                "test_path": test_path,
                "n_test_samples": n_samples,
            }
        )
        mlflow.log_metrics(metrics)
        mlflow.log_text(report, "classification_report.txt")

        model_info = mlflow.transformers.log_model(
            transformers_model=classifier,
            artifact_path="model",
            task="text-classification",
            registered_model_name=model_name,
        )
        print(f"\nMLflow run: {run.info.run_id}")
        print(
            f"Registered model '{model_name}' "
            f"version {model_info.registered_model_version}"
        )


if __name__ == "__main__":
    main()
