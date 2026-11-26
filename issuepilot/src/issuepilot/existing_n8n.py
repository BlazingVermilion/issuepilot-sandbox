"""Host-side replay over the existing n8n instance; only Python's standard library is needed."""

import hashlib
import hmac
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, NAMESPACE_URL, uuid5

from . import replay, replay_link as link

NETWORK = "issuepilot-replay-5678-link"
OWNER = "io.issuepilot.purpose"
ALIAS = "issuepilot-replay-api-5678"
BRANCH_FILE = "workflow-nodes-5678.json"


class RouteNotReady(ValueError):
    """A signed read-only probe may be repeated while n8n registers its webhook."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def json_request(url, raw=None, headers=None):
    if url not in {link.WEBHOOK, link.API + "/replay/receipts"}:
        raise ValueError("Only the two fixed loopback destinations are permitted")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(Request(url, raw, headers or {}), timeout=30) as response:
            body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise ValueError("Response exceeds the receipt limit")
            return json.loads(body)
    except HTTPError as exc:
        error = RouteNotReady if exc.code in {404, 502, 503, 504} else ValueError
        raise error(
            f"{url} returned HTTP {exc.code}. Check the published path, callback and last node in n8n Executions."
        ) from None
    except (URLError, TimeoutError, UnicodeError, json.JSONDecodeError):
        raise ValueError(
            f"No valid JSON receipt from {url}. Check the running services, last node and callback."
        ) from None


def docker(*args):
    done = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf8",
        errors="replace",
        timeout=30,
    )
    if done.returncode:
        raise ValueError(f"Docker {args[0]} failed: {done.stderr.strip()[:400]}")
    return done.stdout.strip()


def published_container(run):
    found = []
    for line in run("ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Image}}").splitlines():
        cid, name, image = line.split("\t")
        ports = json.loads(run("inspect", "--format", "{{json .NetworkSettings.Ports}}", cid)) or {}
        if any(
            b.get("HostPort") == "5678"
            and b.get("HostIp") in {"", "127.0.0.1", "0.0.0.0", "::", "::1"}
            for port, binds in ports.items()
            if port.endswith("/tcp")
            for b in binds or []
        ):
            if "n8n" not in (name + " " + image).lower():
                raise ValueError(
                    f"{name} publishes 5678 but is not recognisable as n8n. Check docker ps; no connection was made."
                )
            found.append(cid)
    if len(found) > 1:
        raise ValueError(
            "Multiple Docker containers publish port 5678. Identify the actual n8n before linking."
        )
    return next(iter(found), None)


def configure(folder, api_id, template, run=docker):
    cid = published_container(run)
    if cid:
        # A dedicated internal bridge adds no host port and no default Internet gateway.
        # Never add the alias 'api' or disconnect a network used by the original workflow.
        ids = run("network", "ls", "--format", "{{.Name}}").splitlines()
        if NETWORK not in ids:
            run(
                "network",
                "create",
                "--driver",
                "bridge",
                "--internal",
                "--label",
                OWNER + "=external-n8n-5678",
                NETWORK,
            )
        props = json.loads(run("network", "inspect", NETWORK))[0]
        if (
            props.get("Driver") != "bridge"
            or props.get("Internal") is not True
            or (props.get("Labels") or {}).get(OWNER) != "external-n8n-5678"
        ):
            raise ValueError(
                "The link network name is in use by another configuration; nothing was attached."
            )
        nodes = props.get("Containers") or {}
        full_api = run("inspect", "--format", "{{.Id}}", api_id)
        full_n8n = run("inspect", "--format", "{{.Id}}", cid)
        if set(nodes) - {full_n8n, full_api}:
            raise ValueError(
                "An unexpected container already uses the private replay link; inspect docker network inspect "
                + NETWORK
            )
        if full_api not in nodes:
            run("network", "connect", "--alias", ALIAS, NETWORK, full_api)
        if full_n8n not in nodes:
            run("network", "connect", NETWORK, full_n8n)
        callback = f"http://{ALIAS}:8000" + link.CALLBACK
        runtime = "Docker"
    else:
        callback = link.API + link.CALLBACK
        runtime = "Windows/host (no Docker publisher on 5678)"
    bundle = branch(template, callback)
    path = folder / BRANCH_FILE
    path.write_text(json.dumps(bundle, indent=2, ensure_ascii=True), encoding="ascii")
    print(
        f"Existing n8n: {link.EDITOR}\nDetected: {runtime}\nReplay callback: {callback}\nNodes for Ctrl+V: {path}"
    )


def branch(template, callback):
    template = json.loads(Path(template).read_bytes())
    nodes = template["nodes"]
    names = {
        n["name"]: v
        for n, v in zip(
            nodes,
            ["Replay 1993 - intake", "Replay 1993 - signed bytes", "Replay 1993 - receipt"],
            strict=True,
        )
    }
    conns = {}
    for old, outputs in template["connections"].items():
        for lane in outputs["main"]:
            for edge in lane:
                edge["node"] = names[edge["node"]]
        conns[names[old]] = outputs
    for n in nodes:
        n["name"] = names[n["name"]]
        n["id"] = str(uuid5(NAMESPACE_URL, link.EDITOR + "/" + n["name"]))
        n["position"][1] += 400
    nodes[0]["webhookId"] = str(uuid5(NAMESPACE_URL, link.EDITOR + "/" + link.PATH))
    nodes[0]["parameters"]["path"] = link.PATH
    nodes[-1]["parameters"].update(
        url=callback,
        sendHeaders=True,
        headerParameters={
            "parameters": [
                {"name": "X-IssuePilot-Workflow", "value": "={{ $workflow.id }}"},
                {"name": "X-IssuePilot-Execution", "value": "={{ String($execution.id) }}"},
            ]
        },
    )
    return {"nodes": nodes, "connections": conns, "pinData": {}}


def checked(values, request=json_request):
    secret, run_id = values["IP_WEBHOOK_SECRET"], values["IP_REPLAY_RUN_ID"]
    nonce, raw, headers = link.probe(secret, run_id)
    for delay in (1, 2, 3, 0):
        try:
            r = request(link.WEBHOOK, raw, headers)
            break
        except RouteNotReady:
            if not delay:
                raise
            time.sleep(delay)
    if not isinstance(r, dict) or r.get("state") != "REPLAY_READY" or r.get("run_id") != run_id:
        raise ValueError(
            "Wrong workflow/instance or missing backend receipt. Publish the three replay nodes and return the last node's JSON."
        )
    meta = link.route(r.get("n8n_workflow_id"), r.get("n8n_execution_id"))
    if not hmac.compare_digest(
        str(r.get("proof", "")), link.reply_tag(secret, run_id, nonce, meta)
    ):
        raise ValueError("The signed route proof did not match. No issues sent.")
    return {"state": "REPLAY_READY", "run_id": run_id, **meta}


def submit(values, folder, *, resume=False, request=json_request, progress=print):
    route = checked(values, request)
    run_id = route["run_id"]
    secret = values["IP_WEBHOOK_SECRET"]
    reviewers = json.loads(values["IP_REVIEWER_TOKENS"])
    if not isinstance(reviewers, dict) or not reviewers:
        raise ValueError(
            "The replay config has no reviewer for the authenticated database readback"
        )
    reviewer = reviewers.get("maintainer") or next(iter(reviewers.values()))
    if not isinstance(reviewer, str) or not reviewer:
        raise ValueError("A reviewer token is required for database readback")

    def readback():
        data = request(link.API + "/replay/receipts", None, {"Authorization": "Bearer " + reviewer})
        if (
            not isinstance(data, dict)
            or data.get("run_id") != run_id
            or not isinstance(data.get("receipts"), dict)
        ):
            raise ValueError("Cannot verify this cohort in the local replay database")
        return data["receipts"]

    rows = replay.snapshots(str(folder / "projection/issues.jsonl"))
    if len(rows) != replay.TOTAL:
        raise ValueError("Incomplete pinned replay corpus")
    numbers = sorted(rows, key=lambda n: {"test": 0, "validation": 1, "train": 2}[rows[n]["split"]])
    before, expected = readback(), {}
    accepted = duplicate = skipped = 0
    first = last = None
    digest = hashlib.sha256()
    for i, number in enumerate(numbers, 1):
        key = replay.delivery_id(number)
        try:
            if resume and key in before:
                cid = before[key]
                skipped += 1
            else:
                raw, headers = replay.signed(number, secret)
                answer = request(link.WEBHOOK, raw, headers)
                if (
                    not isinstance(answer, dict)
                    or answer.get("run_id") != run_id
                    or answer.get("state") not in {"ACCEPTED", "DUPLICATE"}
                ):
                    raise ValueError("Invalid n8n issue receipt or wrong cohort")
                meta = link.route(answer.get("n8n_workflow_id"), answer.get("n8n_execution_id"))
                cid = str(UUID(str(answer.get("case_id"))))
                record = {"issue": number, "case_id": cid, "state": answer["state"], **meta}
                first = first or record
                last = record
                accepted += int(answer["state"] == "ACCEPTED")
                duplicate += int(answer["state"] == "DUPLICATE")
                digest.update((replay.compact(record) + "\n").encode())
            expected[key] = cid
            if i == 1 or i % 50 == 0 or i == len(numbers):
                got = readback()
                if any(got.get(k) != v for k, v in expected.items()):
                    raise ValueError("A receipt does not match PostgreSQL")
                progress(
                    f"Verified {i}/{len(numbers)} | #{number} | accepted={accepted} duplicate={duplicate} skipped={skipped}",
                    flush=True,
                )
        except (ValueError, OSError) as exc:
            raise ValueError(
                f"At #{number} ({i}/{len(numbers)}): {exc}. After fixing the route use: py -3.12 demo.py all --resume"
            ) from None
    if len(set(expected.values())) != len(numbers):
        raise ValueError("Distinct issue numbers do not map to distinct cases")
    report = {
        "state": "INTAKE_VERIFIED",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "editor": link.EDITOR,
        "webhook": link.WEBHOOK,
        "run_id": run_id,
        "revision": replay.REVISION,
        "durable_cases": len(expected),
        "posted_this_command": accepted + duplicate,
        "accepted": accepted,
        "duplicate": duplicate,
        "skipped_previously_stored": skipped,
        "first_execution": first,
        "last_execution": last,
        "receipts_this_command_sha256": digest.hexdigest(),
    }
    path = folder / "artifacts" / "intake"
    path.mkdir(parents=True, exist_ok=True)
    file = path / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    file.write_text(json.dumps(report, indent=2), encoding="utf8")
    progress(json.dumps(report, indent=2) + "\nEvidence file: " + str(file))
    return report
