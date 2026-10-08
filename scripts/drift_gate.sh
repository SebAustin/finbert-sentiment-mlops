#!/usr/bin/env bash
# Drift gate with automatic rollback.
#
# Runs the Deepchecks drift checks against the model currently holding the
# MLflow "production" alias. If drift exceeds the params.yaml thresholds,
# the alias is reverted to the previous registered version.
#
# Usage (from the project root, MLflow server running):
#   scripts/drift_gate.sh
set -uo pipefail

cd "$(dirname "$0")/.."

export MODEL_SOURCE=mlflow
export MODEL_STAGE=production

python scripts/run_deepchecks.py
status=$?

if [[ $status -eq 0 ]]; then
  echo "Drift gate passed: production model kept."
  exit 0
fi

echo "Drift gate FAILED (exit $status): rolling back the production alias..."
python scripts/rollback.py --reason "deepchecks drift gate failed ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
exit "$status"
