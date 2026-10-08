# Security notes

This is a course demo: a **public, unauthenticated** sentiment API on ECS Fargate (port 80 open
to `0.0.0.0/0`, as the course requires). This page records the threat model and what is
mitigated versus accepted. It is based on the review of 2026-10-08.

## Threat model (STRIDE)

| Threat | Status |
|---|---|
| **Spoofing**: no auth on the API | Accepted (public demo). Put the API behind an ALB with auth or WAF for real use. |
| **Tampering**: CI/CD | Workflows use `permissions: contents: read`. Untrusted values reach `run:` only through `env:`. Deploys go through the `production` environment approval. `rollback.yml` runs for `main` only. |
| **Repudiation** | JSON logs (timestamp, level, message, fields) kept 7 days in CloudWatch. Model lineage is kept as MLflow version tags (`promoted_from_run`, `previous_production`, `rolled_back_at`). |
| **Information disclosure** | Request text is never logged (only `text_chars`). Error responses carry no stack traces. `/metrics` and `/docs` are public (low sensitivity, accepted). |
| **Denial of service** | Mitigated: pydantic rejects texts over 10,000 chars and batches over 64 (HTTP 422). Inference is serialized on the 1-vCPU task. Not mitigated: no rate limiting (accepted for the demo). |
| **Elevation of privilege** | The container runs as uid 10001. Port 80 is bound through the `net.ipv4.ip_unprivileged_port_start` sysctl, not root. There is no task role, so the container holds no AWS credentials. The image runs offline (`HF_HUB_OFFLINE=1`). |

## Supply chain

- `torch` is installed alone from `https://download.pytorch.org/whl/cpu` with `--index-url`. Everything else comes from PyPI only, so there is no `--extra-index-url` dependency confusion.
- ECR scan-on-push is enabled, but it covers OS packages only. Run `pip-audit -r requirements-prod.txt` for the Python packages. As of 2026-10-08 the pinned torch 2.5.1, transformers 4.45 and starlette 0.37 have published advisories. Reachability is limited: only `*.safetensors` weights are loaded (no `torch.load` on untrusted input) and there are no multipart endpoints. Upgrading (`fastapi>=0.115`, `transformers>=4.53,<5`, newer torch) is the next step and needs the mlflow 2.16 compatibility re-tested.
- GitHub Actions are pinned to major tags of first-party `actions/*` and `aws-actions/*` (accepted). `ruff` is pinned in CI.

## Secrets

- `.env` is git-ignored and excluded from the Docker build context. gitleaks over the full history was clean.
- AWS credentials live only in GitHub repository secrets. Prefer a dedicated IAM user scoped to ECR push plus ECS deploy over personal admin keys, or GitHub OIDC where the account allows it.
- `ECR_REGISTRY` is a secret per the course instructions. GitHub drops job outputs that contain a secret, so the build job passes only the image **tag** to the deploy job, and the URI is rebuilt there. This is a security control failing in a way that doesn't look like security: the symptom is an empty image tag, not a 403.

## Local stack

Prometheus and Grafana bind to `127.0.0.1` only. Grafana allows anonymous viewing, and its admin password comes from `GRAFANA_ADMIN_PASSWORD`. The course's MLflow command (`--host 0.0.0.0 --allowed-hosts "*"`) exposes an unauthenticated server to your network. Use it only on trusted networks.
