"""Read-only adoption of an existing case store. Never rewrite signed deliveries or audit events."""

from pathlib import Path
import re
from .workspace_files import read_settings

HISTORICAL_REPOSITORY = "replay/issuepilot-sandbox"
HISTORICAL_WORKSPACE_KEY = "replay_run_id"


def identify_existing_store(state_root: Path, projects: set[str], default_project: str):
    relevant = {
        name
        for name in projects
        if name == default_project or name.startswith("issuepilot-replay-full")
    }
    candidates = []
    for file in sorted(state_root.glob("*/.env.replay")):
        project = file.parent.name
        if not project.startswith("issuepilot-replay-full") or not re.fullmatch(
            r"[a-z0-9_-]+", project
        ):
            continue
        values = read_settings(file.read_bytes())
        if (
            values.get("IP_DATASET_REPLAY") != "true"
            or values.get("IP_REPO") != HISTORICAL_REPOSITORY
            or values.get("IP_WRITES_ENABLED") != "false"
        ):
            raise ValueError(
                "An existing workspace does not match the read-only snapshot configuration"
            )
        candidates.append((file, project, values))
    if len(candidates) > 1 or relevant - {p for _, p, _ in candidates}:
        raise ValueError(
            "Existing case storage does not have a unique matching local configuration. Restore its original workspace credentials before starting; no database was changed."
        )
    if not candidates:
        return None
    file, project, old = candidates[0]
    if project not in relevant:
        raise ValueError(
            "Local credentials exist but their PostgreSQL volume was not found. Check the Docker context or restore the original database before starting"
        )
    # Only copy the settings necessary to preserve durable identity and authentication.
    values = {
        k: old[k]
        for k in (
            "IP_REPO",
            "IP_WEBHOOK_SECRET",
            "IP_REVIEWER_TOKENS",
            "POSTGRES_PASSWORD",
            "N8N_ENCRYPTION_KEY",
        )
    }
    values["IP_WORKSPACE_ID"] = old["IP_REPLAY_RUN_ID"]
    return project, values, file.parent / "projection/issues.jsonl"
