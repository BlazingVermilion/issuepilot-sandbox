"""Submit signed dataset events via n8n; the ordinary worker performs inference separately."""

import argparse
from collections import Counter
import json

import httpx
from sqlalchemy import distinct, func, select
from issuepilot import replay
from issuepilot.config import Settings
from issuepilot.db import Case, Delivery, Event, database

URL = "http://n8n:5678/webhook/issuepilot-github"


def send(settings, sessions, *, limit, probe=False, client):
    rows = replay.snapshots(settings.replay_path)
    # Pilot and full use identical IDs/order: first test, then validation, then train.
    order = {"test": 0, "validation": 1, "train": 2}
    numbers = sorted(rows, key=lambda number: order[rows[number]["split"]])[:limit]
    with sessions() as db:
        prior = dict(
            db.execute(
                select(Delivery.id, Delivery.case_id)
                .join(Case, Case.id == Delivery.case_id)
                .where(Case.repo == replay.REPO)
            ).all()
        )
    counts = Counter()
    for n, number in enumerate(numbers, 1):
        key = replay.delivery_id(number)
        raw, headers = replay.signed(number, settings.webhook_secret)

        def post():
            response = client.post(URL, content=raw, headers=headers)
            response.raise_for_status()
            receipt = response.json()
            if (
                not isinstance(receipt, dict)
                or receipt.get("state") not in {"ACCEPTED", "DUPLICATE"}
                or not receipt.get("case_id")
            ):
                raise ValueError(
                    "n8n must return the backend receipt from the published workflow's last node"
                )
            return receipt

        try:
            if key in prior:
                counts["already_received"] += 1
                cid = prior[key]
            else:
                receipt = post()
                cid = receipt["case_id"]
                with sessions() as db:
                    stored = db.get(Delivery, key)
                    if not stored or stored.case_id != cid:
                        raise ValueError("Receipt is not present in this sandbox database")
                counts[receipt["state"].lower()] += 1
            if probe and n == 1:
                duplicate = post()
                if duplicate != {"state": "DUPLICATE", "case_id": cid}:
                    raise ValueError("Duplicate did not resolve to the same case")
                print(f"Replay #{number}: DUPLICATE; same case {cid}", flush=True)
        except (ValueError, httpx.HTTPError) as exc:
            raise SystemExit(
                f"Stopped at issue #{number} ({n}/{limit}): {exc}. Fix n8n and rerun the same command; saved deliveries are retained."
            ) from exc
        if n == 1 or n % 50 == 0 or n == len(numbers):
            print(f"Delivery {n}/{len(numbers)} | VS Code #{number} | {dict(counts)}", flush=True)
    return {"target": len(numbers), **counts}


def progress(settings, sessions):
    with sessions() as db:
        rows = db.execute(
            select(
                Case.status,
                Case.proposal["telemetry"]["model"],
                Case.proposal["telemetry"]["stages"],
            ).where(Case.repo == replay.REPO)
        ).all()
        reviewed = db.scalar(
            select(func.count(distinct(Event.case_id)))
            .join(Case, Case.id == Event.case_id)
            .where(Case.repo == replay.REPO, Event.kind.in_(["APPROVAL", "REJECTION"]))
        )
    return {
        "dataset": replay.DATASET,
        "revision": replay.REVISION,
        "run_id": str(settings.replay_run_id),
        "expected": replay.TOTAL,
        "received": len(rows),
        "not_received": replay.TOTAL - len(rows),
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
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    s = subs.add_parser("send")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--limit", type=int)
    g.add_argument("--all", action="store_true")
    s.add_argument("--probe-duplicate", action="store_true")
    subs.add_parser("status")
    args = parser.parse_args()
    settings = Settings()
    if not settings.dataset_replay:
        raise SystemExit("Use the isolated dataset Compose profile via scripts/demo.py")
    engine, sessions = database(settings.database_url)
    try:
        if args.action == "send":
            limit = replay.TOTAL if args.all else args.limit
            if not 1 <= limit <= replay.TOTAL:
                raise SystemExit(f"Choose 1..{replay.TOTAL} or --all")
            with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
                send(settings, sessions, limit=limit, probe=args.probe_duplicate, client=client)
        print(json.dumps(progress(settings, sessions), indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
