"""LLM orchestration contract tests use a fake model; they are not model-quality scores."""

from langchain_core.messages import AIMessage
from issuepilot.agent import Analyst
from issuepilot.contracts import Proposal
from issuepilot.policy import Policies
import pytest


def test_llm_calls_read_only_policy_tool(runtime, monkeypatch):
    settings = runtime[3]
    settings.engine = "ollama"
    policies = Policies(settings.policy_dir)
    doc = policies.search("bug version reproduction")[0]

    class FakeModel:
        def __init__(self, **kwargs):
            self.calls = 0

        def bind_tools(self, tools):
            assert {t.name for t in tools} == {"read_issue", "search_policy", "recent_cases"}
            return self

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_policy",
                            "args": {"query": "bug version reproduction"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            return AIMessage(content="Ready")

        def with_structured_output(self, schema, method):
            class Output:
                def invoke(self, messages):
                    return Proposal(
                        category="bug",
                        summary="API crash",
                        labels=["bug"],
                        citations=[{"id": doc["id"], "quote": doc["text"][:80]}],
                        reason="Policy supported",
                    )

            return Output()

    monkeypatch.setattr("langchain_ollama.ChatOllama", FakeModel)
    output = Analyst(settings, policies).draft({"issue": {"title": "API crash", "body": "Error"}})
    assert output["trace"][0]["tool"] == "search_policy"
    assert output["documents"][0]["id"] == doc["id"]


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
