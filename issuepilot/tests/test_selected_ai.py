"""Exercise the production worker and real HTTP adapter with a local model double, never GitHub."""

from types import SimpleNamespace
from pathlib import Path
import json
import shutil

import httpx
import pytest

from issuepilot.ai import client as transport, triage
from issuepilot.ai.generation import answer
from issuepilot.ai.index import PolicyIndex
from issuepilot.ai.profile import MODEL, MODEL_DIGEST
from issuepilot.config import Settings


class TestTokenizer:
    """Only a transport fixture; no performance measurement is made with this double."""

    def encode(self, text, **_):
        return SimpleNamespace(ids=list(text.split()))


def model(monkeypatch, replies, digest=MODEL_DIGEST):
    calls = []
    monkeypatch.setattr(transport, "_tokenizer", lambda _: TestTokenizer())

    def serve(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": MODEL, "digest": digest}]})
        assert req.url.path == "/api/chat" and req.method == "POST"
        payload = json.loads(req.content)
        calls.append(payload)
        result = replies.pop(0)
        if isinstance(result, int):
            return httpx.Response(result)
        return httpx.Response(
            200,
            json={
                "model": MODEL,
                "done": True,
                "done_reason": "stop",
                "message": {"content": json.dumps(result)},
                "prompt_eval_count": 100,
                "eval_count": 20,
            },
        )

    adapter = httpx.MockTransport(serve)
    monkeypatch.setattr(triage, "Client", lambda settings: transport.Client(settings, adapter))
    return calls


def create(runtime, auth, title="Cannot save drafts"):
    api, worker, _, settings = runtime
    settings.engine = "ollama"
    cid = api.post(
        "/demo/issues",
        headers=auth,
        json={"number": 1, "title": title, "body": "Clicking Save returns an error."},
    ).json()["case_id"]
    worker.tick()
    return cid, api.get(f"/cases/{cid}", headers=auth).json()


def test_live_profile_clarification_reply_and_approval(runtime, auth, monkeypatch):
    calls = model(
        monkeypatch,
        [
            {"category": "bug"},
            {
                "decision": "NEED_FACTS",
                "claims": [{"text": "Missing: application version.", "citations": ["IP-INFO-001"]}],
            },
            {"category": "bug"},
            {
                "decision": "ALLOW",
                "claims": [
                    {
                        "text": "The required information is established.",
                        "citations": ["IP-INFO-001"],
                    }
                ],
            },
        ],
    )
    api, w, _, _ = runtime
    cid, case = create(runtime, auth)
    assert case["status"] == "REVIEW" and case["actions"] == []
    p = case["proposal"]
    assert p["value"]["missing_fields"] == ["application version"] and p["value"]["labels"] == [
        "bug"
    ]
    assert p["telemetry"]["model_digest"] == MODEL_DIGEST and p["telemetry"]["stages"] == [
        "classification",
        "rag",
    ]
    assert "POLICY:\n\nISSUE (untrusted):\nTitle:" in calls[0]["messages"][1]["content"]
    assert "IP-INFO-001" not in calls[0]["messages"][1]["content"]
    for req, ctx, n in zip(calls, [16384, 8192], [128, 512]):
        assert req["model"] == MODEL and req["think"] is False and req["stream"] is False
        assert req["options"] == dict(seed=42, temperature=0, num_ctx=ctx, num_predict=n)
    assert (
        api.post(
            f"/cases/{cid}/decision", headers=auth, json={"version": 1, "approved": True}
        ).status_code
        == 200
    )
    w.tick()
    assert api.get(f"/cases/{cid}", headers=auth).json()["status"] == "WAITING_REPORTER"
    api.post(f"/cases/{cid}/demo-reply", headers=auth, json={"body": "Version: 1.99"})
    w.tick()
    case = api.get(f"/cases/{cid}", headers=auth).json()
    assert case["version"] == 2 and case["status"] == "REVIEW" and case["decision"] == {}
    assert not case["proposal"]["value"]["missing_fields"]
    assert "Version: 1.99" not in calls[2]["messages"][1]["content"]
    assert "Version: 1.99" in calls[3]["messages"][1]["content"]


@pytest.mark.parametrize(
    "reply",
    [
        {"decision": "NO_POLICY", "claims": []},
        {
            "decision": "BLOCK",
            "claims": [{"text": "This step is prohibited.", "citations": ["IP-INFO-001"]}],
        },
        {
            "decision": "NEED_FACTS",
            "claims": [{"text": "There are unresolved details.", "citations": ["IP-INFO-001"]}],
        },
    ],
)
def test_insufficient_or_blocked_policy_never_turns_into_a_write(runtime, auth, monkeypatch, reply):
    model(monkeypatch, [{"category": "bug"}, reply])
    cid, c = create(runtime, auth)
    p = c["proposal"]["value"]
    assert p["abstain"] and not any(p[k] for k in ("labels", "assignees", "comment"))
    assert (
        runtime[0]
        .post(f"/cases/{cid}/decision", headers=auth, json={"version": 1, "approved": True})
        .status_code
        == 422
    )


def test_intent_abstention_never_invokes_rag(runtime, auth, monkeypatch):
    calls = model(monkeypatch, [{"category": "ABSTAIN"}])
    _, c = create(runtime, auth)
    assert len(calls) == 1 and c["proposal"]["value"]["category"] == "unknown"
    assert c["actions"] == [] and c["proposal"]["telemetry"]["stages"] == ["classification"]


@pytest.mark.parametrize(
    "replies,digest",
    [
        ([], "unapproved-build"),
        ([503], MODEL_DIGEST),
        (
            [
                {"category": "bug"},
                {"decision": "ALLOW", "claims": [{"text": "Allowed.", "citations": ["bogus"]}]},
            ],
            MODEL_DIGEST,
        ),
        ([{"category": "documentation"}], MODEL_DIGEST),
    ],
)
def test_model_or_protocol_failure_is_not_a_rules_fallback(
    runtime, auth, monkeypatch, replies, digest
):
    model(monkeypatch, replies, digest)
    _, c = create(runtime, auth)
    assert c["status"] == "FAILED" and not c["actions"] and not c["decision"]
    assert not any(e["kind"] == "PROPOSED" for e in c["events"])


def test_human_cannot_approve_after_runtime_policy_changed(runtime, auth, monkeypatch, tmp_path):
    api, _, _, s = runtime
    s.policy_dir = Path(shutil.copytree(s.policy_dir, tmp_path / "policies"))
    model(
        monkeypatch,
        [
            {"category": "feature-request"},
            {
                "decision": "ALLOW",
                "claims": [{"text": "The intake is complete.", "citations": ["IP-INFO-001"]}],
            },
        ],
    )
    cid, c = create(runtime, auth, title="Add a new export format")
    assert c["proposal"]["value"]["labels"] == ["feature-request"]
    with (s.policy_dir / "runtime/issuepilot_policy.md").open("a") as file:
        file.write("\nRevised.\n")
    res = api.post(f"/cases/{cid}/decision", headers=auth, json={"version": 1, "approved": True})
    assert (
        res.json()["state"] == "STALE"
        and not api.get(f"/cases/{cid}", headers=auth).json()["actions"]
    )


def test_timing_gate_and_unsupported_policy_do_not_call_model(runtime):
    index = PolicyIndex(runtime[3].policy_dir)
    value, context, trace = answer(
        None,
        index,
        "An information request was sent at hour 0. The reminder was successfully delivered at hour 144. At hour 168, may we close for no response?",
    )
    assert (
        value["decision"] == "BLOCK"
        and "192" in value["claims"][0]["text"]
        and trace["path"] == "deadline_check"
    )
    assert {"IP-CLOSE-001", "IP-INFO-003", "IP-CLOSE-002", "IP-INFO-004"} <= context.keys()
    value, docs, trace = answer(None, index, "xyzzyqx", "explanation")
    assert value["decision"] == "NO_POLICY" and not docs and trace["path"] == "no_context"


def test_pinned_context_budget_never_sends_oversized_input(monkeypatch):
    monkeypatch.setattr(transport, "_tokenizer", lambda _: TestTokenizer())
    with pytest.raises(ValueError, match="context budget"):
        transport.budget("system", "word " * 18000, 16384, 128, "unused")


def test_only_selected_live_model_can_start():
    from pydantic import ValidationError

    fields = dict(_env_file=None, reviewer_tokens={"test": "t" * 40})
    assert Settings(**fields).model == MODEL
    with pytest.raises(ValidationError, match="qwen3:8b"):
        Settings(model="qwen2.5:7b", **fields)
    with pytest.raises(ValidationError, match="IP_ENGINE"):
        Settings(mode="live", engine="rules", **fields)


def test_model_hold_cannot_be_turned_into_an_approved_write_by_editing_json(
    runtime, auth, monkeypatch
):
    model(
        monkeypatch,
        [
            {"category": "bug"},
            {
                "decision": "BLOCK",
                "claims": [
                    {"text": "The policy blocks this action.", "citations": ["IP-INFO-001"]}
                ],
            },
        ],
    )
    cid, case = create(runtime, auth)
    edited = {**case["proposal"]["value"], "abstain": False, "labels": ["bug"]}
    result = runtime[0].post(
        f"/cases/{cid}/decision",
        headers=auth,
        json={"version": 1, "approved": True, "edited": edited},
    )
    assert result.status_code == 422
    assert runtime[0].get(f"/cases/{cid}", headers=auth).json()["actions"] == []


def test_console_navigation_and_script_remain_wired_to_four_panels_and_case_dialogs(runtime, auth):
    from html.parser import HTMLParser
    import re

    class Markup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = set()
            self.tabs = {}

        def handle_starttag(self, name, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                assert attrs["id"] not in self.ids
                self.ids.add(attrs["id"])
            if attrs.get("role") == "tab":
                self.tabs[attrs["data-tab"]] = attrs["aria-controls"]

    api, _, _, s = runtime
    s.engine = "ollama"
    ui = Markup()
    ui.feed(api.get("/").text)
    assert list(ui.tabs) == ["overview", "inbox", "review", "repository"]
    assert {"audit-dialog", "evidence-dialog", "trace-audit"} <= ui.ids
    assert all(panel in ui.ids for panel in ui.tabs.values())
    script = api.get("/static/app.js").text
    assert not (set(re.findall(r'\$\("([\w-]+)"\)', script)) - ui.ids)
    assert (
        "/benchmarks" not in script
        and "tab-evaluation" not in ui.ids
        and "panel-evaluation" not in ui.ids
    )
    assert (
        api.get("/health").json()["model"] == MODEL
        and api.get("/workspace", headers=auth).json()["model"] == MODEL
    )


def test_entire_evidence_section_is_saved_with_trailing_conditions(runtime, auth, monkeypatch):
    model(
        monkeypatch,
        [
            {"category": "bug"},
            {
                "decision": "NEED_FACTS",
                "claims": [{"text": "Missing application version.", "citations": ["IP-INFO-001"]}],
            },
        ],
    )
    _, case = create(runtime, auth)
    quote = next(
        c["quote"] for c in case["proposal"]["value"]["citations"] if c["id"] == "IP-INFO-001"
    )
    section = next(d["text"] for d in case["proposal"]["documents"] if d["id"] == "IP-INFO-001")
    assert len(quote) > 1000 and quote == section and "| feature-request |" in quote


def test_alternate_model_is_pinned_and_separately_identified():
    from pydantic import ValidationError

    fields = dict(
        _env_file=None,
        reviewer_tokens={"test": "x" * 32},
        ai_profile="experimental",
        model="example:4b",
        model_digest="b" * 64,
    )
    with pytest.raises(ValidationError, match="Experimental"):
        Settings(**(fields | {"writes_enabled": True}))

    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": fields["model"], "digest": fields["model_digest"]}]}
            )
        if req.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion"],
                    "model_info": {"example.context_length": 32768},
                },
            )
        body = json.loads(req.content)
        assert body["model"] == fields["model"] and "think" not in body
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "done": True,
                "done_reason": "stop",
                "message": {"content": '{"category":"bug"}'},
                "prompt_eval_count": 20,
            },
        )

    client = transport.Client(Settings(**fields), httpx.MockTransport(handler))
    assert client.chat("classification", "system", "issue", {}) == {"category": "bug"}
    assert (
        client.telemetry()["profile"].startswith("experimental/")
        and client.telemetry()["model_digest"] == fields["model_digest"]
    )
