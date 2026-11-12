"""Fault injection tests for interrupted writes and durable, explicit recovery."""

import httpx
import pytest
from sqlalchemy import select
from issuepilot.db import Action, Case, DemoIssue
from test_workflow import case


def approved(runtime, auth):
    client, worker, service, settings = runtime
    cid, data = case(runtime, auth)
    client.post(
        f"/cases/{cid}/decision", headers=auth, json={"version": data["version"], "approved": True}
    )
    return cid


def status_error(code):
    req = httpx.Request("POST", "https://api.github.com/example")
    return httpx.HTTPStatusError(
        "Rejected", request=req, response=httpx.Response(code, request=req)
    )


def recover(client, cid, auth, **kw):
    return client.post(f"/cases/{cid}/recover", headers=auth, json={"version": 1, **kw})


@pytest.mark.parametrize("code", [401, 403, 404, 422, 429])
def test_permission_fix_resumes_only_remaining_actions(runtime, auth, monkeypatch, code):
    client, worker, service, settings = runtime
    cid = approved(runtime, auth)
    original = service.github.write
    calls = []

    def reject_label(number, kind, payload):
        calls.append(kind)
        if kind == "labels":
            raise status_error(code)
        return original(number, kind, payload)

    monkeypatch.setattr(service.github, "write", reject_label)
    worker.tick()
    data = client.get(f"/cases/{cid}", headers=auth).json()
    assert data["status"] == "BLOCKED"
    assert any(
        a["status"] == "REJECTED" and a["result"]["http_status"] == code for a in data["actions"]
    )
    assert client.post(f"/cases/{cid}/regenerate", headers=auth).status_code == 409
    monkeypatch.setattr(service.github, "write", original)
    assert recover(client, cid, auth).json()["state"] == "RESUME_QUEUED"
    assert recover(client, cid, auth).status_code == 409
    worker.tick()
    assert client.get(f"/cases/{cid}", headers=auth).json()["status"] == "COMPLETED"
    with service.sessions() as s:
        assert len(s.get(DemoIssue, 1).comments) == 1
        assert s.get(DemoIssue, 1).data["labels"] == ["bug"]
        assert len(list(s.scalars(select(Action)))) == 2


@pytest.mark.parametrize("delivered", [True, False])
def test_unknown_comment_requires_readback_and_explicit_risk_ack(
    runtime, auth, monkeypatch, delivered
):
    client, worker, service, _ = runtime
    cid = approved(runtime, auth)
    original = service.github.write

    def timeout(number, kind, payload):
        if delivered:
            original(number, kind, payload)
        raise httpx.ReadTimeout("Response lost")

    monkeypatch.setattr(service.github, "write", timeout)
    worker.tick()
    assert client.get(f"/cases/{cid}", headers=auth).json()["status"] == "UNCERTAIN"
    monkeypatch.setattr(service.github, "write", original)
    response = recover(client, cid, auth)
    if not delivered:
        assert response.status_code == 409
        response = recover(client, cid, auth, acknowledge_unknown=True)
    assert response.status_code == 200
    worker.tick()
    with service.sessions() as s:
        assert len(s.get(DemoIssue, 1).comments) == 1
    assert client.get(f"/cases/{cid}", headers=auth).json()["status"] == "COMPLETED"


def test_recovery_checks_freshness_again_in_worker(runtime, auth, monkeypatch):
    client, worker, service, _ = runtime
    cid = approved(runtime, auth)
    original = service.github.write
    monkeypatch.setattr(
        service.github, "write", lambda *a: (_ for _ in ()).throw(status_error(403))
    )
    worker.tick()
    monkeypatch.setattr(service.github, "write", original)
    assert recover(client, cid, auth).status_code == 200
    with service.sessions.begin() as s:
        row = s.get(DemoIssue, 1)
        row.data = {**row.data, "body": "Updated issue requirements"}
    worker.tick()
    with service.sessions() as s:
        assert s.get(DemoIssue, 1).comments == []
    assert recover(client, cid, auth).status_code == 409
    assert recover(client, cid, auth, mode="regenerate").json()["state"] == "QUEUED"
    worker.tick()
    data = client.get(f"/cases/{cid}", headers=auth).json()
    assert data["version"] == 2 and data["status"] == "REVIEW"
    assert all(a["status"] == "CANCELLED" for a in data["actions"])


def test_recovery_read_outage_preserves_ledger(runtime, auth, monkeypatch):
    client, worker, service, _ = runtime
    cid = approved(runtime, auth)
    monkeypatch.setattr(
        service.github, "write", lambda *a: (_ for _ in ()).throw(status_error(403))
    )
    worker.tick()
    before = client.get(f"/cases/{cid}", headers=auth).json()
    monkeypatch.setattr(
        service.github, "read", lambda *a: (_ for _ in ()).throw(httpx.ConnectError("offline"))
    )
    assert recover(client, cid, auth).status_code == 502
    after = client.get(f"/cases/{cid}", headers=auth).json()
    assert after["actions"] == before["actions"] and after["status"] == before["status"]


def test_legacy_unknown_ledger_recoverable_and_version_protected(runtime, auth, monkeypatch):
    client, worker, service, _ = runtime
    cid = approved(runtime, auth)
    service.prepare_actions(cid)
    with service.sessions.begin() as s:
        s.get(Case, cid).status = "UNCERTAIN"
        for a in s.scalars(select(Action).where(Action.case_id == cid)):
            a.status = "UNKNOWN"
    assert client.post(f"/cases/{cid}/recover", json={"version": 1}).status_code == 401
    assert recover(client, cid, auth, version=99).status_code == 409
    assert recover(client, cid, auth).status_code == 409
    assert recover(client, cid, auth, acknowledge_unknown=True).status_code == 200
    worker.tick()
    assert client.get(f"/cases/{cid}", headers=auth).json()["status"] == "COMPLETED"


def test_failed_planning_can_regenerate_after_model_repair(runtime, auth, monkeypatch):
    client, worker, service, _ = runtime
    original = service.github.read
    monkeypatch.setattr(
        service.github, "read", lambda *a: (_ for _ in ()).throw(httpx.ConnectError("offline"))
    )
    cid = client.post(
        "/demo/issues", headers=auth, json={"number": 1, "title": "API crash", "body": "error"}
    ).json()["case_id"]
    worker.tick()
    assert client.get(f"/cases/{cid}", headers=auth).json()["status"] == "FAILED"
    monkeypatch.setattr(service.github, "read", original)
    assert client.post(f"/cases/{cid}/regenerate", headers=auth).status_code == 200
    worker.tick()
    assert client.get(f"/cases/{cid}", headers=auth).json()["status"] == "REVIEW"


def test_webhook_cannot_overwrite_failed_partial_execution(runtime, auth, monkeypatch):
    from test_workflow import signed

    client, worker, service, settings = runtime
    cid = approved(runtime, auth)
    monkeypatch.setattr(
        service.github, "write", lambda *a: (_ for _ in ()).throw(status_error(403))
    )
    worker.tick()
    with service.sessions.begin() as db:
        db.get(Case, cid).status = "FAILED"
    raw, headers = signed(settings, delivery="after-partial")
    assert client.post("/webhooks/github", content=raw, headers=headers).status_code == 202
    data = client.get(f"/cases/{cid}", headers=auth).json()
    assert data["version"] == 1 and data["decision"]["approved"] is True
    assert data["status"] == "FAILED"


def test_recovery_respects_repository_scope(runtime, auth, monkeypatch):
    client, worker, service, settings = runtime
    cid = approved(runtime, auth)
    monkeypatch.setattr(
        service.github, "write", lambda *a: (_ for _ in ()).throw(status_error(403))
    )
    worker.tick()
    settings.repo = "other/repository"
    assert recover(client, cid, auth).status_code == 403


def test_server_error_stays_unknown(runtime, auth, monkeypatch):
    client, worker, service, _ = runtime
    cid = approved(runtime, auth)
    monkeypatch.setattr(
        service.github, "write", lambda *a: (_ for _ in ()).throw(status_error(503))
    )
    worker.tick()
    data = client.get(f"/cases/{cid}", headers=auth).json()
    assert data["status"] == "UNCERTAIN"
    assert any(a["status"] == "UNKNOWN" for a in data["actions"])


def test_demo_edit_preserves_failed_partial_ledger(runtime, auth, monkeypatch):
    client, worker, service, _ = runtime
    cid = approved(runtime, auth)

    def reject(*args):
        raise status_error(403)

    monkeypatch.setattr(service.github, "write", reject)
    worker.tick()
    with service.sessions.begin() as session:
        session.get(Case, cid).status = "FAILED"
    response = client.post(
        "/demo/issues",
        headers=auth,
        json={"number": 1, "title": "Changed issue", "body": "Changed source"},
    )
    assert response.status_code == 200
    data = client.get(f"/cases/{cid}", headers=auth).json()
    assert data["version"] == 1
    assert data["status"] == "FAILED"
    assert data["decision"]["approved"] is True
