"""Operate IssuePilot from the directory containing this file; Python 3.12+, no host dependencies."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

APP = Path(__file__).resolve().parent / "issuepilot"
sys.path.insert(0, str(APP / "src"))
from issuepilot.local_workspace import Workspace  # noqa: E402


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="command", required=True)
    start = sub.add_parser(
        "start", help="Start the API, database and worker; reuse your existing n8n."
    )
    start.add_argument("--workflow", help="Local n8n editor URL; prompted once on first use.")
    start.add_argument(
        "--source", type=Path, help="Local pinned source files instead of downloading."
    )
    configure = sub.add_parser("configure", help="Select a different n8n workflow.")
    configure.add_argument("--workflow", required=True)
    for cmd, desc in {
        "workflow": "Copy intake nodes for the existing n8n canvas.",
        "check": "Verify the signed webhook without creating a case.",
        "status": "Show intake, model and human review counts.",
        "reviewer": "Copy the reviewer token privately on Windows.",
        "settings-path": "Print the workspace settings path without reading secrets.",
        "export": "Export event metadata locally.",
        "check-snowflake": "Verify the service role, destination and table read access.",
        "sync": "Verify the event mirror in Snowflake.",
        "stop": "Stop the workspace without removing data.",
        "logs": "Inspect workspace diagnostics.",
        "pause": "Pause the model worker.",
        "resume": "Resume the model worker.",
    }.items():
        sub.add_parser(cmd, help=desc)
    verify = sub.add_parser("verify-model", help="Run real predictions with the installed model.")
    verify.add_argument("--require-gpu", action="store_true", help="Also require GPU offload.")
    submit = sub.add_parser(
        "submit", help="Submit the complete source through n8n and verify stored deliveries."
    )
    submit.add_argument(
        "--resume", action="store_true", help="Skip already confirmed case deliveries."
    )
    for cmd in ("prepare", "evaluation-data"):
        p = sub.add_parser(
            cmd,
            help="Verify source data"
            + (
                " and build the classification scoring profile."
                if cmd == "evaluation-data"
                else "."
            ),
        )
        p.add_argument("--source", type=Path)
    args = cli.parse_args(argv)
    if getattr(args, "source", None) and not args.source.exists():
        cli.error("Source path does not exist")
    state = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share") / "IssuePilot"
    Workspace(APP, state).operate(
        args.command,
        editor_url=getattr(args, "workflow", None),
        source=args.source.resolve() if getattr(args, "source", None) else None,
        resume=getattr(args, "resume", False),
        require_gpu=getattr(args, "require_gpu", False),
    )


def run(argv=None):
    try:
        main(argv)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        sys.exit("Stopped: " + str(exc))


if __name__ == "__main__":
    run()
