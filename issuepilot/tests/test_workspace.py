import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from issuepilot import issue_source, source_preparation, local_workspace
from issuepilot.local_workspace import Workspace, DEFAULT_PROJECT
from issuepilot.workspace_compat import HISTORICAL_REPOSITORY

ROOT = Path(__file__).resolve().parents[2]
EDITOR = "http://localhost:5678/workflow/maintainers"


def checkout(parent):
    app = parent / "issuepilot"
    app.mkdir(parents=True)
    (app / ".env.example").write_bytes((ROOT / "issuepilot/.env.example").read_bytes())
    return app


def test_existing_volume_keeps_credentials_delivery_identity_and_unknown_files(
    tmp_path, monkeypatch
):
    state = tmp_path / "local"
    old = state / "issuepilot-replay-full-fixture"
    old.mkdir(parents=True)
    wid = str(uuid4())
    config = dict(
        IP_DATASET_REPLAY="true",
        IP_WRITES_ENABLED="false",
        IP_REPO=HISTORICAL_REPOSITORY,
        IP_REPLAY_RUN_ID=wid,
        IP_WEBHOOK_SECRET="s" * 40,
        IP_REVIEWER_TOKENS=json.dumps({"maintainer": "t" * 40}),
        POSTGRES_PASSWORD="existing-password",
        N8N_ENCRYPTION_KEY="existing-key",
    )
    original = old / ".env.replay"
    original.write_bytes(local_workspace.encode_settings(config))
    initial = original.read_bytes()
    monkeypatch.setattr(local_workspace.n8n_workflow, "run_docker", lambda *args: old.name)
    workspace = Workspace(checkout(tmp_path / "product"), state)
    values = workspace.initialize(EDITOR)
    assert original.read_bytes() == initial
    assert values["IP_WORKSPACE_ID"] == wid and values["IP_REPO"] == HISTORICAL_REPOSITORY
    assert (
        values["IP_COMPOSE_PROJECT"] == old.name
        and values["POSTGRES_PASSWORD"] == config["POSTGRES_PASSWORD"]
    )
    assert workspace.initialize() == values
    from uuid import NAMESPACE_URL, uuid5

    assert issue_source.issue_delivery_id(5, values["IP_REPO"]) == str(
        uuid5(NAMESPACE_URL, f"{HISTORICAL_REPOSITORY}/{issue_source.REVISION}/issues/5/opened")
    )


def test_fresh_start_and_stop_manage_only_case_services(tmp_path, monkeypatch):
    ws = Workspace(checkout(tmp_path / "product"), tmp_path / "local")
    monkeypatch.setattr(local_workspace.n8n_workflow, "run_docker", lambda *args: "")
    monkeypatch.setattr(local_workspace, "prepare_source", lambda *args: None)
    mutate = Mock()
    monkeypatch.setattr(Workspace, "compose", mutate)
    monkeypatch.setattr(Workspace, "connect_workflow", lambda *args: None)
    ws.operate("start", editor_url=EDITOR)
    values = ws.settings()
    assert (
        values["IP_COMPOSE_PROJECT"] == DEFAULT_PROJECT
        and values["IP_REPO"] == issue_source.SANDBOX_REPOSITORY
    )
    assert mutate.call_args.args[-1][-3:] == ["postgres", "api", "worker"]
    assert all("n8n" not in call.args[-1] for call in mutate.call_args_list)
    ws.operate("stop")
    assert mutate.call_args.args[-1] == ["stop", "worker", "api", "postgres"]


def test_snowflake_check_and_sync_share_the_workspace_without_starting_other_services(
    tmp_path, monkeypatch
):
    app = checkout(tmp_path / "product")
    (app / "secrets").mkdir()
    (app / "secrets/snowflake.p8").touch()
    workspace = Workspace(app, tmp_path / "local")
    monkeypatch.setattr(Workspace, "settings", lambda _: {"IP_COMPOSE_PROJECT": "existing"})
    compose = Mock()
    monkeypatch.setattr(Workspace, "compose", compose)
    workspace.operate("check-snowflake")
    workspace.operate("sync")
    (check, sync) = compose.call_args_list
    assert check.args[0] == sync.args[0] == {"IP_COMPOSE_PROJECT": "existing"}
    assert check.args[1][-1] == "--check-context"
    assert "--check-context" not in sync.args[1]
    assert check.args[1][:-1] == sync.args[1][:-2]
    assert "--no-deps" in check.args[1]


def test_volume_without_credentials_is_not_rekeyed(tmp_path, monkeypatch):
    ws = Workspace(checkout(tmp_path / "product"), tmp_path / "local")
    monkeypatch.setattr(local_workspace.n8n_workflow, "run_docker", lambda *args: DEFAULT_PROJECT)
    with pytest.raises(ValueError, match="Restore"):
        ws.initialize(EDITOR)
    assert not ws.settings_file.exists()


def test_bad_download_never_enters_the_worker_or_scoring_cache(tmp_path, monkeypatch):
    from io import BytesIO

    app = checkout(tmp_path)
    # An HTML error page must not become the new dataset.
    monkeypatch.setattr(
        source_preparation, "urlopen", lambda *a, **kw: BytesIO(b"<html>unavailable</html>")
    )
    with pytest.raises(ValueError, match="Checksum mismatch"):
        source_preparation.prepare_source(app, tmp_path / "private", merged=True)
    assert not (tmp_path / "private" / "projection/issues.jsonl").exists()
    assert not (app.parent / "ai-bench/data/train.jsonl").exists()
    assert not (app.parent / "ai-bench/data/full-corpus/profile.json").exists()


def test_inputs_from_git_and_image_agree_on_corpus():
    paths = [
        ROOT / "ai-bench/fixtures/dataset_source.json",
        issue_source.MANIFEST_PATH,
    ]
    a, b = (json.loads(p.read_bytes()) for p in paths)
    assert all(a[k] == b[k] for k in ("repo", "revision", "splits", "sha256"))
    assert b["projection_sha256"] == issue_source.PROJECTION_SHA


def test_legacy_docker_data_path_is_not_needed_to_prepare_public_projection(tmp_path, monkeypatch):
    app = checkout(tmp_path)
    # Even an unusable path left by a different Compose stack cannot block this run.
    (app / "data").write_text("not a writable directory")
    source = tmp_path / "source"
    source.mkdir()
    row = dict(
        issue_number=1,
        input="Title: Draft\n\nDescription:\nLost",
        label="bug",
        created_at="2026-01-01",
        messages=["gold-only"],
    )
    raw = (json.dumps(row) + "\n").encode()
    (source / "test.jsonl").write_bytes(raw)
    mapped = dict(number=1, title="Draft", body="Lost", created_at=row["created_at"], split="test")
    expected = (issue_source.canonical_json(mapped) + "\n").encode()
    monkeypatch.setattr(issue_source, "SPLIT_SIZES", {"test": 1})
    monkeypatch.setattr(issue_source, "PROJECTION_SHA", hashlib.sha256(expected).hexdigest())
    manifest = dict(
        repo=issue_source.DATASET,
        revision=issue_source.REVISION,
        splits={"test": 1},
        sha256={"test": hashlib.sha256(raw).hexdigest()},
    )
    (app / "fixtures").mkdir()
    sample_manifest = app / "fixtures/dataset_source.json"
    sample_manifest.write_text(json.dumps(manifest))
    monkeypatch.setattr(issue_source, "MANIFEST_PATH", sample_manifest)
    state = tmp_path / "per-user"
    source_preparation.prepare_source(app, state, source)
    assert (state / "projection/issues.jsonl").read_bytes() == expected
    assert (app / "data").is_file()
