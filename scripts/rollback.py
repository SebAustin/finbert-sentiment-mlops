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


def get_production_version(client: MlflowClient, model_name: str):
    """Return the version holding the production alias, or None if unset.

    Any other MLflow error (server down, auth) propagates instead of being
    reported as "no alias".
    """
    try:
        return client.get_model_version_by_alias(model_name, PRODUCTION_ALIAS)
    except MlflowException as e:
        if e.error_code in ("RESOURCE_DOES_NOT_EXIST", "INVALID_PARAMETER_VALUE"):
            return None
        raise


def find_rollback_target(client: MlflowClient, model_name: str, current) -> str | None:
    """Version to restore: the one recorded by promote.py when `current` was
    promoted; otherwise the newest older version that was never rolled back."""
    recorded = (current.tags or {}).get("previous_production")
    if recorded:
        recorded_tags = client.get_model_version(model_name, recorded).tags or {}
        if "rolled_back_at" not in recorded_tags:
            return recorded
    older = [
        v
        for v in client.search_model_versions(f"name='{model_name}'")
        if int(v.version) < int(current.version)
        and "rolled_back_at" not in (v.tags or {})
    ]
    return max(older, key=lambda v: int(v.version)).version if older else None


def rollback(client: MlflowClient, model_name: str, reason: str, dry_run: bool) -> int:
    current = get_production_version(client, model_name)
    if current is None:
        print(f"[ERROR] Model '{model_name}' has no '{PRODUCTION_ALIAS}' alias.")
        return 1

    # Idempotency: a version restored by a previous rollback is not rolled
    # back again by a re-run (that would walk the alias back one more step).
    if "restored_at" in (current.tags or {}):
        print(
            f"'{PRODUCTION_ALIAS}' is v{current.version}, already restored by a "
            f"previous rollback at {current.tags['restored_at']}. Nothing to do."
        )
        return 0

    target = find_rollback_target(client, model_name, current)
    if target is None:
        print(
            f"No earlier production version for '{model_name}': nothing to roll "
            f"back to. '{PRODUCTION_ALIAS}' stays on v{current.version}."
        )
        return 1

    print(
        f"Rolling back '{model_name}@{PRODUCTION_ALIAS}': "
        f"v{current.version} -> v{target} ({reason})"
    )
    if dry_run:
        print("Dry run: alias not changed.")
        return 0

    timestamp = datetime.now(timezone.utc).isoformat()
    client.set_registered_model_alias(model_name, PRODUCTION_ALIAS, target)
    client.set_model_version_tag(
        model_name, current.version, "rolled_back_at", timestamp
    )
    client.set_model_version_tag(model_name, current.version, "rollback_reason", reason)
    client.set_model_version_tag(model_name, target, "restored_at", timestamp)
    print(f"'{PRODUCTION_ALIAS}' now points to v{target}.")
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
