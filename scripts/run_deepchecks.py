"""
Model quality check (drift) using deepchecks

Run:
    python scripts/run_deepchecks.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import yaml
from deepchecks.nlp import TextData
from deepchecks.nlp.checks import PredictionDrift, PropertyDrift
from dotenv import load_dotenv

from app.utils import load_classifier

load_dotenv()


def load_params() -> dict:
    with open("params.yaml") as f:
        return yaml.safe_load(f)["deepchecks"]


MODEL_CLASSES = ["negative", "neutral", "positive"]
DRIFT_PROPERTIES = ["Sentiment", "Subjectivity", "Text Length"]


def run_predictions(classifier, texts: list[str]) -> list[str]:
    results = classifier(texts, batch_size=32, truncation=True, max_length=512)
    return [r["label"] for r in results]


def main():
    params = load_params()
    property_drift_threshold = params["property_drift_threshold"]
    prediction_drift_threshold = params["prediction_drift_threshold"]

    print("Loading production model...")

    classifier = load_classifier()

    stream_df = pd.read_csv("data/stream.csv")
    test_df = pd.read_csv("data/test.csv")

    stream_texts = stream_df["text"].tolist()
    test_texts = test_df["text"].tolist()

    # Reference = labelled test set; current = cleaned production stream.
    test_dataset = TextData(
        raw_text=test_texts, task_type="text_classification", name="test"
    )
    stream_dataset = TextData(
        raw_text=stream_texts, task_type="text_classification", name="stream"
    )
    # Only cheap, local properties: "Sentiment" (TextBlob) drives the gate;
    # the others are reported for context.
    for dataset in (test_dataset, stream_dataset):
        dataset.calculate_builtin_properties(include_properties=DRIFT_PROPERTIES)

    # --- NLP property drift (Sentiment) ----------------------------------
    property_result = PropertyDrift().run(
        train_dataset=test_dataset, test_dataset=stream_dataset, with_display=False
    )
    for name, value in property_result.value.items():
        print(f"Property drift — {name}: {value['Drift score']:.4f}")
    sentiment_drift = property_result.value["Sentiment"]["Drift score"]

    # --- Prediction drift ------------------------------------------------
    test_predictions = run_predictions(classifier, test_texts)
    stream_predictions = run_predictions(classifier, stream_texts)
    prediction_result = PredictionDrift().run(
        train_dataset=test_dataset,
        test_dataset=stream_dataset,
        train_predictions=test_predictions,
        test_predictions=stream_predictions,
        model_classes=MODEL_CLASSES,
        with_display=False,
    )
    prediction_drift = prediction_result.value["Drift score"]

    print(
        f"\nSentiment property drift: {sentiment_drift:.4f} "
        f"(threshold {property_drift_threshold})"
    )
    print(
        f"Prediction drift:         {prediction_drift:.4f} "
        f"(threshold {prediction_drift_threshold})"
    )

    failures = []
    if sentiment_drift > property_drift_threshold:
        failures.append(
            f"Sentiment property drift {sentiment_drift:.4f} exceeds "
            f"threshold {property_drift_threshold}"
        )
    if prediction_drift > prediction_drift_threshold:
        failures.append(
            f"Prediction drift {prediction_drift:.4f} exceeds "
            f"threshold {prediction_drift_threshold}"
        )

    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        sys.exit(1)

    print("[PASS] Drift checks within thresholds.")


if __name__ == "__main__":
    main()
