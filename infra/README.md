# FinBERT API on AWS (ECR + ECS Fargate)

Runbook for the scripts in this directory.

| File | Purpose |
|------|---------|
| `setup_aws.sh` | Idempotent bootstrap: ECR, security group, log group, role lookup, cluster, task definition, service (desired count 0) |
| `task-definition.json` | Task definition template (placeholders are substituted by `setup_aws.sh`) |
| `status_aws.sh` | Read-only status + public IP; `scale 0\|1` to stop/start |
| `teardown_aws.sh` | Deletes everything except the IAM role |

## Prerequisites

- AWS CLI v2 and `python3` on your PATH; `gh` CLI (authenticated) for secrets.
- Udacity Cloud Lab credentials exported in your shell, **including the session token**:

  ```bash
  export AWS_ACCESS_KEY_ID=...
  export AWS_SECRET_ACCESS_KEY=...
  export AWS_SESSION_TOKEN=...
  export AWS_REGION=us-east-1
  ```

- A default VPC in the region (the scripts use its subnets).

## Order of operations

1. `infra/setup_aws.sh` - creates/reuses all resources. The service is created with desired count 0.
2. Run the five `gh secret set` commands printed at the end (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION`, `ECR_REGISTRY`). Paste the credential values yourself.
3. Push to `main`. CI builds and pushes the image to ECR.
4. Approve the `production` environment in GitHub Actions; the deploy job scales the service to 1.
5. `infra/status_aws.sh` - shows counts, task definition revision and the task public IP.
6. `curl http://<ip>/health`
7. When idle: `infra/status_aws.sh scale 0`. When finished with the project: `infra/teardown_aws.sh` (add `--yes` to skip the prompt).

If setup fails creating `ecsTaskExecutionRole` (AccessDenied in Cloud Lab), re-run with
`EXECUTION_ROLE_ARN=arn:aws:iam::<account>:role/<existing-role> infra/setup_aws.sh`.

## Cost

- Fargate 1 vCPU / 3 GB is roughly **$0.05 per hour** in us-east-1 (about $36/month if left running 24/7), plus a public IPv4 address charge of about $0.005/hour.
- ECR storage is about $0.10 per GB-month; the lifecycle policy keeps only the last 5 images.
- CloudWatch logs retention is 7 days.
- The Udacity budget is **$25**: scale to 0 whenever you are not testing, and run teardown when done.

## Design notes

- **Port 80.** Fargate with `awsvpc` requires `hostPort == containerPort`, and the security group `finbert-sg` only opens port 80. The image reads `API_PORT`, so the task definition sets `API_PORT=80`.
- **Non-root on port 80.** The image runs as a non-root user (uid 10001), which normally cannot bind ports below 1024. The container sets `systemControls` `net.ipv4.ip_unprivileged_port_start=0`; Fargate supports `net.*` namespaced sysctls with `awsvpc` (platform 1.4.0+).
- **Single task deploys.** The service uses `minimumHealthyPercent=0, maximumPercent=100` so a deploy never runs two 3 GB tasks at once (brief downtime during deploys is accepted).
- **Health check.** Container health check start period and the service grace period are both 180 s to allow for model load.
- **Tags.** Resources created by the scripts are tagged `Project=finbert-mlops`.

## Credentials expire

Cloud Lab credentials are temporary. Each new lab session you must re-export them locally and refresh the
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` GitHub secrets before CI/CD can deploy.

## Notes from the first real run (2026-10-08)

- **Default VPC without subnets:** in some older accounts the default VPC has no subnets. `setup_aws.sh` then creates free default subnets in `<region>a` and `<region>b`. They are public and routed through the default VPC's internet gateway, so the task can pull from ECR.
- **Deployment circuit breaker:** the service runs with `deploymentCircuitBreaker={enable=true,rollback=true}`. A revision that never becomes healthy is rolled back to the last healthy one automatically. Re-running `setup_aws.sh` also applies this to an existing service.
- **Re-running setup** registers a new task-definition revision from `infra/task-definition.json`. The CI deploy job always starts from the latest registered revision and only swaps the image, so re-run setup after editing that file.
- **Manual rollback** to an earlier revision:
  `aws ecs update-service --cluster finbert-cluster --service finbert-api-service --task-definition finbert-api:<N>`
