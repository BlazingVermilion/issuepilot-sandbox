"""Idempotent event mirror with full readback checks. Run one exporter per source database."""

import argparse
import json
import hashlib
from pathlib import Path
from sqlalchemy import select, text
from issuepilot.config import Settings
from issuepilot.db import Event, database
from issuepilot.analytics import event_record, verify_readback
from issuepilot.snowflake_session import checked_session

parser = argparse.ArgumentParser()
mode = parser.add_mutually_exclusive_group()
mode.add_argument("--export-only", action="store_true")
mode.add_argument("--check-context", action="store_true", help="Check access without writing data.")
parser.add_argument("--out", default="artifacts/analytics/events.json")
args = parser.parse_args()
if args.check_context:
    with checked_session():
        pass
    raise SystemExit(0)
settings = Settings()
engine, sessions = database(settings.database_url)
with sessions.begin() as source:
    if engine.dialect.name == "postgresql":
        # Serialize exporters against the same source without blocking the triage worker.
        source.execute(text("SELECT pg_advisory_xact_lock(84622102)"))
    rows = [event_record(e) for e in source.scalars(select(Event).order_by(Event.id))]
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    cohort = {
        "workspace_id": str(settings.workspace_id) if settings.snapshot_intake else None,
        "events": len(rows),
        "batch_sha256": hashlib.sha256(
            "\n".join(row["sha256"] for row in rows).encode()
        ).hexdigest(),
    }
    if args.export_only:
        print(json.dumps({"state": "EXPORTED", **cohort, "path": str(path)}))
        raise SystemExit(0)
    with checked_session() as cursor:
        # Destination objects are provisioned separately by warehouse/setup.sql.
        cursor.execute(
            "CREATE TEMPORARY TABLE IP_EVENT_BATCH (EVENT_ID VARCHAR, BODY VARIANT, HASH VARCHAR)"
        )
        for start in range(0, len(rows), 100):
            batch = rows[start : start + 100]
            values = ",".join(["(%s,%s,%s)"] * len(batch))
            cursor.execute(
                "INSERT INTO IP_EVENT_BATCH SELECT column1, PARSE_JSON(column2), column3 FROM VALUES "
                + values,
                tuple(
                    v for row in batch for v in (row["event_id"], json.dumps(row), row["sha256"])
                ),
            )
        cursor.execute("BEGIN")
        try:
            cursor.execute("""SELECT COUNT(*) FROM TRIAGE_EVENTS t JOIN IP_EVENT_BATCH b
                              ON t.EVENT_ID=b.EVENT_ID WHERE t.HASH<>b.HASH""")
            if cursor.fetchone()[0]:
                raise RuntimeError("Immutable event changed; refusing overwrite")
            cursor.execute("""MERGE INTO TRIAGE_EVENTS t USING IP_EVENT_BATCH b ON t.EVENT_ID=b.EVENT_ID
                WHEN NOT MATCHED THEN INSERT (EVENT_ID,BODY,HASH) VALUES (b.EVENT_ID,b.BODY,b.HASH)""")
            inserted = cursor.fetchone()[0]
            cursor.execute("""SELECT t.EVENT_ID,t.HASH,t.BODY FROM TRIAGE_EVENTS t
                              JOIN IP_EVENT_BATCH b ON t.EVENT_ID=b.EVENT_ID""")
            verify_readback(rows, cursor.fetchall())
            cursor.execute("COMMIT")
        except Exception:
            cursor.execute("ROLLBACK")
            raise
print(json.dumps({"state": "VERIFIED", **cohort, "inserted": int(inserted), "path": str(path)}))
