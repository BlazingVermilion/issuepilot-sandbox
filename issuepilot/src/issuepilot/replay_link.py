"""Receipt protocol for the existing local n8n workflow. No network or third-party imports."""

import hashlib
import hmac
import re
from uuid import uuid4

from . import replay

WORKFLOW = "H4oYmTRrADtudD4S"
EDITOR = f"http://localhost:5678/workflow/{WORKFLOW}"
PATH = "issuepilot-replay-1993"
WEBHOOK = "http://127.0.0.1:5678/webhook/" + PATH
API = "http://127.0.0.1:8021"
CALLBACK = "/webhooks/n8n/replay"


def mac(secret, blob):
    return hmac.new(secret.encode(), blob, hashlib.sha256).hexdigest()


def probe(secret, run_id):
    nonce = uuid4().hex
    raw = replay.compact(
        {"purpose": "issuepilot-replay-check", "run_id": run_id, "nonce": nonce}
    ).encode()
    return (
        nonce,
        raw,
        {
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": nonce,
            "X-Hub-Signature-256": "sha256=" + mac(secret, raw),
        },
    )


def route(workflow, execution):
    if (
        workflow != WORKFLOW
        or not isinstance(execution, str)
        or not re.fullmatch(r"[1-9][0-9]{0,24}", execution)
    ):
        raise PermissionError("Expected the published replay branch in workflow " + WORKFLOW)
    return {"n8n_workflow_id": workflow, "n8n_execution_id": execution}


def reply_tag(secret, run_id, nonce, meta):
    return mac(secret, replay.compact(["replay-receipt-v1", run_id, nonce, meta]).encode())


def validate_ping(payload, delivery, run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", delivery) or payload != {
        "purpose": "issuepilot-replay-check",
        "run_id": run_id,
        "nonce": delivery,
    }:
        raise PermissionError("Ping did not match this replay instance")
