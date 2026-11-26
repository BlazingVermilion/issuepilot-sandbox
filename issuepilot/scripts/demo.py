"""Windows-friendly launcher for a completely separate dataset replay stack (stdlib only)."""

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from issuepilot import replay  # noqa: E402


def prepare_files(source):
    result = replay.prepare(
        source, ROOT / "fixtures/dataset_source.json", ROOT / "data/replay/issues.jsonl"
    )
    private = ROOT / ".env.replay"
    if not private.exists():
        text = (ROOT / ".env.example").read_text(encoding="utf8")
        for old, new in {
            "GENERATE_WEBHOOK_SECRET": secrets.token_hex(32),
            "GENERATE_REVIEWER_TOKEN": secrets.token_urlsafe(40),
            "GENERATE_DATABASE_PASSWORD": secrets.token_hex(24),
            "GENERATE_N8N_ENCRYPTION_KEY": secrets.token_hex(32),
            "IP_REPO=demo/issuepilot-sandbox": "IP_REPO=" + replay.REPO,
        }.items():
            text = text.replace(old, new)
        with private.open("x", encoding="utf8") as f:
            f.write(text + "\nIP_DATASET_REPLAY=true\nIP_REPLAY_RUN_ID=" + str(uuid4()) + "\n")
        private.chmod(0o600)
    for folder in ("secrets", "artifacts/replay"):
        (ROOT / folder).mkdir(parents=True, exist_ok=True)
    print(json.dumps(result, indent=2))
    print(
        "Review token: read the maintainer value in .env.replay locally. Existing secrets were preserved."
    )


def compose(*args):
    if not (ROOT / ".env.replay").is_file() or not (ROOT / "data/replay/issues.jsonl").is_file():
        raise SystemExit(
            "Fetch the pinned issues with ai-bench/bench.py fetch, then run scripts/demo.py prepare"
        )
    env = dict(os.environ)
    # Compose shell overrides should not combine live DB credentials/name with this project's volumes.
    for key in ("POSTGRES_PASSWORD", "N8N_ENCRYPTION_KEY", "N8N_IMAGE", "COMPOSE_PROFILES"):
        env.pop(key, None)
    env.update(
        IP_APP_ENV_FILE=".env.replay",
        IP_REPLAY_DATA_DIR=(ROOT / "data/replay").as_posix(),
        IP_API_PORT="8011",
        IP_N8N_PORT="5688",
        ISSUEPILOT_IMAGE="issuepilot-replay:0.5.4",
    )
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.replay",
            "-p",
            "issuepilot-replay",
            "-f",
            "compose.yaml",
            "-f",
            "compose.replay.yaml",
            "--profile",
            "automation",
            *args,
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("prepare").add_argument(
        "--source",
        type=Path,
        default=ROOT.parent / "ai-bench/data",
        help="Pinned split files (default: sibling ai-bench/data after bench.py fetch), extracted benchmark, or original ZIP.",
    )
    for name in (
        "up",
        "smoke",
        "pilot",
        "all",
        "status",
        "pause",
        "resume",
        "logs",
        "stop",
        "export",
        "sync",
    ):
        sub.add_parser(name)
    a = parser.parse_args()
    if a.action == "prepare":
        prepare_files(a.source)
    elif a.action == "up":
        compose("config", "--quiet")
        compose("build", "api")
        compose("up", "-d", "--no-build", "--wait", "postgres", "api", "worker", "n8n")
        print("IssuePilot: http://localhost:8011 | n8n: http://localhost:5688")
    elif a.action == "smoke":
        compose("exec", "-T", "worker", "python", "scripts/check_ai.py", "--require-gpu")
    elif a.action in {"pilot", "all", "status"}:
        options = {
            "pilot": ["send", "--limit", "3", "--probe-duplicate"],
            "all": ["send", "--all"],
            "status": ["status"],
        }[a.action]
        compose("exec", "-T", "api", "python", "scripts/dataset_run.py", *options)
    elif a.action == "pause":
        compose("stop", "worker")
    elif a.action == "resume":
        compose("up", "-d", "--wait", "worker")
    elif a.action == "logs":
        compose("logs", "--tail", "60", "api", "worker", "n8n")
    elif a.action == "stop":
        compose("stop")
    else:
        # Local export needs no Snowflake installation, credentials or warehouse.
        if a.action == "export":
            compose("exec", "-T", "api", "python", "scripts/sync_snowflake.py", "--export-only")
        else:
            if not (ROOT / "secrets/snowflake.p8").is_file():
                raise SystemExit(
                    "Configure the key file secrets/snowflake.p8 and the Snowflake session variables first"
                )
            compose(
                "--profile",
                "analytics",
                "run",
                "--rm",
                "--no-deps",
                "--build",
                "analytics",
                "python",
                "scripts/sync_snowflake.py",
            )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from None
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
