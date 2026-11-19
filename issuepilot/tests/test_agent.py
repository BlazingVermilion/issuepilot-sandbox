"""LLM orchestration contract tests use a fake model; they are not model-quality scores."""

from issuepilot.agent import Analyst
from issuepilot.contracts import Proposal
from issuepilot.policy import Policies
import pytest


def test_citation_cannot_reference_unretrieved_policy(runtime):
    from issuepilot.contracts import validate_proposal

    proposal = Proposal(
        category="bug",
        summary="x",
        labels=["bug"],
        citations=[{"id": "invented", "quote": "This policy does not exist."}],
        reason="x",
    )
    with pytest.raises(ValueError, match="retrieved policy"):
        validate_proposal(proposal, [], runtime[3])


def test_graph_survives_checkpointer_reopen(runtime, tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command

    policies = Policies(runtime[3].policy_dir)
    analyst = Analyst(runtime[3], policies)
    path = str(tmp_path / "durable.db")
    config = {"configurable": {"thread_id": "restart-test"}}
    with SqliteSaver.from_conn_string(path) as saver:
        result = analyst.graph(saver).invoke({"issue": {"title": "Crash", "body": "Error"}}, config)
        assert "__interrupt__" in result
    with SqliteSaver.from_conn_string(path) as saver:
        graph = analyst.graph(saver)
        assert graph.get_state(config).next == ("human_review",)
        result = graph.invoke(Command(resume={"approved": False, "reviewer": "alice"}), config)
        assert result["decision"]["approved"] is False
        assert graph.get_state(config).next == ()


def test_analytics_does_not_export_content_or_reviewer(runtime):
    from issuepilot.analytics import event_record
    from issuepilot.db import Event, now

    e = Event(
        id="a",
        case_id="b",
        kind="APPROVAL",
        created_at=now(),
        data={
            "reviewer": "private-name",
            "body": "secret body",
            "token": "secret",
            "version": 1,
            "edited": False,
        },
    )
    result = event_record(e)
    assert result["metadata"] == {"version": 1, "edited": False}
    assert len(result["sha256"]) == 64
