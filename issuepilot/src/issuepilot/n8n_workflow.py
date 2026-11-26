"""Connect an existing n8n runtime and render its issue intake nodes."""

import json
import subprocess
from pathlib import Path
from . import intake_protocol as protocol
from .workspace_files import atomic_write

NETWORK = "issuepilot-webhooks"
OWNER = "io.issuepilot.purpose"
ALIAS = "issuepilot-intake-api"
NODE_FILE = "issue-intake.json"


def run_docker(*args):
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


def find_n8n_container(port, run):
    found = []
    for line in run("ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Image}}").splitlines():
        cid, name, image = line.split("\t")
        ports = json.loads(run("inspect", "--format", "{{json .NetworkSettings.Ports}}", cid)) or {}
        if any(
            b.get("HostPort") == str(port)
            and b.get("HostIp") in {"", "127.0.0.1", "0.0.0.0", "::", "::1"}
            for container_port, binds in ports.items()
            if container_port.endswith("/tcp")
            for b in binds or []
        ):
            if "n8n" not in (name + " " + image).lower():
                raise ValueError(
                    f"{name} publishes {port} but is not recognisable as n8n. Check docker ps; no connection was made."
                )
            found.append(cid)
    if len(found) > 1:
        raise ValueError(
            f"Multiple Docker containers publish port {port}. Identify the actual n8n before linking."
        )
    return next(iter(found), None)


def configure_workflow(folder, api_id, template, target, run=run_docker):
    cid = find_n8n_container(target.port, run)
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
                OWNER + "=issue-intake",
                NETWORK,
            )
        props = json.loads(run("network", "inspect", NETWORK))[0]
        if (
            props.get("Driver") != "bridge"
            or props.get("Internal") is not True
            or (props.get("Labels") or {}).get(OWNER) != "issue-intake"
        ):
            raise ValueError(
                "The link network name is in use by another configuration; nothing was attached."
            )
        nodes = props.get("Containers") or {}
        full_api = run("inspect", "--format", "{{.Id}}", api_id)
        full_n8n = run("inspect", "--format", "{{.Id}}", cid)
        if set(nodes) - {full_n8n, full_api}:
            raise ValueError(
                "An unexpected container already uses the intake network; inspect docker network inspect "
                + NETWORK
            )
        if full_api not in nodes:
            run("network", "connect", "--alias", ALIAS, NETWORK, full_api)
        if full_n8n not in nodes:
            run("network", "connect", NETWORK, full_n8n)
        callback = f"http://{ALIAS}:8000" + protocol.CALLBACK_PATH
        runtime = "Docker"
    else:
        callback = protocol.API_BASE + protocol.CALLBACK_PATH
        runtime = "host"
    bundle = workflow_nodes(template, callback)
    path = folder / NODE_FILE
    atomic_write(path, json.dumps(bundle, indent=2, ensure_ascii=True).encode("ascii"))
    print(f"Existing n8n: {target.editor_url}\nDetected: {runtime}\nAPI callback: {callback}")


def workflow_nodes(template: Path, callback: str) -> dict:
    """Use the reviewed workflow as-is; only the backend address varies by runtime."""
    workflow = json.loads(template.read_bytes())
    nodes = workflow["nodes"]
    (webhook,) = (node for node in nodes if node["type"] == "n8n-nodes-base.webhook")
    (submit,) = (node for node in nodes if node["type"] == "n8n-nodes-base.httpRequest")
    if webhook["parameters"]["path"] != protocol.WEBHOOK_PATH:
        raise ValueError("Workflow and sender do not agree on the intake path")
    submit["parameters"]["url"] = callback
    return {"nodes": nodes, "connections": workflow["connections"], "pinData": {}}
