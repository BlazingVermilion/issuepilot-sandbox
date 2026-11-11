"""Repository vocabulary must constrain proposals and subsequent writes."""

import httpx
import pytest
from issuepilot.labels import catalog
from test_workflow import case


def test_repository_catalog_requires_auth(runtime, auth):
    client = runtime[0]
    assert client.get("/repository/labels").status_code == 401
    result = client.get("/repository/labels", headers=auth).json()
    assert result["source"] == "demo_fixture"
    assert "needs-info" not in result["allowed"]
    assert "question" in result["allowed"]
    assert "wontfix" not in result["allowed"]
    assert client.get("/workspace", headers=auth).json()["model"] is None


@pytest.mark.parametrize("phase", ["review", "execution"])
def test_changed_catalog_invalidates_proposal(runtime, auth, monkeypatch, phase):
    client, worker, service, settings = runtime
    cid, data = case(runtime, auth)
    if phase == "execution":
        assert (
            client.post(
                f"/cases/{cid}/decision", headers=auth, json={"version": 1, "approved": True}
            ).json()["state"]
            == "APPROVED"
        )
    original = service.github.list_labels()
    monkeypatch.setattr(
        service.github, "list_labels", lambda: [x for x in original if x["name"] != "bug"]
    )
    if phase == "review":
        assert (
            client.post(
                f"/cases/{cid}/decision", headers=auth, json={"version": 1, "approved": True}
            ).json()["state"]
            == "STALE"
        )
    else:
        worker.tick()
    result = client.get(f"/cases/{cid}", headers=auth).json()
    assert result["status"] == "STALE"
    assert result["actions"] == []


def test_live_catalog_paginates_and_never_falls_back(runtime):
    github, settings = runtime[2].github, runtime[3]
    settings.mode = "live"
    calls = []

    def handler(request):
        calls.append(request)
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json=[{"name": f"label-{i}"} for i in range(100)] if page == 1 else [{"name": "bug"}],
        )

    github.client.close()
    github.client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )
    assert len(github.list_labels()) == 101
    assert len(calls) == 2
    github.client.close()
    github.client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(lambda r: httpx.Response(403)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        github.list_labels()


def test_description_change_invalidates_catalog(runtime):
    settings, github = runtime[3], runtime[2].github
    labels = github.list_labels()
    before = catalog(settings, labels)
    labels[0]["description"] = "Changed meaning"
    assert before["hash"] != catalog(settings, labels)["hash"]


@pytest.mark.parametrize("label", ["needs-info", "wontfix", "duplicate"])
def test_unsupported_label_cannot_be_approved(runtime, auth, label):
    client = runtime[0]
    cid, data = case(runtime, auth)
    proposal = data["proposal"]["value"]
    proposal["labels"] = [label]
    result = client.post(
        f"/cases/{cid}/decision",
        headers=auth,
        json={"version": 1, "approved": True, "edited": proposal},
    )
    assert result.status_code in (400, 422)
    assert client.get(f"/cases/{cid}", headers=auth).json()["actions"] == []
