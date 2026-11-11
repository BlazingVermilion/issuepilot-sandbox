import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.postgres import PostgresSaver
from contextlib import ExitStack
from issuepilot.api import create_app
from issuepilot.config import Settings
from issuepilot.db import Base
from issuepilot.worker import Worker


@pytest.fixture
def runtime(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url=os.getenv("TEST_DATABASE_URL", f"sqlite:///{tmp_path}/app.db"),
        checkpoint_url=str(tmp_path / "graph.db"),
        webhook_secret="w" * 40,
        reviewer_tokens={"alice": "a" * 40, "bob": "b" * 40},
        policy_dir=Path(__file__).resolve().parents[1] / "policies",
    )
    app = create_app(settings)
    # TEST_DATABASE_URL must point to a dedicated disposable test database.
    engine = app.state.sessions.kw["bind"]
    Base.metadata.drop_all(engine)
    with ExitStack() as stack:
        client = stack.enter_context(TestClient(app))
        if os.getenv("TEST_CHECKPOINT_URL"):
            saver = stack.enter_context(
                PostgresSaver.from_conn_string(os.environ["TEST_CHECKPOINT_URL"])
            )
            saver.setup()
        else:
            saver = stack.enter_context(SqliteSaver.from_conn_string(settings.checkpoint_url))
        service = app.state.service
        worker = Worker(settings, app.state.sessions, service.github, saver)
        yield client, worker, service, settings
    Base.metadata.drop_all(engine)


@pytest.fixture
def auth():
    return {"Authorization": "Bearer " + "a" * 40}
