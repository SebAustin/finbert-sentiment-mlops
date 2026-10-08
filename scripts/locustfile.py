"""
Load test for the Sentiment Analysis API.

Run: locust -f scripts/locustfile.py --host http://localhost:8000

Then open http://localhost:8089 to configure and start the test.
Headless: add --headless -u 10 -r 2 -t 60s
"""

import random

from locust import HttpUser, between, task

SAMPLE_HEADLINES = [
    "The company reported record profits and raised its dividend.",
    "The firm filed for bankruptcy after massive losses.",
    "Stocks closed flat on Friday amid low trading volume.",
    "Federal Reserve signals interest rate cuts later this year.",
    "Tech giant announces major layoffs amid revenue decline.",
    "Merger talks between the two firms collapsed overnight.",
    "Quarterly earnings beat analyst expectations by wide margin.",
    "Oil prices surge on supply concerns from the Middle East.",
]
BATCH_SIZE = 4


class SentimentAPIUser(HttpUser):
    # Each simulated user pauses 1–3 s between requests (realistic pacing).
    wait_time = between(1, 3)

    @task(3)
    def predict_single(self):
        self.client.post(
            "/predict",
            json={"text": random.choice(SAMPLE_HEADLINES)},
            name="/predict",
        )

    @task(1)
    def predict_batch(self):
        self.client.post(
            "/predict/batch",
            json={"texts": random.sample(SAMPLE_HEADLINES, BATCH_SIZE)},
            name="/predict/batch",
        )

    @task(1)
    def health_check(self):
        self.client.get("/health", name="/health")
