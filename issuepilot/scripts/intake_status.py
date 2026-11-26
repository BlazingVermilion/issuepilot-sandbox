"""Count received issues, persisted model stages and human decisions separately."""

import json
from collections import Counter
from sqlalchemy import distinct, func, select
from issuepilot import issue_source
from issuepilot.config import Settings
from issuepilot.db import Case, Event, database


def intake_status(settings, sessions):
    expected = len(issue_source.load_snapshots(settings.snapshot_path))
    with sessions() as db:
        rows = db.execute(
            select(
                Case.status,
                Case.proposal["telemetry"]["model"],
                Case.proposal["telemetry"]["stages"],
            ).where(Case.repo == settings.repo)
        ).all()
        reviewed = db.scalar(
            select(func.count(distinct(Event.case_id)))
            .join(Case, Case.id == Event.case_id)
            .where(Case.repo == settings.repo, Event.kind.in_(["APPROVAL", "REJECTION"]))
        )
    return {
        "dataset": issue_source.DATASET,
        "revision": issue_source.REVISION,
        "workspace_id": str(settings.workspace_id),
        "expected": expected,
        "received": len(rows),
        "not_received": expected - len(rows),
        "latest_model_classification": sum(
            model == settings.model and "classification" in (stages or [])
            for _, model, stages in rows
        ),
        "latest_model_rag": sum(
            model == settings.model and "rag" in (stages or []) for _, model, stages in rows
        ),
        "distinct_cases_reviewed": reviewed,
        "states": dict(Counter(status for status, _, _ in rows)),
    }


def main():
    settings = Settings()
    if not settings.snapshot_intake:
        raise SystemExit("Start the snapshot workspace first")
    engine, sessions = database(settings.database_url)
    try:
        print(json.dumps(intake_status(settings, sessions), indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
