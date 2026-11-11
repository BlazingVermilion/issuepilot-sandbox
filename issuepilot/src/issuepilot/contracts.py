"""LLM output is an untrusted proposal, validated before human review."""

import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    quote: str = Field(min_length=15, max_length=1200)


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["bug", "documentation", "enhancement", "question", "accessibility", "unknown"]
    summary: str = Field(min_length=1, max_length=1000)
    missing_fields: list[str] = Field(default_factory=list, max_length=8)
    labels: list[str] = Field(default_factory=list, max_length=4)
    assignees: list[str] = Field(default_factory=list, max_length=2)
    comment: str = Field(default="", max_length=2500)
    citations: list[Citation] = Field(default_factory=list, max_length=5)
    abstain: bool = False
    reason: str = Field(max_length=1500)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def revision(issue):
    # Labels/assignees are included because a maintainer may have already routed the issue.
    return digest(
        {k: issue.get(k) for k in ("title", "body", "state", "updated_at", "labels", "assignees")}
    )


def validate_proposal(proposal, documents, settings, label_catalog=None):
    if label_catalog is None:
        if settings.mode != "demo":
            raise ValueError("Live validation requires a freshly fetched repository label catalog")
        from .labels import catalog, DEMO_LABELS

        label_catalog = catalog(settings, DEMO_LABELS)
    if not set(proposal.labels) <= set(label_catalog["allowed"]):
        raise ValueError("Label is absent from the repository or not permitted by its policy")
    if not set(proposal.assignees) <= set(settings.allowed_assignees):
        raise ValueError("Proposal contains a non-allowlisted assignee")
    indexed = {d["id"]: d for d in documents}
    for citation in proposal.citations:
        if citation.id not in indexed or citation.quote not in indexed[citation.id]["text"]:
            raise ValueError("Citation must quote a retrieved policy chunk exactly")
    if (proposal.comment or proposal.labels or proposal.assignees) and not proposal.citations:
        raise ValueError("Actions require policy evidence")
    if proposal.abstain and (proposal.comment or proposal.labels or proposal.assignees):
        raise ValueError("Abstention cannot contain executable actions")
    return proposal
