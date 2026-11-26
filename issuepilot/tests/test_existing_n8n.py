"""Real verifier, case store and host client; the relay stands in for the published n8n."""

import base64
import json
from pathlib import Path
from uuid import uuid4

import pytest
from issuepilot import existing_n8n as ext, replay, replay_link as link


@pytest.fixture
def route_test(runtime, monkeypatch):
    api, _, _, cfg = runtime
    cfg.dataset_replay = True
    cfg.repo = replay.REPO
    cfg.replay_run_id = uuid4()
    src = {
        n: dict(
            number=n,
            title="Editor loses a draft",
            body="On save.",
            created_at="2025-01-01",
            split="test",
        )
        for n in (31, 32)
    }
    monkeypatch.setattr(replay, "snapshots", lambda path: src)
    monkeypatch.setattr(replay, "TOTAL", len(src))
    values = {
        "IP_WEBHOOK_SECRET": cfg.webhook_secret,
        "IP_REPLAY_RUN_ID": str(cfg.replay_run_id),
        "IP_REVIEWER_TOKENS": json.dumps({"maintainer": cfg.reviewer_tokens["alice"]}),
    }
    calls = []

    def relay(url, raw, headers, workflow=link.WORKFLOW):
        if url == link.API + "/replay/receipts":
            return api.get("/replay/receipts", headers=headers).raise_for_status().json()
        assert url == "http://127.0.0.1:5678/webhook/issuepilot-replay-1993"
        assert "Authorization" not in headers
        calls.append(headers["X-GitHub-Event"])
        return api.post(
            link.CALLBACK,
            json={
                "raw_body_base64": base64.b64encode(raw).decode(),
                "signature": headers["X-Hub-Signature-256"],
                "delivery_id": headers["X-GitHub-Delivery"],
                "event": headers["X-GitHub-Event"],
            },
            headers={"X-IssuePilot-Workflow": workflow, "X-IssuePilot-Execution": str(len(calls))},
        )

    def transport(url, raw, headers):
        response = relay(url, raw, headers)
        return response if isinstance(response, dict) else response.raise_for_status().json()

    return api, cfg, calls, relay, transport, values


def test_signed_probe_then_all_duplicate_resume_and_provenance(route_test, tmp_path, auth):
    api, _, calls, _, http, values = route_test
    assert ext.checked(values, http)["state"] == "REPLAY_READY"
    assert api.get("/replay/receipts", headers=auth).json()["receipts"] == {}
    assert api.get("/replay/receipts").status_code == 401
    accepted, dup, resumed = [
        ext.submit(values, tmp_path, request=http, resume=flag) for flag in [False, False, True]
    ]
    assert accepted["accepted"] == 2 and dup["duplicate"] == 2
    assert resumed["posted_this_command"] == 0 and resumed["skipped_previously_stored"] == 2
    assert accepted["durable_cases"] == dup["durable_cases"] == resumed["durable_cases"] == 2
    assert (
        calls.count("issues") == 4
        and len(list((tmp_path / "artifacts/intake").glob("*.json"))) == 3
    )
    case = api.get("/cases/" + accepted["first_execution"]["case_id"], headers=auth).json()
    assert (
        len(case["events"]) == 1
        and case["events"][0]["data"]["n8n_execution_id"]
        == accepted["first_execution"]["n8n_execution_id"]
    )
    assert dup["first_execution"]["case_id"] == accepted["first_execution"]["case_id"]
    assert (
        dup["first_execution"]["n8n_execution_id"]
        != accepted["first_execution"]["n8n_execution_id"]
    )


def test_probe_fails_closed_before_submitting_real_issues(route_test, tmp_path):
    api, cfg, calls, relay, _, values = route_test
    _, raw, h = link.probe(cfg.webhook_secret, values["IP_REPLAY_RUN_ID"])
    assert relay(link.WEBHOOK, raw, h, workflow="another").status_code == 403
    assert relay(link.WEBHOOK, raw + b" ", h).status_code == 403
    _, raw2, h2 = link.probe(cfg.webhook_secret, str(uuid4()))
    assert relay(link.WEBHOOK, raw2, h2).status_code == 403
    result = relay(link.WEBHOOK, raw, h).json()
    with pytest.raises(ValueError, match="proof"):
        ext.submit(
            values, tmp_path, request=lambda *args: result
        )  # old signed response to a new nonce
    assert "issues" not in calls
    cfg.dataset_replay = False
    assert relay(link.WEBHOOK, raw, h).status_code == 404
    assert (
        api.get(
            "/replay/receipts", headers={"Authorization": "Bearer " + cfg.reviewer_tokens["alice"]}
        ).status_code
        == 404
    )


def test_receipt_is_not_success_without_db_readback(route_test, tmp_path):
    _, _, _, _, http, values = route_test

    def lying(url, data, headers):
        r = http(url, data, headers)
        if url == link.WEBHOOK and headers["X-GitHub-Event"] == "issues":
            r["case_id"] = str(uuid4())
        return r

    with pytest.raises(ValueError, match="does not match PostgreSQL"):
        ext.submit(values, tmp_path, request=lying)
    assert not (tmp_path / "artifacts/intake").exists()


TEMPLATE = Path(__file__).resolve().parents[1] / "n8n/github-intake.json"


@pytest.mark.parametrize("docker", [True, False])
def test_correct_callback_and_no_second_n8n(tmp_path, docker):
    commands = []

    def runner(*args):
        commands.append(args)
        if args[0] == "ps":
            return "abc\tn8n-existing\tn8nio/n8n\ndef\told-n8n\tn8nio/n8n"
        if args[:3] == ("inspect", "--format", "{{json .NetworkSettings.Ports}}"):
            return json.dumps(
                {
                    "5678/tcp": [
                        {
                            "HostPort": "5678" if docker and args[-1] == "abc" else "5698",
                            "HostIp": "127.0.0.1",
                        }
                    ]
                }
            )
        if args[:2] == ("network", "ls"):
            return ext.NETWORK
        if args[:2] == ("network", "inspect"):
            return json.dumps(
                [
                    {
                        "Driver": "bridge",
                        "Internal": True,
                        "Containers": {},
                        "Labels": {ext.OWNER: "external-n8n-5678"},
                    }
                ]
            )
        if args[0] == "inspect":
            return "full-" + args[-1]
        return ""

    ext.configure(tmp_path, "api", TEMPLATE, runner)
    bundle = json.loads((tmp_path / ext.BRANCH_FILE).read_bytes())
    assert not ({"id", "active", "credentials"} & bundle.keys())
    first, _, last = bundle["nodes"]
    assert first["parameters"]["path"] == link.PATH
    assert (
        first["parameters"]["responseMode"] == "lastNode"
        and first["parameters"]["options"]["rawBody"]
    )
    assert (
        last["parameters"]["url"]
        == (("http://" + ext.ALIAS + ":8000") if docker else link.API) + link.CALLBACK
    )
    assert {p["value"] for p in last["parameters"]["headerParameters"]["parameters"]} == {
        "={{ $workflow.id }}",
        "={{ String($execution.id) }}",
    }
    assert all(
        edge["node"] in [n["name"] for n in bundle["nodes"]]
        for node in bundle["connections"].values()
        for lane in node["main"]
        for edge in lane
    )
    assert not any(cmd[0] in {"run", "start", "stop", "rm"} for cmd in commands)
    attach = [cmd for cmd in commands if cmd[:2] == ("network", "connect")]
    assert len(attach) == (2 if docker else 0)
    assert not any("full-def" in cmd for cmd in attach)
    assert all("api" not in cmd for cmd in attach)
