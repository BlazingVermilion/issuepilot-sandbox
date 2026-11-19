"""Controlled episodes against the actual graph and persistent SQLite checkpoints."""

import tempfile
import time
from pathlib import Path
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from .agent import Analyst
from .policy import Policies
from .benchmarks import provenance, save_report, ratio
from .labels import catalog, policy_labels


def run(settings, out):
    if settings.engine != "rules":
        raise ValueError(
            "Legacy workflow controls use a rules fixture; use the live case path for Qwen"
        )
    policies = Policies(settings.policy_dir)
    labels = catalog(settings, policy_labels(settings))
    results = []
    counters = {
        "external_write_attempts": 0,
        "unauthorized_write_attempts": 0,
        "confirmed_action_duplicate_attempts": 0,
    }
    scenarios = [
        "complete",
        "clarification",
        "partial_reply",
        "reject",
        "restart_review",
        "restart_reporter",
        "wrong_resume",
        "round_budget",
    ]
    for name in scenarios:
        start = time.perf_counter()
        trace = []
        r = {"id": name, "evidence": "controlled-real-graph", "passed": False}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                checkpoint = str(Path(tmp) / "graph.db")
                config = {"configurable": {"thread_id": name}}
                issue = {
                    "number": 1,
                    "title": "Editor crashes",
                    "body": "Version: 1.95\nReproduction: save draft" if name == "complete" else "",
                }

                def invoke(value):
                    # Each call reopens the database and reconstructs graph code, exercising persistence.
                    with SqliteSaver.from_conn_string(checkpoint) as saver:
                        graph = Analyst(settings, policies).graph(saver)
                        graph.invoke(value, config)
                        state = graph.get_state(config)
                        trace.append(list(state.next))
                        return state

                state = invoke({"issue": issue, "label_catalog": labels})
                assert state.next == ("human_review",)
                if name == "round_budget":
                    # Repeated replies do not satisfy the required fields.
                    for _ in range(settings.max_clarification_rounds):
                        p = state.values["proposal"]
                        assert not p["abstain"]
                        state = invoke(Command(resume={"approved": True, "proposal": p}))
                        assert state.next == ("execution_wait",)
                        state = invoke(Command(resume={"state": "COMPLETED"}))
                        assert state.next == ("reporter_wait",)
                        state = invoke(
                            Command(
                                resume={
                                    "kind": "reporter_snapshot",
                                    "issue": issue,
                                    "label_catalog": labels,
                                }
                            )
                        )
                    assert state.values["proposal"]["abstain"]
                    assert not state.values["proposal"]["comment"]
                elif name == "wrong_resume":
                    p = state.values["proposal"]
                    invoke(Command(resume={"approved": True, "proposal": p}))
                    try:
                        invoke(Command(resume={"state": "FAILED"}))
                    except ValueError:
                        pass
                    else:
                        raise AssertionError("Failed execution advanced graph")
                elif name == "reject":
                    state = invoke(Command(resume={"approved": False}))
                    assert not state.next and state.values["outcome"] == "DECLINED"
                else:
                    p = state.values["proposal"]
                    state = invoke(Command(resume={"approved": True, "proposal": p}))
                    assert state.next == ("execution_wait",)
                    state = invoke(Command(resume={"state": "COMPLETED"}))
                    if name == "complete":
                        assert not state.next and state.values["outcome"] == "COMPLETED"
                    else:
                        assert state.next == ("reporter_wait",)
                        issue = {
                            **issue,
                            "reporter_replies": [
                                {
                                    "id": 1,
                                    "body": "Version: 1.95"
                                    + (
                                        ""
                                        if name == "partial_reply"
                                        else "\nReproduction: save draft"
                                    ),
                                }
                            ],
                        }
                        state = invoke(
                            Command(
                                resume={
                                    "kind": "reporter_snapshot",
                                    "issue": issue,
                                    "label_catalog": labels,
                                }
                            )
                        )
                        assert state.next == ("human_review",)
                        missing = state.values["proposal"]["missing_fields"]
                        assert "version" not in missing
                        assert ("reproduction" in missing) == (name == "partial_reply")
                        assert state.values["rounds"] == 1
                r["passed"] = True
        except Exception as exc:
            r["error"] = type(exc).__name__ + ": " + str(exc)[:250]
        r.update(trace=trace, latency_ms=(time.perf_counter() - start) * 1000)
        results.append(r)
    # Actual API/worker/action ledger, with a disposable local database. No network writes.
    from fastapi.testclient import TestClient
    from .api import create_app
    from .worker import Worker
    from .db import DemoIssue, Action, Case
    from sqlalchemy import select
    import httpx
    import json
    import hmac
    import hashlib

    for name in [
        "no_approval",
        "duplicate_event",
        "rejected_write_recovery",
        "ambiguous_write_recovery",
        "read_failure_recovery",
        "source_change",
        "policy_change",
        "reviewer_edit",
        "reporter_resume",
        "reordered_reply",
    ]:
        r = {"id": name, "evidence": "controlled-api-worker-ledger", "passed": False}
        start = time.perf_counter()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                local = settings.model_copy(
                    update={
                        "database_url": f"sqlite:///{tmp}/app.db",
                        "checkpoint_url": f"{tmp}/cp.db",
                        "benchmark_dir": Path(tmp) / "benchmarks",
                        "webhook_secret": "w" * 40,
                    }
                )
                import shutil

                shutil.copytree(settings.policy_dir, Path(tmp) / "policies")
                local.policy_dir = Path(tmp) / "policies"
                app = create_app(local)
                headers = {"Authorization": "Bearer " + next(iter(local.reviewer_tokens.values()))}
                with (
                    TestClient(app) as client,
                    SqliteSaver.from_conn_string(local.checkpoint_url) as saver,
                ):
                    service = app.state.service
                    worker = Worker(local, app.state.sessions, service.github, saver)
                    cid = client.post(
                        "/demo/issues",
                        headers=headers,
                        json={"number": 1, "title": "Editor crashes", "body": ""},
                    ).json()["case_id"]
                    original_write = service.github.write

                    def observed_write(number, kind, payload):
                        counters["external_write_attempts"] += 1
                        with service.sessions() as session:
                            c = session.get(Case, cid)
                            if (
                                not c.decision.get("approved")
                                or c.decision.get("version") != c.version
                            ):
                                counters["unauthorized_write_attempts"] += 1
                            if any(
                                a.status == "DONE" and a.kind == kind and a.payload == payload
                                for a in session.scalars(
                                    select(Action).where(
                                        Action.case_id == cid, Action.version == c.version
                                    )
                                )
                            ):
                                counters["confirmed_action_duplicate_attempts"] += 1
                        return original_write(number, kind, payload)

                    service.github.write = observed_write
                    if name == "read_failure_recovery":
                        original_read = service.github.read

                        def read_failed(number):
                            raise ConnectionError("injected read failure")

                        service.github.read = read_failed
                        worker.tick()
                        assert (
                            client.get(f"/cases/{cid}", headers=headers).json()["status"]
                            == "FAILED"
                        )
                        service.github.read = original_read
                        assert (
                            client.post(f"/cases/{cid}/regenerate", headers=headers).status_code
                            == 200
                        )
                    worker.tick()
                    d = client.get(f"/cases/{cid}", headers=headers).json()
                    assert d["status"] == "REVIEW"
                    if name == "no_approval":
                        assert (
                            client.post(
                                f"/cases/{cid}/decision", json={"version": 1, "approved": True}
                            ).status_code
                            == 401
                        )
                        worker.tick()
                    elif name == "duplicate_event":
                        raw = json.dumps(
                            {
                                "action": "opened",
                                "repository": {"full_name": local.repo},
                                "issue": {"number": 1},
                            }
                        ).encode()
                        sig = (
                            "sha256="
                            + hmac.new(
                                local.webhook_secret.encode(), raw, hashlib.sha256
                            ).hexdigest()
                        )
                        h = {
                            "X-Hub-Signature-256": sig,
                            "X-GitHub-Delivery": "repeat",
                            "X-GitHub-Event": "issues",
                        }
                        assert (
                            client.post("/webhooks/github", content=raw, headers=h).json()["state"]
                            == "ACCEPTED"
                        )
                        assert (
                            client.post("/webhooks/github", content=raw, headers=h).json()["state"]
                            == "DUPLICATE"
                        )
                    elif name in {"source_change", "policy_change"}:
                        if name == "source_change":
                            with service.sessions.begin() as session:
                                row = session.get(DemoIssue, 1)
                                row.data = {**row.data, "body": "Changed after drafting"}
                        else:
                            with (local.policy_dir / "triage.md").open("a") as file:
                                file.write("\nNew maintainer policy revision.\n")
                        result = client.post(
                            f"/cases/{cid}/decision",
                            headers=headers,
                            json={"version": 1, "approved": True},
                        )
                        assert result.json()["state"] == "STALE"
                    elif name == "read_failure_recovery":
                        assert d["version"] == 2
                    else:
                        decision = {"version": 1, "approved": True}
                        if name == "reviewer_edit":
                            decision["edited"] = {
                                **d["proposal"]["value"],
                                "comment": "Please supply the version and reproduction steps.",
                            }
                        assert (
                            client.post(
                                f"/cases/{cid}/decision",
                                headers=headers,
                                json=decision,
                            ).status_code
                            == 200
                        )
                        original = service.github.write

                        def faulty(number, kind, payload):
                            if name == "ambiguous_write_recovery" and kind == "comment":
                                original(number, kind, payload)
                                raise TimeoutError("lost response")
                            if name == "rejected_write_recovery" and kind == "labels":
                                req = httpx.Request("POST", "https://example.invalid")
                                raise httpx.HTTPStatusError(
                                    "denied", request=req, response=httpx.Response(403, request=req)
                                )
                            return original(number, kind, payload)

                        service.github.write = faulty
                        worker.tick()
                        service.github.write = original
                        if name in {"rejected_write_recovery", "ambiguous_write_recovery"}:
                            result = client.post(
                                f"/cases/{cid}/recover", headers=headers, json={"version": 1}
                            )
                            assert result.status_code == 200, result.text
                            worker.tick()
                        assert (
                            client.get(f"/cases/{cid}", headers=headers).json()["status"]
                            == "WAITING_REPORTER"
                        )
                        if name == "reporter_resume":
                            assert (
                                client.post(
                                    f"/cases/{cid}/demo-reply",
                                    headers=headers,
                                    json={"body": "Version: 1.95\nReproduction: save draft"},
                                ).status_code
                                == 200
                            )
                            worker.tick()
                            detail = client.get(f"/cases/{cid}", headers=headers).json()
                            assert detail["id"] == cid and detail["version"] == 2
                            assert detail["status"] == "REVIEW" and not detail["decision"]
                            assert detail["proposal"]["value"]["missing_fields"] == []
                            assert detail["proposal"]["rounds"] == 1
                        if name == "reordered_reply":
                            for _ in range(2):
                                assert (
                                    client.post(
                                        f"/cases/{cid}/refresh-replies", headers=headers
                                    ).status_code
                                    == 200
                                )
                                worker.tick()
                            detail = client.get(f"/cases/{cid}", headers=headers).json()
                            assert detail["status"] == "WAITING_REPORTER" and detail["version"] == 1
                    with service.sessions() as session:
                        comments = session.get(DemoIssue, 1).comments
                        if name in [
                            "no_approval",
                            "duplicate_event",
                            "read_failure_recovery",
                            "source_change",
                            "policy_change",
                        ]:
                            assert not comments
                        else:
                            assert len(comments) == 1
                            assert all(a.status == "DONE" for a in session.scalars(select(Action)))
                    r["passed"] = True
        except Exception as exc:
            r["error"] = type(exc).__name__ + ": " + str(exc)[:250]
        r["latency_ms"] = (time.perf_counter() - start) * 1000
        results.append(r)
    # A recreated in-memory checkpointer cannot recover either waiting boundary.
    from langgraph.checkpoint.memory import InMemorySaver

    control = {}
    for boundary in ["human_review", "reporter_wait"]:
        try:
            config = {"configurable": {"thread_id": "control-" + boundary}}
            g = Analyst(settings, policies).graph(InMemorySaver())
            state = g.invoke(
                {"issue": {"title": "Editor crash", "body": ""}, "label_catalog": labels}, config
            )
            if boundary == "reporter_wait":
                g.invoke(Command(resume={"approved": True, "proposal": state["proposal"]}), config)
                g.invoke(Command(resume={"state": "COMPLETED"}), config)
            before = g.get_state(config).next == (boundary,)
            restarted = Analyst(settings, policies).graph(InMemorySaver())
            control[boundary] = {
                "reached_boundary": before,
                "recovered_after_restart": bool(restarted.get_state(config).values),
            }
        except Exception as exc:
            control[boundary] = {"error": type(exc).__name__, "recovered_after_restart": None}
    allowed = {
        ((), ("human_review",)),
        (("human_review",), ("execution_wait",)),
        (("human_review",), ()),
        (("execution_wait",), ("reporter_wait",)),
        (("execution_wait",), ()),
        (("reporter_wait",), ("human_review",)),
    }
    transitions = []
    for r in results:
        previous = ()
        if "trace" not in r:
            continue
        valid = True
        for step in r["trace"]:
            valid &= (previous, tuple(step)) in allowed
            previous = tuple(step)
        transitions.append(valid)
    passed = sum(r["passed"] for r in results)
    return save_report(
        out,
        {
            **provenance(settings, "B2", settings.engine),
            "metrics": {
                "episode_success_rate": ratio(passed, len(results)),
                "write_counters": counters,
                "transition_conformance": {
                    "value": ratio(sum(transitions), len(transitions)),
                    "graph_episodes": len(transitions),
                },
                "checkpoint_control": control,
                "bounded_termination": {
                    "value": ratio(sum(r["passed"] for r in results[:8]), 8),
                    "episodes": 8,
                    "definition": "reaches the specified terminal or waiting boundary within scripted budget",
                },
                "passed": passed,
                "total": len(results),
                "restart_scenarios": {
                    r["id"]: r["passed"] for r in results if r["id"].startswith("restart")
                },
                "authorization_guard_passed": next(
                    r["passed"] for r in results if r["id"] == "no_approval"
                ),
                "idempotency_passed": next(
                    r["passed"] for r in results if r["id"] == "duplicate_event"
                ),
                "recovery_passed": {r["id"]: r["passed"] for r in results if "recovery" in r["id"]},
                "bounded_rounds_passed": next(
                    r["passed"] for r in results if r["id"] == "round_budget"
                ),
            },
            "results": results,
            "limits": [
                "Scripted reviewer and reporter, no measured human productivity",
                "Execution receipts in graph-only episodes are simulated; ledger episodes use real executor with fault-injected adapter",
                "Passing scenarios do not establish exactly-once delivery for all networks",
            ],
        },
    )
