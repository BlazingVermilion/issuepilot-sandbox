"""A new checkout must not reset an existing replay or send events during setup."""

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("checkout_replay", ROOT / "demo.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def checkout(parent):
    app = parent / "issuepilot"
    app.mkdir(parents=True)
    for name in (".env.example", "pyproject.toml"):
        (app / name).write_bytes((ROOT / "issuepilot" / name).read_bytes())
    return app


def test_checkout_change_keeps_credentials_and_targets_the_same_case_store(tmp_path, monkeypatch):
    state = tmp_path / "private user state"
    calls = []

    def docker(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(launcher.subprocess, "run", docker)
    monkeypatch.setenv("POSTGRES_PASSWORD", "from-an-unrelated-project")
    first, second = checkout(tmp_path / "project A"), checkout(tmp_path / "project B")
    launcher.fresh(first, state, "start")
    config = state / launcher.FRESH / ".env.replay"
    initial = config.read_bytes()
    assert not (first / ".env.replay").exists()
    launcher.fresh(second, state, "start")
    assert not any("send" in args for args, _ in calls)
    assert config.read_bytes() == initial

    launcher.fresh(second, state, "all")
    cmd, opts = calls[-1]
    assert cmd[-3:] == ["scripts/dataset_run.py", "send", "--all"]
    assert cmd[cmd.index("-p") + 1] == "issuepilot-replay-full1993"
    assert Path(opts["env"]["IP_APP_ENV_FILE"]) == config
    assert "POSTGRES_PASSWORD" not in opts["env"]
    assert (opts["env"]["IP_API_PORT"], opts["env"]["IP_N8N_PORT"]) == ("8021", "5698")


def test_missing_private_configuration_does_not_rekey_volumes(tmp_path, monkeypatch):
    docker = Mock(return_value=subprocess.CompletedProcess([], 0, b"existing_pg_volume\n", b""))
    monkeypatch.setattr(launcher.subprocess, "run", docker)
    with pytest.raises(SystemExit, match="Restore"):
        launcher.fresh(checkout(tmp_path / "new clone"), tmp_path / "state", "start")
    assert docker.call_count == 1
    assert docker.call_args.args[0][1:3] == ["volume", "ls"]
    assert not (tmp_path / "state" / launcher.FRESH / ".env.replay").exists()


def test_bad_download_never_enters_the_worker_or_scoring_cache(tmp_path, monkeypatch):
    from io import BytesIO

    app = checkout(tmp_path)
    (app / "fixtures").mkdir()
    (app / "fixtures/dataset_source.json").write_bytes(
        (ROOT / "issuepilot/fixtures/dataset_source.json").read_bytes()
    )
    # An HTML error page must not become the new dataset.
    monkeypatch.setattr(launcher, "urlopen", lambda *a, **kw: BytesIO(b"<html>unavailable</html>"))
    with pytest.raises(SystemExit, match="Checksum mismatch"):
        launcher.prepare_dataset(app, merged=True)
    assert not (app / "data/replay/issues.jsonl").exists()
    assert not (app.parent / "ai-bench/data/train.jsonl").exists()
    assert not (app.parent / "ai-bench/data/full-corpus/profile.json").exists()


def test_inputs_from_git_and_image_agree_on_corpus():
    paths = [
        ROOT / "ai-bench/fixtures/dataset_source.json",
        ROOT / "issuepilot/fixtures/dataset_source.json",
    ]
    a, b = (json.loads(p.read_bytes()) for p in paths)
    assert a == b
    assert sum(b["splits"].values()) == 1993
