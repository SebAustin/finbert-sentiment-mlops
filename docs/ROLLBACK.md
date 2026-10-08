# Model rollback on drift

The MLflow alias `models:/finbert@production` decides which model version the API serves
(`MODEL_SOURCE=mlflow`). When the drift gate fails for the model currently in production,
the alias is moved back to the previous registered version.

## Components

| File | Role |
|---|---|
| `scripts/run_deepchecks.py` | Drift gate: exits 1 when Sentiment property drift or prediction drift is above the `params.yaml` thresholds. |
| `scripts/rollback.py` | Moves `production` to the highest version below the current one. It tags the rolled-back version with `rolled_back_at` / `rollback_reason` and the restored one with `restored_at`. Supports `--dry-run`. It exits 1 if there is nothing to roll back to. |
| `scripts/drift_gate.sh` | Runs the gate against `MODEL_SOURCE=mlflow` / `MODEL_STAGE=production` and calls `rollback.py` on failure. |
| `.github/workflows/rollback.yml` | The same rollback from GitHub Actions. It can be started manually (`workflow_dispatch`) or runs automatically when `test-and-deploy` fails *because its `deepchecks` job failed* (`workflow_run`). |

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

## Verified run (2026-10-08)

With two registered versions (v2 = production) and `property_drift_threshold` temporarily
lowered to 0.1:

```
Sentiment property drift: 0.4042 (threshold 0.1)
[FAIL] Sentiment property drift 0.4042 exceeds threshold 0.1
Drift gate FAILED (exit 1): rolling back the production alias...
Rolling back 'finbert@production': v2 -> v1 (deepchecks drift gate failed (2026-10-08T18:09:17Z))
'production' now points to v1.
```
