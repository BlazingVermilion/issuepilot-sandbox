"""Start and operate an issue workspace without taking ownership of the user's n8n."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from uuid import UUID, uuid4
from . import issue_source, n8n_workflow, issue_intake
from .intake_protocol import WorkflowTarget
from .source_preparation import prepare_source
from .workspace_compat import HISTORICAL_REPOSITORY, identify_existing_store
from .workspace_files import read_settings, write_private_once, atomic_write

DEFAULT_PROJECT = "issuepilot-workspace"


@dataclass
class Workspace:
    app: Path
    state: Path

    @property
    def folder(self):
        return self.state / "workspace"

    @property
    def settings_file(self):
        return self.folder / "settings.env"

    def settings(self):
        if not self.settings_file.is_file():
            raise ValueError("Start the workspace with manage.py start first.")
        values = read_settings(self.settings_file.read_bytes())
        validate_settings(values)
        return values

    def initialize(self, editor_url=None):
        if self.settings_file.exists():
            values = self.settings()
            if (
                editor_url
                and WorkflowTarget.from_editor_url(editor_url).editor_url
                != values["IP_N8N_EDITOR_URL"]
            ):
                raise ValueError(
                    "This workspace already has a configured workflow. Use manage.py configure --workflow URL to change it deliberately."
                )
            return values
        if not editor_url:
            if not sys.stdin.isatty():
                raise ValueError("For unattended setup, supply start --workflow URL.")
            editor_url = input("Paste the editor URL of your existing n8n workflow: ").strip()
        target = WorkflowTarget.from_editor_url(editor_url)
        raw = n8n_workflow.run_docker(
            "volume",
            "ls",
            "--filter",
            "label=com.docker.compose.volume=postgres_data",
            "--format",
            '{{.Label "com.docker.compose.project"}}',
        )
        previous = identify_existing_store(self.state, set(raw.splitlines()), DEFAULT_PROJECT)
        values = {
            "IP_MODE": "demo",
            "IP_ENGINE": "ollama",
            "IP_MODEL": "qwen3:8b",
            "IP_AI_PROFILE": "selected",
            "IP_SNAPSHOT_INTAKE": "true",
            "IP_REPO": issue_source.SANDBOX_REPOSITORY,
            "IP_WRITES_ENABLED": "false",
            "IP_GITHUB_TOKEN": "",
            "IP_POLICY_DIR": "policies",
            "IP_OLLAMA_URL": "http://host.docker.internal:11434",
            "IP_WORKSPACE_ID": str(uuid4()),
            "IP_COMPOSE_PROJECT": DEFAULT_PROJECT,
            "IP_N8N_WORKFLOW_ID": target.workflow_id,
            "IP_N8N_EDITOR_URL": target.editor_url,
            "IP_WEBHOOK_SECRET": secrets.token_hex(32),
            "IP_REVIEWER_TOKENS": json.dumps(
                {"maintainer": secrets.token_urlsafe(40)}, separators=(",", ":")
            ),
            "POSTGRES_PASSWORD": secrets.token_hex(24),
            "N8N_ENCRYPTION_KEY": secrets.token_hex(32),
        }
        template = read_settings((self.app / ".env.example").read_bytes())
        values["N8N_IMAGE"] = template["N8N_IMAGE"]
        if previous:
            project, keys, projection = previous
            values.update(keys, IP_COMPOSE_PROJECT=project)
            if projection.is_file():
                issue_source.load_snapshots(str(projection))
                dest = self.folder / "projection/issues.jsonl"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(projection, dest)
                if os.name == "posix":
                    dest.chmod(0o644)
            print("Reusing the existing PostgreSQL workspace and its delivery identities.")
        validate_settings(values)
        write_private_once(self.settings_file, encode_settings(values))
        return values

    def compose(self, values, args, *, capture=False):
        env = os.environ.copy()
        for key in (
            "COMPOSE_PROFILES",
            "POSTGRES_PASSWORD",
            "N8N_IMAGE",
            "N8N_ENCRYPTION_KEY",
            "IP_REPO",
            "IP_N8N_WORKFLOW_ID",
            "IP_API_PORT",
        ):
            env.pop(key, None)
        artifacts = self.folder / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        env.update(
            IP_APP_ENV_FILE=self.settings_file.as_posix(),
            IP_SNAPSHOT_DIR=(self.folder / "projection").as_posix(),
            IP_WORKSPACE_ARTIFACTS=artifacts.as_posix(),
            IP_API_PORT="8021",
            ISSUEPILOT_IMAGE="issuepilot-workspace:local",
        )
        cmd = [
            "docker",
            "compose",
            "--env-file",
            str(self.settings_file),
            "-p",
            values["IP_COMPOSE_PROJECT"],
            "-f",
            "compose.yaml",
            "-f",
            "compose.intake.yaml",
            *args,
        ]
        result = subprocess.run(
            cmd, cwd=self.app, env=env, check=False, capture_output=capture, text=True
        )
        if result.returncode:
            if args[:2] == ["--profile", "analytics"]:
                raise ValueError(
                    "Snowflake command did not finish. See the analytics output above."
                )
            raise ValueError(f"Docker Compose '{args[0]}' failed. Inspect: manage.py logs")
        return (result.stdout or "").strip() if capture else None

    def connect_workflow(self, values):
        api_id = self.compose(values, ["ps", "-q", "api"], capture=True)
        if not api_id:
            raise ValueError("The API is not running. Use manage.py start.")
        target = WorkflowTarget.from_editor_url(values["IP_N8N_EDITOR_URL"])
        n8n_workflow.configure_workflow(
            self.folder, api_id, self.app / "n8n/issue-intake.json", target
        )
        return self.folder / n8n_workflow.NODE_FILE

    def operate(self, command, *, editor_url=None, source=None, resume=False):
        if command in {"prepare", "evaluation-data"}:
            prepare_source(self.app, self.folder, source, merged=command == "evaluation-data")
            return
        values = self.initialize(editor_url) if command == "start" else self.settings()
        if command == "configure":
            target = WorkflowTarget.from_editor_url(editor_url or "")
            values.update(
                IP_N8N_EDITOR_URL=target.editor_url, IP_N8N_WORKFLOW_ID=target.workflow_id
            )
            validate_settings(values)
            atomic_write(self.settings_file, encode_settings(values))
            print("Workflow updated. Run manage.py start, then install the intake nodes.")
            return
        if command == "start":
            prepare_source(self.app, self.folder, source)
            for args in (
                ["config", "--quiet"],
                ["build", "api"],
                ["up", "-d", "--no-build", "--wait", "postgres", "api", "worker"],
            ):
                self.compose(values, args)
            self.connect_workflow(values)
            print(
                "IssuePilot: http://localhost:8021\nInstall the workflow nodes once with manage.py workflow; then use manage.py check and manage.py submit."
            )
        elif command == "workflow":
            f = self.connect_workflow(values)
            copy_for_user(f.read_text(encoding="ascii"), description="workflow nodes", fallback=f)
        elif command == "reviewer":
            tokens = json.loads(values["IP_REVIEWER_TOKENS"])
            copy_for_user(
                tokens.get("maintainer") or next(iter(tokens.values())),
                description="reviewer token",
                fallback=self.settings_file,
            )
        elif command == "check":
            print(json.dumps(issue_intake.verify_intake(values), indent=2))
        elif command == "submit":
            issue_intake.submit_issues(values, self.folder, resume=resume)
        elif command == "export":
            dest = self.folder / "artifacts/analytics/events.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.compose(
                values,
                [
                    "exec",
                    "-T",
                    "api",
                    "python",
                    "scripts/sync_snowflake.py",
                    "--export-only",
                    "--out",
                    "/tmp/issuepilot-events.json",
                ],
            )
            self.compose(values, ["cp", "api:/tmp/issuepilot-events.json", str(dest)])
            print("Event export:", dest)
        elif command in {"check-snowflake", "sync"}:
            if not (self.app / "secrets/snowflake.p8").is_file():
                raise ValueError(
                    "Configure a Snowflake key before syncing. See the operator guide."
                )
            self.compose(
                values,
                [
                    "--profile",
                    "analytics",
                    "run",
                    "--rm",
                    "--no-deps",
                    "--build",
                    "analytics",
                    "python",
                    "scripts/sync_snowflake.py",
                    *(
                        ["--check-context"]
                        if command == "check-snowflake"
                        else ["--out", "/tmp/issuepilot-events.json"]
                    ),
                ],
            )
        else:
            steps = {
                "status": ["exec", "-T", "api", "python", "scripts/intake_status.py"],
                "logs": ["logs", "--tail", "45", "api", "postgres", "worker"],
                "stop": ["stop", "worker", "api", "postgres"],
                "verify-model": [
                    "exec",
                    "-T",
                    "worker",
                    "python",
                    "scripts/check_ai.py",
                    "--require-gpu",
                ],
                "pause": ["stop", "worker"],
                "resume": ["up", "-d", "--wait", "worker"],
            }
            self.compose(values, steps[command])


def encode_settings(values: dict) -> bytes:
    if any("\n" in str(v) or "\r" in str(v) for v in values.values()):
        raise ValueError("Workspace settings must be single-line literals")
    return "".join(f"{k}={v}\n" for k, v in values.items()).encode()


def validate_settings(values):
    if not (
        values.get("IP_SNAPSHOT_INTAKE") == "true"
        and values.get("IP_WRITES_ENABLED") == "false"
        and values.get("IP_REPO") in {issue_source.SANDBOX_REPOSITORY, HISTORICAL_REPOSITORY}
        and values.get("IP_MODE") == "demo"
        and values.get("IP_ENGINE") == "ollama"
        and not values.get("IP_GITHUB_TOKEN")
        and all(
            values.get(k)
            for k in (
                "IP_WEBHOOK_SECRET",
                "IP_REVIEWER_TOKENS",
                "POSTGRES_PASSWORD",
                "N8N_ENCRYPTION_KEY",
            )
        )
        and re.fullmatch(r"issuepilot[-a-z0-9_]*", values.get("IP_COMPOSE_PROJECT", ""))
    ):
        raise ValueError(
            "Not a valid snapshot workspace configuration. Existing data was not changed."
        )
    UUID(values["IP_WORKSPACE_ID"])
    if (
        WorkflowTarget.from_editor_url(values["IP_N8N_EDITOR_URL"]).workflow_id
        != values["IP_N8N_WORKFLOW_ID"]
    ):
        raise ValueError("The configured n8n workflow ID does not match its editor URL")


def copy_for_user(text, *, description, fallback):
    if os.name == "nt":
        # JSON is ASCII-escaped and generated tokens are URL-safe. Do not add a BOM to a node payload.
        data = text.encode("ascii") if text.isascii() else text.encode("utf-16")
        result = subprocess.run(["clip.exe"], input=data, capture_output=True)
        if result.returncode == 0:
            print(f"Copied {description} to the clipboard.")
            return
    print(f"Read the {description} locally from {fallback}.")
