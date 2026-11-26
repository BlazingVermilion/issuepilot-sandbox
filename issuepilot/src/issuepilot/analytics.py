"""Export metadata only: no issue bodies, comments, credentials or reviewer tokens."""

from .contracts import digest
import json

ALLOWED = {
    "source_kind",
    "n8n_workflow_id",
    "n8n_execution_id",
    "workspace_id",
    "source_size",
    "replay_run_id",  # Retain the original hash when mirroring stored events.
    "dataset_revision",
    "source_number",
    "split",
    "version",
    "engine",
    "model",
    "profile",
    "model_digest",
    "policy_hash",
    "latency_ms",
    "category",
    "missing_count",
    "abstain",
    "edited",
    "state",
    "at",
    "exception",
    "original_assignees",
    "approved_assignees",
}


def event_record(event):
    data = {k: v for k, v in event.data.items() if k in ALLOWED}
    timestamp = event.created_at.isoformat()
    record = {
        "event_id": event.id,
        "case_id": event.case_id,
        "kind": event.kind,
        "created_at": timestamp,
        "metadata": data,
    }
    return {**record, "sha256": digest(record)}


def verify_readback(rows, fetched):
    expected = {r["event_id"]: r for r in rows}
    observed = [key for key, _, _ in fetched]
    if (
        len(expected) != len(rows)
        or len(observed) != len(expected)
        or set(observed) != set(expected)
    ):
        raise RuntimeError(
            "Readback IDs do not match the source; missing, extra or duplicate events"
        )
    for key, saved_hash, payload in fetched:
        body = json.loads(payload) if isinstance(payload, str) else payload
        if saved_hash != expected[key]["sha256"] or body != expected[key]:
            raise RuntimeError("Readback content mismatch")
