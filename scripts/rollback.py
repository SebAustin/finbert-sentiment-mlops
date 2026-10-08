"""
Roll the "production" alias back to the previous registered model version.

Used when the drift gate (scripts/run_deepchecks.py) fails for the model
currently serving production — see scripts/drift_gate.sh and docs/ROLLBACK.md.

Run:
    python scripts/rollback.py --reason "drift gate failed"
    python scripts/rollback.py --dry-run
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

load_dotenv()

PRODUCTION_ALIAS = "production"


def find_previous_version(
    client: MlflowClient, model_name: str, current: int
) -> int | None:
    """Return the highest registered version lower than `current`, if any."""
    versions = [
        int(v.version) for v in client.search_model_versions(f"name='{model_name}'")
    ]
    older = [v for v in versions if v < current]
    return max(older) if older else None


def rollback(client: MlflowClient, model_name: str, reason: str, dry_run: bool) -> int:
    try:
        current = int(
            client.get_model_version_by_alias(model_name, PRODUCTION_ALIAS).version
        )
    except MlflowException:
        print(
            f"[ERROR] Model '{model_name}' has no '{PRODUCTION_ALIAS}' alias to roll back."
        )
        return 1

    previous = find_previous_version(client, model_name, current)
    if previous is None:
        print(
            f"No version older than v{current} for '{model_name}': nothing to roll back to. "
            f"'{PRODUCTION_ALIAS}' stays on v{current}."
        )
        return 1

    print(
        f"Rolling back '{model_name}@{PRODUCTION_ALIAS}': v{current} -> v{previous} ({reason})"
    )
    if dry_run:
        print("Dry run: alias not changed.")
        return 0

    client.set_registered_model_alias(model_name, PRODUCTION_ALIAS, str(previous))
    timestamp = datetime.now(timezone.utc).isoformat()
    client.set_model_version_tag(model_name, str(current), "rolled_back_at", timestamp)
    client.set_model_version_tag(model_name, str(current), "rollback_reason", reason)
    client.set_model_version_tag(model_name, str(previous), "restored_at", timestamp)
    print(f"'{PRODUCTION_ALIAS}' now points to v{previous}.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument(
        "--reason", default="manual rollback", help="Recorded as a version tag."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change."
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
    model_name = os.getenv("MODEL_NAME") or "finbert"
    client = MlflowClient(tracking_uri=tracking_uri)
    sys.exit(rollback(client, model_name, args.reason, args.dry_run))


if __name__ == "__main__":
    main()
