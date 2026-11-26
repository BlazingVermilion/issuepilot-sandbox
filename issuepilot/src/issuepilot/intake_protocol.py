"""The signed challenge and receipt contract between n8n, the sender and the API."""

from dataclasses import dataclass
import hashlib
import hmac
import re
from urllib.parse import urlsplit
from uuid import uuid4
from .issue_source import canonical_json

WEBHOOK_PATH = "issuepilot/issues"
API_BASE = "http://127.0.0.1:8021"
CALLBACK_PATH = "/webhooks/n8n/intake"
RECEIPTS_PATH = "/intake/receipts"


@dataclass(frozen=True)
class WorkflowTarget:
    workflow_id: str
    port: int

    def __post_init__(self):
        if not isinstance(self.workflow_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", self.workflow_id
        ):
            raise ValueError("Invalid n8n workflow ID")
        if not 1 <= self.port <= 65535:
            raise ValueError("Invalid local n8n port")

    @classmethod
    def from_editor_url(cls, value: str):
        url = urlsplit(value.strip())
        match = re.fullmatch(r"/workflow/([A-Za-z0-9_-]{1,128})/?", url.path)
        if (
            url.scheme != "http"
            or url.hostname not in {"localhost", "127.0.0.1"}
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or not match
        ):
            raise ValueError(
                "Paste the existing local editor URL: http://localhost:PORT/workflow/ID"
            )
        return cls(match.group(1), url.port or 80)

    @property
    def editor_url(self):
        return f"http://localhost:{self.port}/workflow/{self.workflow_id}"

    @property
    def webhook_url(self):
        return f"http://127.0.0.1:{self.port}/webhook/{WEBHOOK_PATH}"


def digest_signature(secret, data):
    return hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


def create_challenge(secret, workspace_id):
    nonce = uuid4().hex
    raw = canonical_json(
        {"purpose": "issue-intake-check", "workspace_id": workspace_id, "nonce": nonce}
    ).encode()
    return (
        nonce,
        raw,
        {
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": nonce,
            "X-Hub-Signature-256": "sha256=" + digest_signature(secret, raw),
        },
    )


def verify_workflow_context(expected_id, workflow_id, execution_id):
    if (
        not expected_id
        or expected_id != workflow_id
        or not isinstance(execution_id, str)
        or not re.fullmatch(r"[1-9][0-9]{0,24}", execution_id)
    ):
        raise PermissionError("Delivery did not come through the configured n8n workflow")
    return {"n8n_workflow_id": workflow_id, "n8n_execution_id": execution_id}


def sign_receipt(secret, workspace_id, nonce, metadata):
    return digest_signature(
        secret, canonical_json(["issue-intake-receipt", workspace_id, nonce, metadata]).encode()
    )


def validate_challenge(payload, delivery_id, workspace_id):
    if not re.fullmatch(r"[a-f0-9]{32}", delivery_id) or payload != {
        "purpose": "issue-intake-check",
        "workspace_id": workspace_id,
        "nonce": delivery_id,
    }:
        raise PermissionError("This challenge belongs to another workspace")
