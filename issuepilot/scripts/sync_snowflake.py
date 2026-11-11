"""Idempotent event mirror with full readback checks. Run one exporter per source database."""

import argparse
import json
import os
from pathlib import Path
from sqlalchemy import select, text
from issuepilot.config import Settings
from issuepilot.db import Event, database
from issuepilot.analytics import event_record

parser = argparse.ArgumentParser()
parser.add_argument("--export-only", action="store_true")
parser.add_argument("--out", default="artifacts/analytics/events.json")
args = parser.parse_args()
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
    if args.export_only:
        print(json.dumps({"state": "EXPORTED", "events": len(rows), "path": str(path)}))
        raise SystemExit(0)
    import snowflake.connector

    with snowflake.connector.connect(
        account=os.environ["IP_SF_ACCOUNT"],
        user=os.environ["IP_SF_USER"],
        authenticator="SNOWFLAKE_JWT",
        private_key_file=os.environ["IP_SF_PRIVATE_KEY_FILE"],
        private_key_file_pwd=os.environ.get("IP_SF_PRIVATE_KEY_PASSWORD"),
        role=os.environ["IP_SF_ROLE"],
        warehouse=os.environ["IP_SF_WAREHOUSE"],
        database=os.environ["IP_SF_DATABASE"],
        schema=os.environ["IP_SF_SCHEMA"],
    ) as conn:
        with conn.cursor() as cursor:
            # Destination objects are provisioned separately by warehouse/setup.sql.
            cursor.execute(
                "CREATE TEMPORARY TABLE IP_EVENT_BATCH (EVENT_ID VARCHAR, BODY VARIANT, HASH VARCHAR)"
            )
            for row in rows:
                cursor.execute(
                    "INSERT INTO IP_EVENT_BATCH SELECT %s, PARSE_JSON(%s), %s",
                    (row["event_id"], json.dumps(row), row["sha256"]),
                )
            cursor.execute("BEGIN")
            try:
                cursor.execute("""SELECT COUNT(*) FROM TRIAGE_EVENTS t JOIN IP_EVENT_BATCH b
                                  ON t.EVENT_ID=b.EVENT_ID WHERE t.HASH<>b.HASH""")
                if cursor.fetchone()[0]:
                    raise RuntimeError("Immutable event changed; refusing overwrite")
                cursor.execute("""MERGE INTO TRIAGE_EVENTS t USING IP_EVENT_BATCH b ON t.EVENT_ID=b.EVENT_ID
                    WHEN NOT MATCHED THEN INSERT (EVENT_ID,BODY,HASH) VALUES (b.EVENT_ID,b.BODY,b.HASH)""")
                cursor.execute("""SELECT t.EVENT_ID,t.HASH,t.BODY FROM TRIAGE_EVENTS t
                                  JOIN IP_EVENT_BATCH b ON t.EVENT_ID=b.EVENT_ID""")
                fetched = cursor.fetchall()
                expected = {row["event_id"]: row for row in rows}
                if len(fetched) != len(expected):
                    raise RuntimeError("Readback count or duplicate check failed")
                for event_id, hash_value, body in fetched:
                    parsed = json.loads(body) if isinstance(body, str) else body
                    if expected[event_id] != parsed or hash_value != expected[event_id]["sha256"]:
                        raise RuntimeError("Readback content mismatch")
                cursor.execute("COMMIT")
            except Exception:
                cursor.execute("ROLLBACK")
                raise
print(json.dumps({"state": "VERIFIED", "events": len(rows), "path": str(path)}))
