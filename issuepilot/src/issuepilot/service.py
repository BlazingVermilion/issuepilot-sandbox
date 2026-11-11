"""Transactional intake, optimistic approvals, and a durable external-action ledger."""

import hashlib
import hmac
import json
import re
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .contracts import Proposal, revision, validate_proposal
from .db import Case, Delivery, Action, event, uid
from .policy import Policies
from .labels import catalog


class Conflict(ValueError):
    pass


class Service:
    def __init__(self, settings, sessions, github):
        self.settings, self.sessions, self.github = settings, sessions, github

    def ingest(self, raw, signature, delivery, event_name):
        expected = (
            "sha256="
            + hmac.new(self.settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
        )
        if (
            not self.settings.webhook_secret
            or not re.fullmatch(r"sha256=[0-9a-f]{64}", signature)
            or not hmac.compare_digest(expected, signature)
        ):
            raise PermissionError("Invalid webhook signature")
        if len(raw) > self.settings.max_body_bytes or not delivery or len(delivery) > 200:
            raise ValueError("Invalid webhook size or delivery ID")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Webhook payload must be an object")
        body_hash = hashlib.sha256(raw).hexdigest()
        if event_name == "ping":
            return {"state": "PING"}
        if event_name != "issues" or payload.get("action") not in {
            "opened",
            "edited",
            "reopened",
            "closed",
        }:
            return {"state": "IGNORED"}
        if payload.get("repository", {}).get("full_name", "").lower() != self.settings.repo.lower():
            raise PermissionError("Repository is outside the sandbox allowlist")
        issue = payload.get("issue", {})
        if (
            not isinstance(issue, dict)
            or not isinstance(issue.get("number"), int)
            or issue["number"] < 1
        ):
            raise ValueError("A positive issue number is required")
        if "pull_request" in issue:
            return {"state": "IGNORED"}
        try:
            with self.sessions.begin() as session:
                prior = session.get(Delivery, delivery)
                if prior:
                    if prior.payload_hash != body_hash:
                        raise Conflict("Delivery ID reused with a different body")
                    return {"state": "DUPLICATE", "case_id": prior.case_id}
                case = session.execute(
                    select(Case)
                    .where(Case.repo == self.settings.repo, Case.number == int(issue["number"]))
                    .with_for_update()
                ).scalar_one_or_none()
                if not case:
                    case = Case(repo=self.settings.repo, number=int(issue["number"]))
                    session.add(case)
                    session.flush()
                elif case.status in {"EXECUTING", "APPROVED", "UNCERTAIN"}:
                    # Never overwrite a partly executed or approved proposal. The worker reads live state.
                    event(session, case, "SOURCE_EVENT_DURING_ACTION", action=payload["action"])
                else:
                    case.version += 1
                    case.status = "QUEUED"
                    case.decision = {}
                session.add(Delivery(id=delivery, payload_hash=body_hash, case_id=case.id))
                event(session, case, "RECEIVED", delivery_id=delivery, action=payload["action"])
                return {"state": "ACCEPTED", "case_id": case.id}
        except IntegrityError:
            # A concurrent first delivery may have won either unique constraint.
            with self.sessions() as session:
                prior = session.get(Delivery, delivery)
                if prior and prior.payload_hash == body_hash:
                    return {"state": "DUPLICATE", "case_id": prior.case_id}
            raise Conflict("Concurrent intake; retry the same delivery")

    def decide(self, case_id, version, reviewer, approved, edited=None):
        with self.sessions.begin() as session:
            case = session.execute(
                select(Case).where(Case.id == case_id).with_for_update()
            ).scalar_one_or_none()
            if not case:
                raise ValueError("Unknown case")
            if case.status != "REVIEW" or case.version != version:
                raise Conflict("Proposal is no longer awaiting this review version")
            if case.repo != self.settings.repo:
                raise PermissionError("Case belongs to a different repository")
            current = self.github.read(case.number)
            policies = Policies(self.settings.policy_dir)
            label_catalog = catalog(self.settings, self.github.list_labels())
            if (
                revision(current) != case.source_hash
                or policies.hash != case.policy_hash
                or label_catalog["hash"] != case.proposal.get("label_catalog", {}).get("hash")
            ):
                case.status = "STALE"
                event(session, case, "STALE", at="approval")
                return {"state": "STALE"}
            proposed = Proposal.model_validate(edited or case.proposal["value"])
            validate_proposal(proposed, case.proposal["documents"], self.settings, label_catalog)
            if approved and proposed.abstain:
                raise ValueError("An abstention cannot be approved for execution")
            case.decision = {
                "approved": approved,
                "reviewer": reviewer,
                "proposal": proposed.model_dump(),
                "version": version,
            }
            case.status = "APPROVED" if approved else "REJECTED"
            event(
                session,
                case,
                "APPROVAL" if approved else "REJECTION",
                reviewer=reviewer,
                edited=proposed.model_dump() != case.proposal["value"],
                version=version,
                original_assignees=case.proposal["value"]["assignees"],
                approved_assignees=proposed.assignees,
            )
            return {"state": case.status}

    def requeue(self, case_id):
        with self.sessions.begin() as session:
            case = session.execute(
                select(Case).where(Case.id == case_id).with_for_update()
            ).scalar_one()
            if case.repo != self.settings.repo:
                raise PermissionError("Case belongs to a different repository")
            if case.status not in {"STALE", "FAILED", "DECLINED"}:
                raise Conflict("Only stale, failed or rejected proposals may be regenerated")
            case.version += 1
            case.status, case.error, case.decision = "QUEUED", "", {}
            event(session, case, "REQUEUED", version=case.version)

    def prepare_actions(self, case_id):
        with self.sessions.begin() as session:
            case = session.execute(
                select(Case).where(Case.id == case_id).with_for_update()
            ).scalar_one()
            if case.status != "APPROVED" or not case.decision.get("approved"):
                raise Conflict("Execution requires an approved proposal")
            if case.repo != self.settings.repo:
                raise PermissionError("Case belongs to a different repository")
            label_catalog = catalog(self.settings, self.github.list_labels())
            if (
                label_catalog["hash"] != case.proposal.get("label_catalog", {}).get("hash")
                or revision(self.github.read(case.number)) != case.source_hash
                or Policies(self.settings.policy_dir).hash != case.policy_hash
            ):
                case.status = "STALE"
                event(session, case, "STALE", at="execution")
                return False
            if self.settings.mode == "live" and not self.settings.writes_enabled:
                raise ValueError("Enable live writes only after sandbox acceptance")
            proposal = Proposal.model_validate(case.decision["proposal"])
            validate_proposal(proposal, case.proposal["documents"], self.settings, label_catalog)
            for kind, value in (
                ("comment", proposal.comment),
                ("labels", proposal.labels),
                ("assignees", proposal.assignees),
            ):
                if value:
                    action_id = uid()
                    payload = (
                        {"body": value + f"\n\n<!-- issuepilot:{action_id} -->"}
                        if kind == "comment"
                        else {kind: value}
                    )
                    session.add(
                        Action(
                            id=action_id,
                            case_id=case.id,
                            version=case.version,
                            kind=kind,
                            payload=payload,
                        )
                    )
            case.status = "EXECUTING"
            event(session, case, "EXECUTION_STARTED", reviewer=case.decision["reviewer"])
            return True

    def execute(self, case_id):
        with self.sessions() as session:
            case = session.get(Case, case_id)
            if case.status != "EXECUTING" or not case.decision.get("approved"):
                raise Conflict("No authorized execution")
            if case.repo != self.settings.repo:
                raise PermissionError("Case belongs to a different repository")
            number = case.number
            ids = list(
                session.scalars(
                    select(Action.id).where(
                        Action.case_id == case_id, Action.version == case.version
                    )
                )
            )
        for action_id in ids:
            with self.sessions.begin() as session:
                action = session.get(Action, action_id)
                if action.status == "DONE":
                    continue
                if action.status != "PENDING":
                    raise Conflict("Ambiguous external write; reconcile before any new execution")
                action.status = "SENDING"
                kind, payload = action.kind, action.payload
            # Persist intent before I/O. Do not blindly retry a timed-out POST.
            try:
                result = self.github.write(number, kind, payload)
            except Exception:
                with self.sessions.begin() as session:
                    session.get(Action, action_id).status = "UNKNOWN"
                    case = session.get(Case, case_id)
                    case.status = "UNCERTAIN"
                    event(session, case, "WRITE_UNCERTAIN", action_id=action_id)
                raise
            with self.sessions.begin() as session:
                action = session.get(Action, action_id)
                action.status, action.result = "DONE", result
        with self.sessions.begin() as session:
            case = session.get(Case, case_id)
            case.status = "COMPLETED"
            event(session, case, "COMPLETED", version=case.version)

    def reconcile(self, case_id, reviewer):
        """Read remote effects only. Never resend ambiguous writes automatically."""
        with self.sessions.begin() as session:
            case = session.execute(
                select(Case).where(Case.id == case_id).with_for_update()
            ).scalar_one()
            if case.status != "UNCERTAIN":
                raise Conflict("Case is not awaiting reconciliation")
            if case.repo != self.settings.repo:
                raise PermissionError("Case belongs to a different repository")
            issue = self.github.read(case.number)
            actions = list(
                session.scalars(
                    select(Action).where(Action.case_id == case.id, Action.version == case.version)
                )
            )
            for action in actions:
                if action.status == "DONE":
                    continue
                exists = (
                    self.github.marker_exists(case.number, f"<!-- issuepilot:{action.id} -->")
                    if action.kind == "comment"
                    else set(action.payload[action.kind]) <= set(issue.get(action.kind, []))
                )
                if exists:
                    action.status = "DONE"
            case.status = "COMPLETED" if all(a.status == "DONE" for a in actions) else "UNCERTAIN"
            event(session, case, "RECONCILED", reviewer=reviewer, state=case.status)
            return {"state": case.status}
