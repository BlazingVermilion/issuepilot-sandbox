import hashlib
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from issuepilot import issue_source
from issuepilot.config import Settings
from issuepilot.db import Action, DemoIssue, Event
from issuepilot.analytics import event_record, verify_readback
from test_selected_ai import model


def test_mirror_requires_all_ids_and_exact_contents():
    a, b = [{"event_id": k, "sha256": "hash-" + k, "metadata": {"value": 1}} for k in ("a", "b")]
    ra = ("a", a["sha256"], json.dumps(a))
    rb = ("b", b["sha256"], b)
    verify_readback([a, b], [rb, ra])
    with pytest.raises(RuntimeError, match="IDs"):
        verify_readback([a, b], [ra, ra])
    with pytest.raises(RuntimeError, match="content"):
        verify_readback([a, b], [ra, ("b", b["sha256"], b | {"metadata": {}})])


def dataset_settings():
    return dict(
        _env_file=None,
        mode="demo",
        repo=issue_source.SANDBOX_REPOSITORY,
        engine="ollama",
        model="qwen3:8b",
        snapshot_intake=True,
        workspace_id=uuid4(),
        webhook_secret="w" * 40,
        n8n_workflow_id="maintainers",
        reviewer_tokens={"a": "a" * 40},
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": "live"},
        {"github_token": "not-allowed"},
        {"writes_enabled": True},
        {"repo": "someone/else"},
        {"engine": "rules"},
        {"workspace_id": None},
    ],
)
def test_dataset_never_writes_or_uses_rules(overrides):
    with pytest.raises(ValidationError):
        Settings(**(dataset_settings() | overrides))


def test_tampered_projection_fails_before_intake(tmp_path):
    dirty = tmp_path / "issues.jsonl"
    dirty.write_text('{"label":"bug"}')
    with pytest.raises(ValueError, match="projection"):
        issue_source.load_snapshots(dirty)


def test_dataset_case_uses_only_snapshot_and_waits_for_a_real_reviewer(runtime, auth, monkeypatch):
    api, worker, service, settings = runtime
    for k, v in dataset_settings().items():
        if k not in {"_env_file", "reviewer_tokens"}:
            setattr(settings, k, v)
    rows = {
        72: dict(
            number=72,
            title="Editor crashes on save",
            body="Saving used to work.",
            created_at="2025-01-01T00:00:00Z",
            split="test",
        ),
        73: dict(
            number=73,
            title="A second report",
            body="",
            created_at="2025-01-01T00:00:00Z",
            split="train",
        ),
    }
    monkeypatch.setattr(issue_source, "load_snapshots", lambda path: rows)
    raw, headers = issue_source.sign_issue_delivery(72, settings.webhook_secret)
    assert api.post("/webhooks/github", content=raw + b" ", headers=headers).status_code == 403
    wrong = headers | {"X-GitHub-Delivery": "random"}
    assert api.post("/webhooks/github", content=raw, headers=wrong).status_code == 403
    first = api.post("/webhooks/github", content=raw, headers=headers).json()
    cid = first["case_id"]
    assert api.post("/webhooks/github", content=raw, headers=headers).json() == {
        "state": "DUPLICATE",
        "case_id": cid,
    }
    assert (
        api.post(
            "/demo/issues", headers=auth, json={"number": 72, "title": "other", "body": "other"}
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"/cases/{cid}/demo-reply", headers=auth, json={"body": "invented reply"}
        ).status_code
        == 404
    )
    assert api.get("/workspace", headers=auth).json()["dataset"]["total"] == len(rows)
    with service.sessions() as db:
        assert db.get(DemoIssue, 72).data == issue_source.to_issue_document(rows[72])
        audit = event_record(db.scalar(select(Event).where(Event.case_id == cid)))
        assert audit["metadata"]["workspace_id"] == str(settings.workspace_id)
        assert "Saving used to work" not in json.dumps(audit)
    calls = model(
        monkeypatch,
        [
            {"category": "bug"},
            {
                "decision": "NEED_FACTS",
                "claims": [{"text": "Missing: application version.", "citations": ["IP-INFO-001"]}],
            },
        ],
    )
    assert worker.tick()
    case = api.get("/cases/" + cid, headers=auth).json()
    assert case["issue_url"] == "https://github.com/microsoft/vscode/issues/72"
    assert case["status"] == "REVIEW" and case["actions"] == []
    assert case["proposal"]["telemetry"]["stages"] == ["classification", "rag"]
    assert (
        "Title: Editor crashes on save\n\nDescription:\nSaving used to work."
        in calls[0]["messages"][1]["content"]
    )
    raw2, head2 = issue_source.sign_issue_delivery(73, settings.webhook_secret)
    assert api.post("/webhooks/github", content=raw2, headers=head2).status_code == 202
    assert (
        api.post(
            f"/cases/{cid}/decision", headers=auth, json={"version": 1, "approved": True}
        ).status_code
        == 200
    )
    # The review decision must be serviced before the next unprocessed issue.
    assert worker.tick()
    with service.sessions() as db:
        assert db.get(DemoIssue, 72).comments and all(
            a.result["demo"] for a in db.scalars(select(Action))
        )
    assert api.post("/webhooks/github", content=raw, headers=headers).json()["state"] == "DUPLICATE"


def test_prepare_ignores_gold_and_never_replaces_valid_input_on_failure(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    spec = {
        "repo": issue_source.DATASET,
        "revision": issue_source.REVISION,
        "splits": {"test": 1},
        "sha256": {},
    }
    row = {
        "issue_number": 7,
        "created_at": "2025-01-01T00:00:00Z",
        "input": "Title: X\n\nDescription:\nY",
        "label": "GOLD_SENTINEL",
        "messages": [{"content": "ASSISTANT_SENTINEL"}],
    }
    raw = json.dumps(row).encode() + b"\n"
    file = data / "test.jsonl"
    file.write_bytes(raw)
    spec["sha256"]["test"] = hashlib.sha256(raw).hexdigest()
    manifest = tmp_path / "spec.json"
    manifest.write_text(json.dumps(spec))
    projected = (
        issue_source.canonical_json(
            dict(number=7, created_at=row["created_at"], title="X", body="Y", split="test")
        )
        + "\n"
    ).encode()
    monkeypatch.setattr(issue_source, "SPLIT_SIZES", spec["splits"])
    monkeypatch.setattr(issue_source, "PROJECTION_SHA", hashlib.sha256(projected).hexdigest())
    dest = tmp_path / "projection"
    assert (
        issue_source.build_issue_projection(tmp_path, manifest, dest)["unique_issues"] == 1
        and dest.read_bytes() == projected
    )
    file.write_bytes(raw + b"\n")
    with pytest.raises(ValueError, match="checksum"):
        issue_source.build_issue_projection(tmp_path, manifest, dest)
    assert dest.read_bytes() == projected
