# Screenshots (2026-10-08)

| # | What it shows | Rubric area |
|---|---|---|
| [01](01-ecs-live-api-docs.png) | FastAPI docs served by the ECS Fargate task | Deployment |
| [02](02-ecs-live-health.png) | `GET /health` on the ECS task → `{"status":"ok"}` | Inference service |
| [03](03-ecs-live-metrics.png) | `GET /metrics` on the ECS task (Prometheus exposition) | Monitoring |
| [04](04-github-actions-pipeline.png) | GitHub Actions run: lint → test + deepchecks → build/push to ECR → approved `production` deploy | CI/CD |
| [05](05-ecs-live-deployment-evidence.png) | Real CLI output: ECS service ACTIVE 1/1 with circuit breaker, task RUNNING/HEALTHY on the ECR image, `curl` against the public IP, CloudWatch JSON logs (account ID masked) | Deployment, logging |
| [06](06-mlflow-registered-model-production-alias.png) | MLflow registry: `finbert` v4 `@production`, with promotion and rollback lineage tags | Model registry |
| [07](07-mlflow-evaluation-run.png) | Evaluation run: 6 params, accuracy / f1_weighted / precision_weighted / recall_weighted, model registered | Model evaluation |
| [08](08-mlflow-experiment-runs.png) | Experiment `finbert-evaluation`: evaluation runs (v1–v4) and monitoring-stream runs | MLflow tracking |
| [09](09-mlflow-production-monitoring-metrics.png) | `monitoring/stream.py` against the **ECS** API: pct_positive/negative/neutral, avg_confidence, avg_latency_ms per window (444 requests, 0 failures) | Production monitoring |
| [10](10-grafana-dashboard.png) | Grafana dashboard (docker compose) under Locust load: rate by sentiment, p50/p95/p99 latency, sentiment share, errors | Monitoring (extra) |
| [11](11-prometheus-target-up.png) | Prometheus target `finbert-api` (`api:8000/metrics`) UP, 15 s scrape | Monitoring |

After the screenshots the ECS service was scaled to 0 (`infra/status_aws.sh scale 0`).
