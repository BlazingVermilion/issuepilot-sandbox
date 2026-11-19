"""Offline integration check: actual signed API intake, simulated n8n envelope, fresh SQLite, no inference."""

import argparse
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from issuepilot import replay
from issuepilot.api import create_app
from issuepilot.config import Settings
from issuepilot.db import Case, Delivery, DemoIssue, Event
from dataset_run import send, progress


def run(path):
    with TemporaryDirectory() as tmp:
        cfg = Settings(
            _env_file=None,
            dataset_replay=True,
            mode="demo",
            repo=replay.REPO,
            engine="ollama",
            model="qwen3:8b",
            github_token="",
            writes_enabled=False,
            replay_run_id=uuid4(),
            replay_path=path,
            webhook_secret="w" * 40,
            reviewer_tokens={"integration": "t" * 40},
            database_url="sqlite:///" + str(Path(tmp) / "isolated.db"),
        )
        app = create_app(cfg)
        with TestClient(app) as api:

            class Relay:
                def post(self, url, content, headers):
                    return api.post(
                        "/webhooks/n8n",
                        json={
                            "raw_body_base64": base64.b64encode(content).decode(),
                            "event": headers["X-GitHub-Event"],
                            "signature": headers["X-Hub-Signature-256"],
                            "delivery_id": headers["X-GitHub-Delivery"],
                        },
                    )

            sessions = app.state.sessions
            first = send(cfg, sessions, limit=3, probe=True, client=Relay())
            full = send(cfg, sessions, limit=replay.TOTAL, client=Relay())
            resumed = send(cfg, sessions, limit=replay.TOTAL, probe=True, client=Relay())
            with sessions() as db:
                sizes = {
                    name: db.scalar(select(func.count()).select_from(table))
                    for name, table in (
                        ("cases", Case),
                        ("immutable_sources", DemoIssue),
                        ("deliveries", Delivery),
                        ("audit_events", Event),
                    )
                }
            listing = api.get("/cases", headers={"Authorization": "Bearer " + "t" * 40}).json()
            assert all(n == replay.TOTAL for n in sizes.values()) and len(listing) == replay.TOTAL
            assert {row["number"] for row in listing} == set(replay.snapshots(path))
            assert (
                first["accepted"] == 3
                and full["accepted"] == replay.TOTAL - 3
                and resumed["already_received"] == replay.TOTAL
            )
            return {
                "kind": "offline intake integration check",
                "transport": "n8n envelope simulated locally; FastAPI HMAC and DB path real",
                "database": "temporary SQLite",
                "inference_executed": False,
                "external_github_writes": 0,
                "actual_n8n": False,
                "actual_snowflake": False,
                "actual_github_actions": False,
                "counts": sizes,
                "authenticated_inbox_rows": len(listing),
                "resumed_without_new_deliveries": resumed["already_received"],
                "status": progress(cfg, sessions)["states"],
            }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=Path("data/replay/issues.jsonl"))
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    record = json.dumps(run(args.input), indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(record, encoding="utf8")
    print(record)
