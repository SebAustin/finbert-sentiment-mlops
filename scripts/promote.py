"""
Promote the latest version of the registered model to Production
if the weighted F1 score from the most recent evaluation run meets
the required threshold.

Steps:
1- read the F1 threshold from params.yaml,
2- fetch the weighted F1 score from the most recent MLflow evaluation run,
3- assign the "production" alias to the latest model version if the score
   meets (>=) the threshold,
4- print a clear message when the model does not qualify.

Run:
    python scripts/promote.py
"""

import os
import sys

import mlflow
import yaml
from dotenv import load_dotenv
from mlflow.tracking import MlflowClient

load_dotenv()

PRODUCTION_ALIAS = "production"
F1_METRIC = "f1_weighted"


def load_params() -> dict:
    with open("params.yaml") as f:
        return yaml.safe_load(f)["promote"]


def get_latest_f1(client: MlflowClient, experiment_name: str) -> tuple[str, float]:
    """
    Return the run_id and f1_weighted of the most recent evaluation run.
    """
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        raise ValueError(
            f"Experiment '{experiment_name}' not found. "
            "Run `python scripts/evaluate.py` first."
        )

    # Only consider runs that actually logged the F1 metric (i.e. evaluation
    # runs), so monitoring runs in the same experiment are ignored.
    runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        filter_string=f"metrics.{F1_METRIC} >= 0",
        order_by=["attributes.start_time DESC"],
        max_results=1,
    )
    if not runs:
        raise ValueError(
            f"No run with metric '{F1_METRIC}' in experiment '{experiment_name}'."
        )

    latest = runs[0]
    return latest.info.run_id, latest.data.metrics[F1_METRIC]


def promote(client: MlflowClient, model_name: str) -> str:
    """Assign the production alias to the latest registered model version."""
    versions = client.search_model_versions(f"name='{model_name}'")
    if not versions:
        raise ValueError(f"No registered versions found for model '{model_name}'.")

    # get the latest version of the model
    latest = max(versions, key=lambda v: int(v.version))
    client.set_registered_model_alias(model_name, PRODUCTION_ALIAS, latest.version)
    print(
        f"Promoted model '{model_name}' version {latest.version} "
        f"to alias '{PRODUCTION_ALIAS}'."
    )
    return latest.version


def main():
    threshold = float(load_params()["f1_threshold"])
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
    experiment_name = os.getenv("MLFLOW_EXPERIMENT_NAME") or "finbert-evaluation"
    model_name = os.getenv("MODEL_NAME") or "finbert"

    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)

    try:
        run_id, f1 = get_latest_f1(client, experiment_name)
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    print(f"Latest evaluation run: {run_id}")
    print(f"{F1_METRIC} = {f1:.4f} (threshold = {threshold:.4f})")

    if f1 < threshold:
        print(
            f"Model NOT promoted: {F1_METRIC} {f1:.4f} is below the "
            f"threshold {threshold:.4f}. The current '{PRODUCTION_ALIAS}' "
            "alias is left unchanged."
        )
        return

    version = promote(client, model_name)
    client.set_model_version_tag(model_name, version, F1_METRIC, f"{f1:.4f}")
    client.set_model_version_tag(model_name, version, "promoted_from_run", run_id)


if __name__ == "__main__":
    main()
