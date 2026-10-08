"""
Integration tests for the Sentiment Analysis API.

The session-scoped `client` fixture (tests/conftest.py) loads the real model
once, so these tests exercise the full request -> model -> response path.
"""

import pytest
from fastapi.testclient import TestClient

from app import main

VALID_SENTIMENTS = {"positive", "negative", "neutral"}
RESULT_FIELDS = {"text", "sentiment", "confidence", "latency_ms"}

HEADLINES = [
    "The company reported record profits and raised its dividend.",
    "The firm filed for bankruptcy after massive losses.",
    "Stocks closed flat on Friday amid low trading volume.",
]


def assert_valid_result(result: dict, expected_text: str) -> None:
    assert set(result) == RESULT_FIELDS
    assert result["text"] == expected_text
    assert result["sentiment"] in VALID_SENTIMENTS
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["latency_ms"] > 0


@pytest.mark.parametrize("text", HEADLINES)
def test_predict_returns_valid_response(client: TestClient, text):
    response = client.post("/predict", json={"text": text})

    assert response.status_code == 200
    assert_valid_result(response.json(), text)


def test_predict_batch(client: TestClient):
    response = client.post("/predict/batch", json={"texts": HEADLINES})

    assert response.status_code == 200
    results = response.json()
    assert isinstance(results, list)
    assert len(results) == len(HEADLINES)
    for result, text in zip(results, HEADLINES, strict=True):
        assert_valid_result(result, text)


def test_predict_batch_empty_list_returns_422(client: TestClient):
    response = client.post("/predict/batch", json={"texts": []})

    assert response.status_code == 422


def test_health_returns_ok(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("text", ["", "   "])
def test_predict_empty_text_returns_422(client: TestClient, text):
    response = client.post("/predict", json={"text": text})

    assert response.status_code == 422


def test_predict_batch_with_blank_item_returns_422(client: TestClient):
    response = client.post("/predict/batch", json={"texts": ["Shares rose.", " "]})

    assert response.status_code == 422


def test_predict_batch_over_limit_returns_422(client: TestClient):
    texts = ["Shares rose."] * (main.MAX_BATCH_SIZE + 1)

    response = client.post("/predict/batch", json={"texts": texts})

    assert response.status_code == 422


def test_predict_missing_field_returns_422(client: TestClient):
    response = client.post("/predict", json={})

    assert response.status_code == 422


def test_metrics_exposes_prediction_metrics(client: TestClient):
    client.post("/predict", json={"text": HEADLINES[0]})

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "prediction_requests_total" in body
    assert "prediction_latency_ms_bucket" in body
    assert "prediction_errors_total" in body


def test_endpoints_return_503_when_model_unloaded(client: TestClient, monkeypatch):
    monkeypatch.setattr(main, "classifiers", {})

    assert client.get("/health").status_code == 503
    assert client.post("/predict", json={"text": HEADLINES[0]}).status_code == 503
    batch = client.post("/predict/batch", json={"texts": HEADLINES})
    assert batch.status_code == 503


def test_prediction_error_increments_error_counter(client: TestClient, monkeypatch):
    def broken_model(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setitem(main.classifiers, "sentiment", broken_model)
    before = main.PREDICTION_ERRORS._value.get()

    with pytest.raises(RuntimeError):
        client.post("/predict", json={"text": HEADLINES[0]})

    assert main.PREDICTION_ERRORS._value.get() == before + 1


def test_predict_overlong_text_returns_422(client: TestClient):
    response = client.post("/predict", json={"text": "a" * (main.MAX_INPUT_CHARS + 1)})

    assert response.status_code == 422


def test_check_labels_rejects_unpatched_model():
    class Config:
        id2label = {0: "LABEL_0", 1: "LABEL_1", 2: "LABEL_2"}

    class Model:
        config = Config()

    class Classifier:
        model = Model()

    with pytest.raises(ValueError):
        main.check_labels(Classifier())
