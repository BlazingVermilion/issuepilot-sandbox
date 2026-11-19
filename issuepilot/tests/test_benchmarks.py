"""Scoring and workflow tests with independent expected outcomes, never live writes."""

import json
from pathlib import Path
import pytest
from issuepilot.benchmarks import classification_metrics, run_b, settings_for, Predictor
from issuepilot.policy import Policies
from issuepilot.workflow_benchmark import run


def test_failures_and_abstentions_remain_in_denominators():
    rows = [
        {
            "expected": expected,
            "predicted": predicted,
            "valid_contract": predicted != "ERROR",
            "latency_ms": 10,
        }
        for expected, predicted in [
            ("bug", "bug"),
            ("bug", "ABSTAIN"),
            ("feature-request", "ERROR"),
            ("feature-request", "bug"),
        ]
    ]
    m = classification_metrics(rows)
    assert m["accuracy"] == 0.25
    assert m["coverage"] == 0.5
    assert m["selective_accuracy"] == 0.5
    assert m["macro_f1"] == 0.25
    assert m["failure_rate"] == 0.25


def test_predictor_long_body_does_not_overflow_proposal_summary():
    s = settings_for("rules", "unused", "http://localhost:1")
    p = Predictor(s, Policies(s.policy_dir), "rules")
    category, abstain, _, _ = p.classify("Title: Editor crash\n" + "Evidence " * 1000)
    assert category == "bug" and not abstain
    with pytest.raises(ValueError, match="budget"):
        p.classify("x" * (s.max_input_chars + 1))


def test_controlled_benchmark_contracts(tmp_path):
    s = settings_for("rules", "unused", "http://localhost:1")
    report = run_b(s, tmp_path / "rag")
    assert report["metrics"]["application_cases"] >= 6
    assert report["metrics"]["linked_subset_count"] == 0
    assert report["semantic_faithfulness"] == "UNMEASURED"
    assert report["metrics"]["application_contract_rate"] == 1
    assert Path(tmp_path / "rag/results.csv").is_file()
    report = run(s, tmp_path / "workflow")
    assert report["metrics"]["passed"] == report["metrics"]["total"], report["results"]
    assert report["metrics"]["write_counters"]["unauthorized_write_attempts"] == 0
    assert report["metrics"]["write_counters"]["confirmed_action_duplicate_attempts"] == 0
    assert not report["metrics"]["checkpoint_control"]["human_review"]["recovered_after_restart"]


def test_offline_evaluations_are_not_served_by_the_application(runtime, auth):
    client = runtime[0]
    assert client.get("/benchmarks", headers=auth).status_code == 404
    assert client.post("/benchmarks", headers=auth, json={"layer": "A"}).status_code == 404
    assert 'id="tab-evaluation"' not in client.get("/").text


def test_reporter_identity_and_reply_cannot_approve(runtime, auth):
    import hmac
    import hashlib

    client, worker, service, settings = runtime
    cid = client.post(
        "/demo/issues", headers=auth, json={"number": 1, "title": "Editor crash", "body": ""}
    ).json()["case_id"]
    worker.tick()
    client.post(f"/cases/{cid}/decision", headers=auth, json={"version": 1, "approved": True})
    worker.tick()
    assert client.get(f"/cases/{cid}", headers=auth).json()["status"] == "WAITING_REPORTER"
    payload = {
        "action": "created",
        "repository": {"full_name": settings.repo},
        "issue": {"number": 1, "user": {"login": "reporter"}},
        "comment": {"user": {"login": "other"}, "body": "approved"},
    }
    raw = json.dumps(payload).encode()
    signature = (
        "sha256=" + hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    )
    assert service.ingest(raw, signature, "non-author", "issue_comment")["state"] == "IGNORED"
    assert (
        client.post(f"/cases/{cid}/demo-reply", json={"body": "Version: 1.95"}).status_code == 401
    )
    client.post(f"/cases/{cid}/demo-reply", headers=auth, json={"body": "Version: 1.95"})
    worker.tick()
    result = client.get(f"/cases/{cid}", headers=auth).json()
    assert result["status"] == "REVIEW" and result["version"] == 2
    assert result["decision"] == {}
    assert result["proposal"]["value"]["missing_fields"] == ["reproduction"]


def test_label_binary_profile_does_not_create_missing_live_labels(runtime):
    from issuepilot.labels import catalog

    result = catalog(runtime[3], [{"name": "bug"}, {"name": "enhancement"}])
    assert result["allowed"] == ["bug"]


def test_linked_extractor_excludes_answer_comment_and_generic_links(tmp_path):
    from issuepilot.linked_sources import extract

    source = tmp_path / "history.jsonl"
    row = {
        "id": 1,
        "opening_snapshot_verified": True,
        "snapshot_provenance": {"source": "synthetic-fixture-not-real-history"},
        "title": "Crash",
        "body": "Observed error",
        "comments": [
            {
                "id": 1,
                "body": "Visible context",
                "created_at": "2026-01-01",
                "author_association": "NONE",
            },
            {
                "id": 2,
                "body": "ANSWER_MARKER https://github.com/microsoft/vscode/wiki/Issues-Triaging#requesting-information",
                "created_at": "2026-01-02",
                "author_association": "MEMBER",
            },
        ],
    }
    other = {**row, "id": 2, "opening_snapshot_verified": False}
    source.write_text(json.dumps(row) + "\n" + json.dumps(other))
    report = extract(source, tmp_path / "out")
    result = json.loads((tmp_path / "out/linked.jsonl").read_text())
    assert report["eligible"] == 1 and report["total"] == 2
    assert "ANSWER_MARKER" not in result["query"]
    assert result["relevant_sections"] == ["triage:bug-reports"]


def test_near_duplicate_detection_groups_across_splits_only():
    from issuepilot.benchmarks import near_duplicates

    text = "a b c d e f g h i j k l m n o p q r s t"
    rows = [("train", "1", text), ("test", "2", text), ("train", "3", text)]
    result = near_duplicates(rows)
    assert {tuple(r["issue_ids"]) for r in result} == {("1", "2"), ("2", "3")}


def test_failed_reply_draft_resumes_same_checkpoint_without_republishing(
    runtime, auth, monkeypatch
):
    from issuepilot.agent import Analyst
    from issuepilot.db import DemoIssue

    client, worker, service, _ = runtime
    cid = client.post(
        "/demo/issues", headers=auth, json={"number": 1, "title": "Editor crash", "body": ""}
    ).json()["case_id"]
    worker.tick()
    client.post(f"/cases/{cid}/decision", headers=auth, json={"version": 1, "approved": True})
    worker.tick()
    client.post(
        f"/cases/{cid}/demo-reply",
        headers=auth,
        json={"body": "Version: 1.95\nReproduction: save draft"},
    )
    original = Analyst.draft

    def fail(self, state):
        raise ConnectionError("model unavailable after reporter checkpoint")

    monkeypatch.setattr(Analyst, "draft", fail)
    worker.tick()
    assert client.get(f"/cases/{cid}", headers=auth).json()["status"] == "REPLY_FAILED"
    monkeypatch.setattr(Analyst, "draft", original)
    assert client.post(f"/cases/{cid}/refresh-replies", headers=auth).status_code == 200
    worker.tick()
    detail = client.get(f"/cases/{cid}", headers=auth).json()
    assert detail["status"] == "REVIEW" and detail["version"] == 2
    assert detail["proposal"]["rounds"] == 1
    assert detail["proposal"]["value"]["missing_fields"] == []
    with service.sessions() as session:
        assert len(session.get(DemoIssue, 1).comments) == 1
