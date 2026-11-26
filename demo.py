"""Run the 1,993-issue sandbox from a Git checkout (Python 3.12, no host pip install)."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import tomllib
from urllib.request import urlopen
from uuid import UUID, uuid4

ORIGINAL = "issuepilot-replay"
FRESH = "issuepilot-replay-full1993"
APP = Path(__file__).resolve().parent / "issuepilot"
sys.path.insert(0, str(APP / "src"))
from issuepilot import replay, existing_n8n  # noqa: E402


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        pending = Path(tmp.name)
    try:
        pending.write_bytes(data)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def prepare_dataset(root, state, source=None, merged=False):
    manifest = root / "fixtures/dataset_source.json"
    spec = json.loads(manifest.read_bytes())
    if (spec["repo"], spec["revision"], spec["splits"]) != (
        replay.DATASET,
        replay.REVISION,
        replay.SIZES,
    ):
        fail("Dataset manifest differs from this release.")
    # Standard Compose may have created issuepilot/data as root. The replay
    # projection belongs to the same per-user instance as its database credentials.
    instance = state / FRESH
    instance.mkdir(parents=True, mode=0o700, exist_ok=True)
    dest = instance / "projection/issues.jsonl"
    if not source and not merged and dest.is_file():
        rows = replay.snapshots(str(dest))
        print(f"Dataset ready: {len(rows)} verified issues.")
        return
    if source is None:
        source = root.parent / "ai-bench/data"
        for split in replay.SIZES:
            path = source / f"{split}.jsonl"
            if path.exists():
                continue  # The projection builder verifies cached bytes too.
            print(f"Downloading pinned {split} split...", flush=True)
            url = f"https://huggingface.co/datasets/{replay.DATASET}/resolve/{replay.REVISION}/{split}.jsonl"
            with urlopen(url, timeout=90) as response:
                data = response.read(12_000_001)
            if len(data) > 12_000_000 or hashlib.sha256(data).hexdigest() != spec["sha256"][split]:
                fail(f"Checksum mismatch for {split}; the download was not saved.")
            atomic_bytes(path, data)
    report = replay.prepare(source, manifest, dest)
    if os.name == "posix":
        # Only the public, gold-free leaf is bound into the non-root container.
        dest.parent.chmod(0o755)
        dest.chmod(0o644)
    print(f"Dataset ready: {report['unique_issues']} verified issues (gold excluded from replay).")
    if merged:
        # This folder is deliberately outside every app/worker volume.
        folder = root.parent / "ai-bench/data/full-corpus"
        rows = []
        for split in replay.SIZES:
            for line in replay.verified_split(source, split, spec).splitlines():
                row = json.loads(line)
                sample = {k: row[k] for k in ("issue_number", "input", "label")}
                sample["source_split"] = split
                sample["created_at"] = row["created_at"]
                rows.append(json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n")
        atomic_bytes(folder / "all-1993.jsonl", "".join(rows).encode())
        profile = {
            "classification": {
                "splits": {"test": "all-1993.jsonl"},
                "fields": {"id": "issue_number", "text": "input", "label": "label"},
            }
        }
        atomic_bytes(folder / "profile.json", (json.dumps(profile, indent=2) + "\n").encode())
        print(f"Full-corpus scoring profile (not a new held-out test): {folder / 'profile.json'}")


def fail(message):
    raise SystemExit(message)


def private_write(path, content):
    with open(path, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)) as out:
        out.write(content)


def replay_values(raw):
    """Read the simple literal key=value format written by scripts/demo.py."""
    values = {}
    for line in raw.decode("utf-8-sig").splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key and not key.startswith("#"):
            values[key] = value.strip().strip("\"'")
    if (
        values.get("IP_DATASET_REPLAY", "").lower() != "true"
        or values.get("IP_REPO") != "replay/issuepilot-sandbox"
        or values.get("IP_WRITES_ENABLED", "").lower() != "false"
        or not all(
            values.get(k)
            for k in (
                "POSTGRES_PASSWORD",
                "N8N_ENCRYPTION_KEY",
                "IP_WEBHOOK_SECRET",
                "IP_REVIEWER_TOKENS",
            )
        )
    ):
        fail("Not a generated dataset replay configuration. Do not use a live .env file.")
    UUID(values.get("IP_REPLAY_RUN_ID", ""))
    return values


def authenticates(raw):
    # A separate client on the actual Compose network exercises TCP password auth,
    # unlike a Unix socket, localhost inside postgres, or pg_isready.
    env = dict(os.environ, PGPASSWORD=replay_values(raw)["POSTGRES_PASSWORD"])
    cmd = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network",
        ORIGINAL + "_default",
        "--env",
        "PGPASSWORD",
        "--env",
        "PGCONNECT_TIMEOUT=5",
        "postgres:17-bookworm",
        "psql",
        "-X",
        "-w",
        "-h",
        "postgres",
        "-U",
        "issuepilot",
        "-d",
        "issuepilot",
        "-A",
        "-t",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "SELECT 1",
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, timeout=30)
    if result.returncode == 0 and result.stdout.strip() == b"1":
        return True
    if b"password authentication failed" in result.stderr.lower():
        return False
    fail(
        "PROBE_UNAVAILABLE: check Docker Desktop and docker ps --filter name=issuepilot-replay-postgres-1. No files changed."
    )


def recover(root, state, old):
    current = root / ".env.replay"
    original = current.read_bytes()
    if authenticates(original):
        print(
            "CONFIG_OK: legacy replay credentials authenticate. From issuepilot/ run: py -3.12 scripts/demo.py up"
        )
        return
    if old is None or not old.is_file() or old.resolve() == current.resolve():
        fail(
            "NO_MATCH: no distinct original replay file found. Use --from PATH or run demo.py start. No files changed."
        )
    candidate = old.read_bytes()
    if not authenticates(candidate):
        fail(
            "NO_MATCH: neither replay file authenticates. Use the original backup or run demo.py start. No files changed."
        )
    if current.read_bytes() != original:
        fail("Configuration changed during the check; run recover again.")
    backups = state / "backups"
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = backups / ("env.replay-" + uuid4().hex + ".bak")
    private_write(backup, original)
    with tempfile.NamedTemporaryFile(dir=root, prefix=".replay-restore-", delete=False) as tmp:
        pending = Path(tmp.name)
        tmp.write(candidate)
    try:
        os.replace(pending, current)
    finally:
        pending.unlink(missing_ok=True)
    print("RESTORED: original .env.replay verified by SELECT 1; previous file backed up at", backup)
    print("Use the legacy launcher inside issuepilot/: py -3.12 scripts/demo.py up")


def fresh(root, state, action, *, resume=False):
    folder = state / FRESH
    cfg, overlay = folder / ".env.replay", folder / "artifacts.compose.json"
    if action == "start":
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not cfg.exists():
            volumes = subprocess.run(
                [
                    "docker",
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    "label=com.docker.compose.project=" + FRESH,
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
            if volumes.stdout.strip():
                fail(
                    f"Replay volumes already exist but {cfg} is missing. Restore this instance's file before starting. No volumes were removed."
                )
            template = (root / ".env.example").read_text(encoding="utf-8-sig")
            for old, new in {
                "GENERATE_WEBHOOK_SECRET": secrets.token_hex(32),
                "GENERATE_REVIEWER_TOKEN": secrets.token_urlsafe(40),
                "GENERATE_DATABASE_PASSWORD": secrets.token_hex(24),
                "GENERATE_N8N_ENCRYPTION_KEY": secrets.token_hex(32),
                "IP_REPO=demo/issuepilot-sandbox": "IP_REPO=replay/issuepilot-sandbox",
            }.items():
                if template.count(old) != 1:
                    fail("Unexpected .env.example; restore the template from this commit.")
                template = template.replace(old, new)
            content = (
                template + "\nIP_DATASET_REPLAY=true\nIP_REPLAY_RUN_ID=" + str(uuid4()) + "\n"
            ).encode()
            replay_values(content)
            private_write(cfg, content)
        artifacts = folder / "artifacts"
        artifacts.mkdir(exist_ok=True)
        mount = {"type": "bind", "source": artifacts.as_posix(), "target": "/app/artifacts"}
        overrides = {s: {"volumes": [mount]} for s in ("api", "analytics")}
        overrides["analytics"]["command"] = [
            "python",
            "scripts/sync_snowflake.py",
            "--out",
            "/tmp/issuepilot-events.json",
        ]
        atomic_bytes(overlay, json.dumps({"services": overrides}).encode())
    if not cfg.is_file() or not overlay.is_file():
        fail("Run demo.py start first. The original replay has not been changed.")
    values = replay_values(cfg.read_bytes())
    if action == "check":
        print(json.dumps(existing_n8n.checked(values), indent=2))
        return
    if action == "all":
        existing_n8n.submit(values, folder, resume=resume)
        return
    env = dict(os.environ)
    for key in ("POSTGRES_PASSWORD", "N8N_ENCRYPTION_KEY", "N8N_IMAGE", "COMPOSE_PROFILES"):
        env.pop(key, None)
    env.update(
        IP_APP_ENV_FILE=cfg.as_posix(),
        IP_REPLAY_DATA_DIR=(folder / "projection").as_posix(),
        IP_API_PORT="8021",
        ISSUEPILOT_IMAGE=f"issuepilot-replay:{version(root)}",
    )
    prefix = [
        "docker",
        "compose",
        "--env-file",
        str(cfg),
        "-p",
        FRESH,
        "-f",
        "compose.yaml",
        "-f",
        "compose.replay.yaml",
        "-f",
        str(overlay),
    ]
    steps = {
        "link": [],
        "start": [
            ["config", "--quiet"],
            ["build", "api"],
            ["up", "-d", "--no-build", "--wait", "postgres", "api", "worker"],
        ],
        "status": [["exec", "-T", "api", "python", "scripts/dataset_run.py", "status"]],
        "logs": [["logs", "--tail", "45", "api", "postgres", "worker"]],
        "stop": [["stop", "worker", "api", "postgres"]],
        "smoke": [["exec", "-T", "worker", "python", "scripts/check_ai.py", "--require-gpu"]],
        "pause": [["stop", "worker"]],
        "resume": [["up", "-d", "--wait", "worker"]],
        "export": [
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
            [
                "cp",
                "api:/tmp/issuepilot-events.json",
                str(folder / "artifacts/analytics/events.json"),
            ],
        ],
        "sync": [["--profile", "analytics", "run", "--rm", "--no-deps", "--build", "analytics"]],
    }
    if action == "sync" and not (root / "secrets/snowflake.p8").is_file():
        fail(
            "Set up the Snowflake role, connection and issuepilot/secrets/snowflake.p8 before sync."
        )
    if action == "export":
        (folder / "artifacts/analytics").mkdir(parents=True, exist_ok=True)
    for step in steps[action]:
        result = subprocess.run(prefix + step, cwd=root, env=env, check=False)
        if result.returncode:
            if action == "start":
                fail(
                    f"Start stopped. Run: py -3.12 demo.py logs. This instance uses {cfg}; existing volumes and settings have been kept."
                )
            fail(f"{action} failed (Docker exit {result.returncode}); use demo.py logs.")
    if action in {"start", "link"}:
        cid = subprocess.run(
            prefix + ["ps", "-q", "api"],
            cwd=root,
            env=env,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        if not cid:
            fail("Replay API is not running. Use demo.py start first.")
        existing_n8n.configure(folder, cid, root / "n8n/github-intake.json")
        print("Inbox: http://localhost:8021")
        print("Reviewer token location (open locally):", cfg)
        print(
            "Paste only the three replay nodes once in the existing workflow, publish, then run: py -3.12 demo.py check"
        )
        print("Send all 1,993 issues: py -3.12 demo.py all")
    if action == "export":
        print("Local event export:", folder / "artifacts/analytics/events.json")


def version(root):
    with (root / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "action",
        choices=[
            "start",
            "link",
            "check",
            "all",
            "status",
            "logs",
            "stop",
            "smoke",
            "pause",
            "resume",
            "export",
            "sync",
            "prepare",
            "evaluation-data",
            "recover",
        ],
    )
    cli.add_argument(
        "--from", dest="original_config", type=Path, help="Original .env.replay (recover only)"
    )
    cli.add_argument(
        "--source",
        type=Path,
        help="Optional pinned split directory or original dataset ZIP; otherwise download once",
    )
    cli.add_argument(
        "--resume",
        action="store_true",
        help="all only: skip stable deliveries already confirmed in this cohort",
    )
    a = cli.parse_args()
    if a.resume and a.action != "all":
        cli.error("--resume applies only to all")
    root = APP
    if a.original_config and a.action != "recover":
        cli.error("--from applies only to recover")
    if a.source and a.action not in {"prepare", "evaluation-data", "start"}:
        cli.error("--source applies only to start, prepare or evaluation-data")
    if a.source and not a.source.exists():
        cli.error("--source path does not exist")
    if a.source is not None:
        a.source = a.source.resolve()
    state = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share") / "IssuePilot"
    if a.action in {"start", "prepare", "evaluation-data"}:
        prepare_dataset(root, state, a.source, merged=a.action == "evaluation-data")
        if a.action != "start":
            return
    if a.action == "recover":
        recover(root, state, a.original_config)
    else:
        fresh(root, state, a.action, resume=a.resume)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.exit("Stopped: " + str(exc))
