"""Run the checked-in reporting joins against a compact event ledger.

Only Snowflake's VARIANT access and qualified table name are adapted for SQLite.
The groupings, windows, joins and arithmetic run from the shipping SQL verbatim.
"""

import json
from pathlib import Path
import re
import sqlite3
from statistics import median

SQL = Path(__file__).resolve().parents[1] / "warehouse"


class Median:
    def __init__(self):
        self.values = []

    def step(self, value):
        if value is not None:
            self.values.append(value)

    def finalize(self):
        return median(self.values) if self.values else None


def queries(name):
    source = (SQL / name).read_text(encoding="utf-8")
    source = re.sub(r"--[^\n]*", "", source)
    source = source.replace("ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS", "triage_events")
    source = re.sub(
        r"((?:[a-z]+\.)?BODY)((?::\w+)+)::(?:VARCHAR|INTEGER|BOOLEAN|FLOAT)",
        lambda m: f"json_extract({m[1]}, '$" + m[2].replace(":", ".") + "')",
        source,
    )
    return [s for s in source.split(";") if s.strip()]


def test_workspace_reports_do_not_depend_on_a_session_variable_or_double_count():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.create_aggregate("MEDIAN", 1, Median)
    db.execute("CREATE TABLE triage_events (EVENT_ID TEXT, BODY TEXT)")

    def event(event_id, case, kind, **metadata):
        row = {"case_id": case, "kind": kind, "metadata": metadata, "created_at": event_id}
        db.execute("INSERT INTO triage_events VALUES (?,?)", (event_id, json.dumps(row)))

    event("01", "a", "RECEIVED", replay_run_id="old", source_number=12)
    event("02", "a", "RECEIVED", replay_run_id="old", source_number=12)
    model = dict(model="qwen3:8b", engine="ollama", policy_hash="policy", latency_ms=3000)
    event("03", "a", "PROPOSED", version=1, **model)
    event("03", "a", "PROPOSED", version=1, **model)  # duplicate physical row
    event("04", "a", "APPROVAL", version=1, edited=True)
    event("05", "a", "APPROVAL", version=1, edited=True)
    event("06", "a", "FAILED")
    event("07", "a", "PROPOSED", version=2, **model)  # unreviewed version
    event("08", "b", "RECEIVED", workspace_id="now", source_number=12, source_size=1)
    event("09", "b", "PROPOSED", version=1, **model)
    event("10", "live", "RECEIVED", source_number=42)
    event("11", "shared", "RECEIVED", workspace_id="old", source_number=13)
    event("12", "shared", "RECEIVED", workspace_id="now", source_number=13)
    event("13", "shared", "COMPLETED")

    counts, versions = queries("intake_metrics.sql")
    assert queries("replay.sql") == [counts, versions]
    report = {r["workspace_id"]: r for r in db.execute(counts)}
    assert set(report) == {"old", "now"}
    old = report["old"]
    assert (old["received_issues"], old["received_cases"], old["source_size"]) == (2, 2, None)
    assert (old["cases_with_qwen_proposal"], old["reviewed_cases"], old["cases_ever_failed"]) == (
        1,
        1,
        1,
    )
    assert (old["ambiguous_cases"], old["completed_cases"]) == (1, 0)
    assert (old["distinct_event_ids"], old["duplicate_event_ids"]) == (7, 1)
    assert report["now"]["source_size"] == 1
    rates = {r["workspace_id"]: r for r in db.execute(versions)}
    assert (rates["old"]["proposals"], rates["old"]["reviewed"], rates["old"]["rejected"]) == (
        2,
        1,
        0,
    )
    assert rates["old"]["edited_pct_of_reviewed"] == 100
    assert rates["now"]["edited_pct_of_reviewed"] is None
    assert rates["old"]["median_draft_seconds"] == 3
    (global_events,) = queries("metrics.sql")
    assert sum(r["mirrored_rows"] for r in db.execute(global_events)) == 14
    db.close()
