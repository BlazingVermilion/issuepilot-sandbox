"""Send signed issue snapshots through n8n and reconcile receipts with the case store."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID
from . import issue_source, intake_protocol as protocol
from .workspace_files import atomic_write


class RouteNotReady(ValueError):
    """A signed read-only probe may be repeated while n8n registers its webhook."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def request_json(url, raw=None, headers=None):
    parts = urlsplit(url)
    local_hook = (
        parts.scheme == "http"
        and parts.hostname == "127.0.0.1"
        and parts.path == "/webhook/" + protocol.WEBHOOK_PATH
        and not (parts.query or parts.fragment or parts.username or parts.password)
    )
    if url != protocol.API_BASE + protocol.RECEIPTS_PATH and not local_hook:
        raise ValueError("The client accepts only local webhook and receipt URLs")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(Request(url, raw, headers or {}), timeout=30) as response:
            body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise ValueError("Response exceeds the receipt limit")
            return json.loads(body)
    except HTTPError as exc:
        error = RouteNotReady if exc.code in {404, 502, 503, 504} else ValueError
        raise error(
            f"{url} returned HTTP {exc.code}. Check the published POST path and the last node in n8n Executions."
        ) from None
    except (URLError, TimeoutError, UnicodeError, json.JSONDecodeError):
        raise ValueError(
            f"No valid JSON receipt from {url}. Check the running services, last node and callback."
        ) from None


def verify_intake(values, request=request_json):
    target = protocol.WorkflowTarget.from_editor_url(values["IP_N8N_EDITOR_URL"])
    secret, workspace_id = values["IP_WEBHOOK_SECRET"], values["IP_WORKSPACE_ID"]
    nonce, raw, headers = protocol.create_challenge(secret, workspace_id)
    for delay in (1, 2, 3, 0):
        try:
            r = request(target.webhook_url, raw, headers)
            break
        except RouteNotReady:
            if not delay:
                raise
            time.sleep(delay)
    if (
        not isinstance(r, dict)
        or r.get("state") != "INTAKE_READY"
        or r.get("workspace_id") != workspace_id
    ):
        raise ValueError(
            "Wrong workflow/instance or missing backend receipt. Publish the issue intake workflow and return the last node's JSON."
        )
    meta = protocol.verify_workflow_context(
        target.workflow_id, r.get("n8n_workflow_id"), r.get("n8n_execution_id")
    )
    if not hmac.compare_digest(
        str(r.get("proof", "")), protocol.sign_receipt(secret, workspace_id, nonce, meta)
    ):
        raise ValueError("The signed route proof did not match. No issues sent.")
    return {"state": "INTAKE_READY", "workspace_id": workspace_id, **meta}


def submit_issues(values, folder, *, resume=False, request=request_json, progress=print):
    target = protocol.WorkflowTarget.from_editor_url(values["IP_N8N_EDITOR_URL"])
    route = verify_intake(values, request)
    workspace_id = route["workspace_id"]
    secret = values["IP_WEBHOOK_SECRET"]
    reviewers = json.loads(values["IP_REVIEWER_TOKENS"])
    if not isinstance(reviewers, dict) or not reviewers:
        raise ValueError("The workspace has no reviewer for the authenticated database readback")
    reviewer = reviewers.get("maintainer") or next(iter(reviewers.values()))
    if not isinstance(reviewer, str) or not reviewer:
        raise ValueError("A reviewer token is required for database readback")

    def readback():
        data = request(
            protocol.API_BASE + protocol.RECEIPTS_PATH,
            None,
            {"Authorization": "Bearer " + reviewer},
        )
        if (
            not isinstance(data, dict)
            or data.get("workspace_id") != workspace_id
            or not isinstance(data.get("receipts"), dict)
        ):
            raise ValueError("Cannot verify this cohort in the workspace database")
        return data["receipts"]

    rows = issue_source.load_snapshots(str(folder / "projection/issues.jsonl"))
    numbers = sorted(rows, key=lambda n: {"test": 0, "validation": 1, "train": 2}[rows[n]["split"]])
    before, expected = readback(), {}
    accepted = duplicate = skipped = 0
    first = last = None
    digest = hashlib.sha256()
    for i, number in enumerate(numbers, 1):
        key = issue_source.issue_delivery_id(number, values["IP_REPO"])
        try:
            if resume and key in before:
                cid = before[key]
                skipped += 1
            else:
                raw, headers = issue_source.sign_issue_delivery(number, secret, values["IP_REPO"])
                answer = request(target.webhook_url, raw, headers)
                if (
                    not isinstance(answer, dict)
                    or answer.get("workspace_id") != workspace_id
                    or answer.get("state") not in {"ACCEPTED", "DUPLICATE"}
                ):
                    raise ValueError("Invalid n8n issue receipt or wrong cohort")
                meta = protocol.verify_workflow_context(
                    target.workflow_id,
                    answer.get("n8n_workflow_id"),
                    answer.get("n8n_execution_id"),
                )
                cid = str(UUID(str(answer.get("case_id"))))
                record = {"issue": number, "case_id": cid, "state": answer["state"], **meta}
                first = first or record
                last = record
                accepted += int(answer["state"] == "ACCEPTED")
                duplicate += int(answer["state"] == "DUPLICATE")
                digest.update((issue_source.canonical_json(record) + "\n").encode())
            expected[key] = cid
            if i == 1 or i % 50 == 0 or i == len(numbers):
                got = readback()
                if any(got.get(k) != v for k, v in expected.items()):
                    raise ValueError("A receipt does not match PostgreSQL")
                progress(
                    f"Verified {i}/{len(numbers)} | #{number} | accepted={accepted} duplicate={duplicate} skipped={skipped}",
                    flush=True,
                )
        except (ValueError, OSError) as exc:
            raise ValueError(
                f"At #{number} ({i}/{len(numbers)}): {exc}. After fixing the route use: py -3.12 manage.py submit --resume"
            ) from None
    if len(set(expected.values())) != len(numbers):
        raise ValueError("Distinct issue numbers do not map to distinct cases")
    report = {
        "state": "INTAKE_VERIFIED",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "editor": target.editor_url,
        "webhook": target.webhook_url,
        "workspace_id": workspace_id,
        "revision": issue_source.REVISION,
        "durable_cases": len(expected),
        "posted_this_command": accepted + duplicate,
        "accepted": accepted,
        "duplicate": duplicate,
        "skipped_previously_stored": skipped,
        "first_execution": first,
        "last_execution": last,
        "receipts_this_command_sha256": digest.hexdigest(),
    }
    path = folder / "artifacts" / "intake"
    path.mkdir(parents=True, exist_ok=True)
    file = path / "summary.json"
    atomic_write(file, (json.dumps(report, indent=2) + "\n").encode("utf8"))
    progress(json.dumps(report, indent=2) + "\nEvidence file: " + str(file))
    return report
