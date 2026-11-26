import json
import runpy
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest
from issuepilot import db
from issuepilot.snowflake_session import ContextError, select_target


NAMES = dict(role="SYNC", database="EVENTS", schema="MIRROR", warehouse="SYNC_WH")


class ServiceCursor:
    """Simulate a successful login with no current database, schema or warehouse."""

    def __init__(self, denied=None, role="SYNC"):
        self.denied = denied
        self.role = role
        self.database = self.schema = self.warehouse = None
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        if self.denied and self.denied in sql:
            raise PermissionError("does not exist or not authorized")
        if sql.startswith("USE DATABASE"):
            self.database = params[0]
        elif sql.startswith("USE SCHEMA"):
            assert self.database
            db_name, self.schema = params[0].split(".")
            assert db_name == self.database
        elif sql.startswith("USE WAREHOUSE"):
            self.warehouse = params[0]
        elif "FROM TRIAGE_EVENTS" in sql:
            assert self.database and self.schema and self.warehouse
        elif not sql.startswith("SELECT CURRENT_USER()"):
            pytest.fail(f"Unexpected statement in a read-only check: {sql}")

    def fetchone(self):
        return "SVC", self.role, self.warehouse, self.database, self.schema


@pytest.mark.parametrize(
    ("denied", "target"),
    [
        ("USE DATABASE", "database EVENTS"),
        ("USE SCHEMA", "schema EVENTS.MIRROR"),
        ("USE WAREHOUSE", "warehouse SYNC_WH"),
        ("FROM TRIAGE_EVENTS", "EVENTS.MIRROR.TRIAGE_EVENTS"),
    ],
)
def test_denied_object_is_named_before_any_staging(denied, target):
    cursor = ServiceCursor(denied=denied)
    with pytest.raises(ContextError) as caught:
        select_target(cursor, **NAMES)
    assert target in str(caught.value)
    assert "does not exist or not authorized" in str(caught.value)
    assert denied in cursor.queries[-1]


def test_unexpected_role_does_not_touch_the_target_table():
    cursor = ServiceCursor(role="PUBLIC")
    with pytest.raises(ContextError, match="Expected role SYNC"):
        select_target(cursor, **NAMES)
    assert not any("TRIAGE_EVENTS" in sql for sql in cursor.queries)


def test_check_command_restores_empty_context_without_opening_postgres(
    tmp_path, monkeypatch, capsys
):
    cursor = ServiceCursor()
    conn = Mock()
    conn.cursor.return_value = nullcontext(cursor)
    connector = ModuleType("snowflake.connector")
    connector.connect = Mock(return_value=nullcontext(conn))
    snowflake = ModuleType("snowflake")
    snowflake.connector = connector
    monkeypatch.setitem(sys.modules, "snowflake", snowflake)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)
    for k, v in dict(
        account="ORG-TEST", user="SVC", private_key_file="service.p8", **NAMES
    ).items():
        monkeypatch.setenv(f"IP_SF_{k.upper()}", v)

    monkeypatch.setattr(db, "database", lambda *_: pytest.fail("Preflight tried to open Postgres"))
    script = Path(__file__).resolve().parents[1] / "scripts/sync_snowflake.py"
    out = tmp_path / "events.json"
    monkeypatch.setattr(sys, "argv", [str(script), "--check-context", "--out", str(out)])
    with pytest.raises(SystemExit) as exit_status:
        runpy.run_path(str(script), run_name="__main__")
    assert exit_status.value.code == 0
    assert not out.exists()
    assert json.loads(capsys.readouterr().out) == dict(state="CONTEXT_OK", user="SVC", **NAMES)
    assert connector.connect.call_args.kwargs["role"] == "SYNC"
