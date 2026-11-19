"""Bounded read-only tool calling, policy-grounded drafting, durable human interrupt."""

import json
import re
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from .contracts import Proposal, validate_proposal
from .ai.triage import draft as ai_draft


class State(TypedDict, total=False):
    issue: dict
    proposal: dict
    documents: list
    trace: list
    decision: dict
    label_catalog: dict
    rounds: int
    outcome: str
    receipt: dict
    workflow_version: int
    telemetry: dict


def baseline(issue, policies, allowed_labels):
    """Deterministic comparator; field extraction is deliberately not semantic ML."""
    text = (
        f"{issue['title']} {issue.get('body', '')}"
        + "\n"
        + "\n".join(r["body"] for r in issue.get("reporter_replies", []))
    ).lower()
    if any(
        w in text for w in ("crash", "error", "fail", "broken", "bug", "does not", "not working")
    ):
        category, required, query = "bug", ["version", "reproduction"], "bug version reproduction"
    elif any(w in text for w in ("feature", "enhancement", "please add", "support for")):
        category, required, query = "feature-request", ["use case"], "feature requests use case"
    else:
        return Proposal(
            category="unknown",
            summary=issue["title"],
            abstain=True,
            next_step="escalate",
            reason="Outside the binary profile or unclear intent.",
        ), []
    if category == "bug" and "extension" in text:
        required.append("extensions_disabled")
        query += " extension investigation disabled"
    patterns = {
        "version": r"version\s*[:=]\s*([^\n]+)",
        "reproduction": r"(?:reproduction|steps(?: to reproduce)?)\s*[:=]\s*([^\n]+)",
        "use case": r"use case\s*[:=]\s*([^\n]+)",
        "extensions_disabled": r"(?:extensions_disabled|extensions disabled)\s*[:=]\s*([^\n]+)",
    }
    missing = []
    for field in required:
        matches = re.findall(patterns[field], text)
        if not any(x.strip() not in {"", "missing", "unknown", "n/a", "yes/no"} for x in matches):
            missing.append(field)
    docs = policies.search(query)
    if not docs:
        return Proposal(
            category=category,
            summary=issue["title"],
            abstain=True,
            next_step="escalate",
            reason="No supporting policy.",
        ), []
    area = next(
        (
            area
            for word, area in [
                ("terminal", "integrated-terminal"),
                ("extension", "extensions"),
                ("editor", "editor"),
                ("git", "git"),
                ("workbench", "workbench"),
            ]
            if word in text
        ),
        "unknown",
    )
    return Proposal(
        category=category,
        summary=issue["title"],
        feature_area=area,
        next_step="clarify" if missing else "route",
        missing_fields=missing,
        labels=[category] if category in allowed_labels else [],
        comment=f"Could you provide the following: {', '.join(missing)}?" if missing else "",
        citations=[{"id": docs[0]["id"], "quote": docs[0]["text"]}],
        reason="Applied the local VS Code workflow adaptation.",
    ), docs


class Analyst:
    def __init__(self, settings, policies, recent_cases=lambda: []):
        self.settings, self.policies, self.recent_cases = settings, policies, recent_cases

    def draft(self, state):
        self.last_attempt = None
        if len(json.dumps(state["issue"])) > self.settings.max_input_chars:
            raise ValueError("Input exceeds the declared character budget; no silent truncation")
        label_catalog = state.get("label_catalog")
        if label_catalog is None:
            if self.settings.mode != "demo":
                raise ValueError("A live label catalog is required")
            from .labels import catalog, policy_labels

            label_catalog = catalog(self.settings, policy_labels(self.settings))
        if self.settings.engine == "rules":
            proposal, documents = baseline(state["issue"], self.policies, label_catalog["allowed"])
            trace = [{"tool": "policy_search", "mode": "fixed_workflow"}]
            self.telemetry = {"calls": 0, "model": None, "policy_hash": self.policies.hash}
        else:
            proposal, documents, trace, self.telemetry = ai_draft(
                self.settings, self.policies, state["issue"], label_catalog
            )
        self.last_attempt = {"proposal": proposal.model_dump(), "documents": documents}
        validate_proposal(proposal, documents, self.settings, label_catalog)
        proposal.next_step = (
            "escalate"
            if proposal.abstain
            else ("clarify" if proposal.missing_fields and proposal.comment else "route")
        )
        return {
            "proposal": proposal.model_dump(),
            "documents": documents,
            "trace": trace,
            "telemetry": getattr(self, "telemetry", {}),
        }

    def graph(self, checkpointer):
        """Durable reviewer/executor/reporter handoffs; writes stay in the ledger executor."""

        def start(state):
            return {"workflow_version": 2, "rounds": state.get("rounds", 0)}

        def assess(state):
            proposal = Proposal.model_validate(state["proposal"])
            if (
                proposal.next_step == "clarify"
                and state.get("rounds", 0) >= self.settings.max_clarification_rounds
            ):
                proposal.abstain = True
                proposal.labels, proposal.assignees, proposal.comment = [], [], ""
                proposal.next_step = "escalate"
                proposal.reason = "Clarification budget reached; maintainer investigation required."
            return {"proposal": proposal.model_dump()}

        def review(state):
            decision = interrupt(
                {
                    "kind": "maintainer",
                    "proposal": state["proposal"],
                    "documents": state["documents"],
                }
            )
            return {"decision": decision}

        def after_review(state):
            if not state["decision"].get("approved"):
                return "declined"
            return "execution_wait"

        def execution_wait(state):
            receipt = interrupt(
                {
                    "kind": "executor",
                    "proposal": state["decision"].get("proposal", state["proposal"]),
                }
            )
            if receipt.get("state") != "COMPLETED":
                raise ValueError("Only confirmed execution may advance the graph")
            return {"receipt": receipt}

        def after_execution(state):
            proposal = state["decision"].get("proposal", state["proposal"])
            return (
                "reporter_wait"
                if proposal.get("missing_fields") and proposal.get("comment")
                else "completed"
            )

        def reporter_wait(state):
            reply = interrupt(
                {
                    "kind": "reporter",
                    "missing_fields": state["decision"]
                    .get("proposal", state["proposal"])
                    .get("missing_fields", []),
                }
            )
            if reply.get("kind") != "reporter_snapshot" or not isinstance(reply.get("issue"), dict):
                raise ValueError("Reporter resume requires a verified source snapshot")
            return {
                "issue": reply["issue"],
                "label_catalog": reply["label_catalog"],
                "rounds": state.get("rounds", 0) + 1,
                "decision": {},
                "receipt": {},
            }

        graph = StateGraph(State)
        for name, fn in [
            ("initialize", start),
            ("draft", self.draft),
            ("assess", assess),
            ("human_review", review),
            ("execution_wait", execution_wait),
            ("reporter_wait", reporter_wait),
            ("declined", lambda s: {"outcome": "DECLINED"}),
            ("completed", lambda s: {"outcome": "COMPLETED"}),
        ]:
            graph.add_node(name, fn)
        graph.add_edge(START, "initialize")
        graph.add_edge("initialize", "draft")
        graph.add_edge("draft", "assess")
        graph.add_edge("assess", "human_review")
        graph.add_conditional_edges("human_review", after_review, ["declined", "execution_wait"])
        graph.add_conditional_edges(
            "execution_wait", after_execution, ["reporter_wait", "completed"]
        )
        graph.add_edge("reporter_wait", "draft")
        graph.add_edge("declined", END)
        graph.add_edge("completed", END)
        return graph.compile(checkpointer=checkpointer)
