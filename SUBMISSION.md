# Project Submission: Real-Time Financial News Sentiment Service

**Student repository:** https://github.com/SebAustin/finbert-sentiment-mlops
**Model:** `baptle/FinBERT_market_based` · **Dataset:** `baptle/financial_headlines_market_based`
**Evidence:** [`docs/screenshots/`](docs/screenshots/README.md) (11 screenshots, indexed by rubric area)

## Summary

I built an MLOps stack around a pre-trained FinBERT financial sentiment classifier:

- a DVC data pipeline
- MLflow evaluation, model registry and gated promotion
- a FastAPI inference service with integration and load tests
- a CPU-only Docker image and a docker-compose stack (API, Prometheus, Grafana)
- a GitHub Actions pipeline that tests, runs a Deepchecks drift gate, pushes to Amazon ECR, and deploys to ECS Fargate behind a manual approval
- production monitoring in Prometheus/Grafana and MLflow

The full pipeline ran green on GitHub (run `37824933291`) and deployed the service to ECS Fargate. I tested the live service with `curl` and the monitoring stream (screenshots 01–05, 09). Afterwards I scaled the ECS service to 0 to control cost. `infra/status_aws.sh scale 1` restarts it with a new public IP.

| Result | Value |
|---|---|
| Evaluation on 1,629 test headlines | accuracy **0.638** · f1_weighted **0.590** · precision_weighted 0.611 · recall_weighted 0.638 |
| Promotion gate (`f1_threshold: 0.56`) | passed. `finbert` v4 holds the `production` alias |
| Drift gate (test set vs. 444 cleaned Bluesky posts) | Sentiment property drift **0.404** (threshold 0.5) · prediction drift **0.232** (threshold 0.6) |
| Integration tests | **16 passed** (both the HuggingFace and MLflow model sources) |
| Load test (Locust, 10 users, 30 s) | 0 failures · `/predict` p50 34 ms / p95 64 ms (local CPU) |
| Production monitoring (ECS) | 444 requests, 0 failures, 9 windows logged to MLflow |
| Docker image | 11 GB → **1.8 GB** with CPU-only torch |

## Rubric checklist

### Data Pipeline and Versioning

| Requirement | Where |
|---|---|
| `prepare` stage: runs `scripts/load_data.py`, script as a dependency, params `prepare.test_size` / `prepare.random_seed`, outputs `data/train.csv`, `data/test.csv` | `dvc.yaml:13` |
| `clean` stage: runs `scripts/clean_data.py`, depends on the script and `data/raw_stream.csv`, params `clean.min_words` / `clean.max_words`, output `data/stream.csv` | `dvc.yaml:24` |
| `dvc.lock` committed | `dvc.lock` |

### Model Registry and Evaluation

| Requirement | Where |
|---|---|
| Tracking URI and experiment set, run started, 6 params and the 4 metrics logged, model registered as `finbert` | `scripts/evaluate.py:93` (`log_to_mlflow`), metrics `:108`, registration `:115` · screenshot 07 |
| Reads the F1 threshold from `params.yaml` and `f1_weighted` from the latest evaluation run; sets the `production` alias only if F1 ≥ threshold | `scripts/promote.py:110`, `:37` (`get_latest_f1`), `:101` |
| Prints a message when the model does not qualify | `scripts/promote.py:129` ("Model NOT promoted: …") |
| MLflow UI shows the registered model with a `production` alias | screenshots 06, 08 |

### Inference Service Development

| Requirement | Where |
|---|---|
| `GET /health` returns `{"status":"ok"}`, or 503 when the model is not loaded | `app/main.py:206` |
| `POST /predict` returns `PredictionResult` (text, sentiment, confidence, latency_ms); 422 on empty text, 503 when unloaded | `app/main.py:212` |
| `POST /predict/batch` returns a list; 422 on an empty list, 503 when unloaded | `app/main.py:219` |
| `run_prediction` helper: takes a text, runs the model, returns predictions and latency | `app/main.py:134` |
| ≥3 tests: `/predict` schema, batch length, batch empty → 422 | `tests/test_api.py:32`, `:39`, `:50` (16 tests in total) |
| Locust `HttpUser` with `/predict` (3), `/predict/batch` (1), `/health` (1), and `wait_time` | `scripts/locustfile.py:29-47` |

### Containerization and Deployment Automation

| Requirement | Where |
|---|---|
| `python:3.12-slim`, dependencies from a requirements file, copies `app/`, `EXPOSE 8000`, CMD starts FastAPI | `Dockerfile:11`, `:36` (`requirements-prod.txt`), `:52`, `:54`, `:60` |
| Compose: build context, `8000:8000`, `env_file: .env`, `mlruns/` and `mlartifacts/` volumes, `host.docker.internal:host-gateway`, healthcheck on `/health` | `docker-compose.yml:16-43` |
| Deepchecks: `TextData` for the stream and test sets, NLP property drift on "Sentiment", prediction drift, non-zero exit above the `params.yaml` thresholds | `scripts/run_deepchecks.py:63`, `:75`, `:85`, `:119` |
| CI on push to `main`, plus the provided `lint` job | `.github/workflows/ci-cd.yml` |
| `test` job: `pytest tests/` with `MODEL_SOURCE=huggingface` | `ci-cd.yml:51` |
| `deepchecks` job: `run_deepchecks.py` with `MODEL_SOURCE=huggingface` | `ci-cd.yml:84` |
| `build` job (needs test + deepchecks): AWS credentials from secrets, ECR login, build, push the `<sha7>-<timestamp>` and `latest` tags | `ci-cd.yml:122` |
| `deploy` job (needs build): downloads the task definition, renders the new image, deploys to `finbert-api-service` / `finbert-cluster` with `amazon-ecs-deploy-task-definition` | `ci-cd.yml:175`, `:207` · screenshots 04, 05 |

### Production Monitoring

| Requirement | Where |
|---|---|
| Counter for requests by sentiment, Histogram for latency in ms, Counter for errors | `app/main.py:62`, `:67`, `:72` |
| `run_predictions` increments the request counter, observes the histogram, and increments the error counter on exception | `app/main.py:150` |
| `GET /metrics` using `generate_latest()` and `CONTENT_TYPE_LATEST` | `app/main.py:201` · screenshot 03 |
| `log()` emits JSON with timestamp, level and message; called for model load, predictions and errors | `app/main.py:48` · CloudWatch logs in screenshot 05 |
| `prometheus.yml`: `job_name: "finbert-api"`, `scrape_interval: 15s`, target `api:8000` | `prometheus.yml:9` · screenshot 11 |
| `stream.py`: reads `data/stream.csv`, starts an MLflow run, calls `/predict`, and every `WINDOW_SIZE` logs pct_positive/negative/neutral, avg_confidence and avg_latency_ms with the window index as the step | `monitoring/stream.py:58`, `:72`, `:101` · screenshot 09 |

## Suggestions to make the project stand out: 4 of 5 implemented

| Suggestion | Implementation |
|---|---|
| Grafana dashboard committed as JSON | `monitoring/grafana/dashboards/finbert-api.json`, auto-provisioned by compose. Panels: request rate by sentiment, p50/p95/p99 latency, sentiment share over time, error rate. Screenshot 10. |
| Manual approval before the ECS deploy | The `deploy` job targets the GitHub `production` environment. It requires a reviewer and allows only the `main` branch. Approval is visible in screenshot 04. |
| Model rollback when the drift gate fails | `scripts/rollback.py`, `scripts/drift_gate.sh`, `.github/workflows/rollback.yml`. Drift exits with code 2, which reverts the `production` alias to the recorded previous version. The rollback is idempotent, and errors never trigger it. See `docs/ROLLBACK.md` for the documentation and a verified run. |
| `requirements-prod.txt` with CPU-only torch and `--no-cache-dir`, before/after sizes | 11 GB → 1.8 GB, documented in `docs/IMAGE_SIZE.md`. The baseline Dockerfile is in `docs/Dockerfile.baseline`. |
| A/B testing path to a candidate MLflow version | **Not implemented.** The registry lineage tags and per-request JSON logs would be the starting point. |

Beyond the suggestions, I also added hardening:

- input limits (422)
- serialized inference
- a model-label sanity check
- a non-root container
- an ECS deployment circuit breaker
- idempotent `infra/` setup and teardown scripts
- `SECURITY.md` (STRIDE)

## How to verify

```bash
python scripts/smoke_test.py                         # download and patch the model
dvc repro                                            # Task 1
mlflow server --port 5000 --host 0.0.0.0             # then:
python scripts/evaluate.py && python scripts/promote.py   # Task 2
pytest tests/                                        # Task 3 (16 tests)
locust -f scripts/locustfile.py --host http://localhost:8000
docker compose up --build                            # Task 4: API :8000, Prometheus :9090, Grafana :3000
python scripts/run_deepchecks.py                     # Task 5: exit 0 = pass, 2 = drift
python monitoring/stream.py                          # Task 6 (API running)
```

AWS resources are created by `infra/setup_aws.sh` and removed by `infra/teardown_aws.sh`. See `infra/README.md` for details.

## Notes for the reviewer

- **Dependency pins:** I added `numpy<2` and `transformers<4.46` to `requirements.txt`. scikit-learn 1.3 does not import with NumPy 2, and MLflow 2.16 predates transformers 5. CI installs CPU-only torch first, to avoid about 3 GB of CUDA wheels.
- **Model config patch:** the HuggingFace model ships a broken `id2label` (float labels). CI and the Docker build both run `scripts/smoke_test.py` to patch it. The API also refuses to serve a model whose labels aren't the expected sentiment labels.
- **Model source on ECS vs. compose:** the ECS task serves the model baked into the image (`MODEL_SOURCE=huggingface`), so it starts without an MLflow server. Compose and local runs load `models:/finbert@production` from MLflow.
- **Optimistic evaluation:** the starter's random train/test split is required by the rubric, and the model was fine-tuned on this same dataset, so the 0.59 F1 is optimistic. See "Known limitations" in `README.md`.
- **AWS account:** deployed to us-east-1. The account ID is masked in the screenshots.
- **macOS port conflict:** AirPlay Receiver occupies port 5000 on macOS, so the local `.env` uses `127.0.0.1:5000`.
