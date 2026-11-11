"""Export metadata only: no issue bodies, comments, credentials or reviewer tokens."""

from .contracts import digest

ALLOWED = {
    "version",
    "engine",
    "model",
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
