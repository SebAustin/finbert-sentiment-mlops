# Model rollback on drift

The MLflow alias `models:/finbert@production` decides which model version the API serves
(`MODEL_SOURCE=mlflow`). When the drift gate fails for the model currently in production,
the alias is moved back to the previous registered version.

## Components

| File | Role |
|---|---|
| `scripts/run_deepchecks.py` | Drift gate. Exit **0** = pass, **2** = drift above the `params.yaml` thresholds, **1** = the check itself crashed (missing data, MLflow down, …). |
| `scripts/promote.py` | When it moves `production` to a new version, it records the version it replaces as the tag `previous_production`. It refuses to promote a version tagged `rolled_back_at`. It also refuses a version that did not come from the evaluated run. |
| `scripts/rollback.py` | Moves `production` back to the recorded `previous_production`. If that tag is missing or points to a version that was itself rolled back, it uses the newest older version that was never rolled back. It tags the rolled-back version (`rolled_back_at`, `rollback_reason`) and the restored one (`restored_at`). It is **idempotent**: once production points to a restored version, re-runs do nothing, so the alias never walks further back. Supports `--dry-run`. |
| `scripts/drift_gate.sh` | Runs the gate against `MODEL_SOURCE=mlflow` / `MODEL_STAGE=production`. It calls `rollback.py` **only on exit 2**; any other failure is reported and production is left alone. |
| `.github/workflows/rollback.yml` | The same rollback from GitHub Actions. It can be started manually (`workflow_dispatch`). It also starts automatically when the *"Run Deepchecks drift gate"* step of `test-and-deploy` fails (`workflow_run`, `main` only). Install, DVC or download failures in that job do not trigger it. |

## How it is triggered

1. **Locally / on a scheduler** (primary path, works with the MLflow server on your machine):
   ```bash
   mlflow server --port 5000 --host 0.0.0.0
   scripts/drift_gate.sh
   ```
   Schedule it with cron next to `monitoring/stream.py` to re-check the production model
   against fresh stream data.
2. **Automatically in GitHub Actions**: when the CI `deepchecks` job fails, `model-rollback`
   starts. It checks that the failed job really was `deepchecks`, then rolls back. This needs
   a repository secret `MLFLOW_TRACKING_URI` that points at an MLflow server reachable from
   GitHub runners. Without that secret the job only prints a notice, because a laptop MLflow
   server can't be reached from GitHub.
3. **Manually**: Actions → *model-rollback* → *Run workflow*, with a reason.

The failing CI run never reaches `build`/`deploy` (`needs: [test, deepchecks]`), so a drifting
build is never deployed, and the registry alias is restored to the last good version.

## Scope: registry rollback vs. container rollback

Two deployment paths, each with its own rollback:

| Deployment | What decides the model | Rollback |
|---|---|---|
| `docker compose` / `python app/main.py` with `MODEL_SOURCE=mlflow` | `models:/finbert@production` (read at startup) | `rollback.py` moves the alias, then **restart the API** to load the restored version. |
| ECS Fargate (CI/CD) | The model baked into the image (`MODEL_SOURCE=huggingface`) | The ECS **deployment circuit breaker** (`rollback=true`, set by `infra/setup_aws.sh`) reverts automatically to the last healthy task definition. To roll back manually, redeploy an earlier revision: `aws ecs update-service --cluster finbert-cluster --service finbert-api-service --task-definition finbert-api:<N>`. |

## Verified run (2026-10-08)

Setup: v3 was promoted with `previous_production=2`, and v2 had already been rolled back
earlier. `property_drift_threshold` was temporarily lowered to 0.1.

```
[FAIL] Sentiment property drift 0.4042 exceeds threshold 0.1
Drift gate FAILED (exit 2): rolling back the production alias...
Rolling back 'finbert@production': v3 -> v1 (deepchecks drift gate failed (2026-10-08T18:25:45Z))
'production' now points to v1.

$ python scripts/rollback.py            # re-run: idempotent
'production' is v1, already restored by a previous rollback at 2026-10-08T18:25:48Z. Nothing to do.

$ python scripts/promote.py             # the drifted version cannot be re-promoted
[ERROR] Model NOT promoted: v3 was rolled back for drift (...); register a new version instead.

$ mv data/stream.csv /tmp && scripts/drift_gate.sh   # errors are not drift
Drift check errored (exit 1): NOT rolling back. Fix the error and re-run.
```
