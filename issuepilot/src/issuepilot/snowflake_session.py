"""Select and verify the sync destination in the service user's own session."""

from contextlib import contextmanager
import json
import os


class ContextError(RuntimeError):
    """A named Snowflake precondition failed before any warehouse data was changed."""


def _actual_name(configured):
    if configured.startswith('"') and configured.endswith('"'):
        return configured[1:-1].replace('""', '"')
    return configured.upper()


def select_target(cursor, *, role, warehouse, database, schema):
    def check(sql, args, stage, hint):
        try:
            if args:
                cursor.execute(sql, args)
            else:
                cursor.execute(sql)
        except Exception as exc:
            query_id = getattr(exc, "sfqid", None)
            reference = f" [query {query_id}]" if query_id else ""
            raise ContextError(f"{stage}{reference}: {exc}. {hint}") from exc

    for sql, target, kind in (
        ("USE DATABASE IDENTIFIER(%s)", database, "database"),
        ("USE SCHEMA IDENTIFIER(%s)", f"{database}.{schema}", "schema"),
        ("USE WAREHOUSE IDENTIFIER(%s)", warehouse, "warehouse"),
    ):
        check(
            sql,
            (target,),
            f"Cannot select {kind} {target} as {role}",
            f"Check that the {kind} exists and {role} has USAGE on it.",
        )

    check(
        "SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE(), "
        "CURRENT_DATABASE(), CURRENT_SCHEMA()",
        (),
        "Cannot read Snowflake session context",
        "Check the service connection.",
    )
    fields = ("user", "role", "warehouse", "database", "schema")
    row = cursor.fetchone()
    if not row or len(row) != len(fields):
        raise ContextError("Snowflake returned no usable session context.")
    actual = dict(zip(fields, row, strict=True))
    requested = dict(role=role, warehouse=warehouse, database=database, schema=schema)
    for key, value in requested.items():
        if actual[key] != _actual_name(value):
            raise ContextError(f"Expected {key} {value}; Snowflake reported {actual[key]!r}.")
    check(
        "SELECT EVENT_ID, BODY, HASH FROM TRIAGE_EVENTS LIMIT 0",
        (),
        f"Cannot read {database}.{schema}.TRIAGE_EVENTS as {role}",
        "Check the table, the three column names, and the SELECT grant.",
    )
    return actual


@contextmanager
def checked_session():
    import snowflake.connector

    names = {}
    for key in ("role", "warehouse", "database", "schema"):
        variable = f"IP_SF_{key.upper()}"
        value = os.environ.get(variable, "").strip()
        if not value:
            raise ContextError(f"{variable} is empty in the analytics container.")
        names[key] = value
    # Only authenticate with the application role at login. Set the destination
    # explicitly below, rather than relying on an optional login default.
    with snowflake.connector.connect(
        account=os.environ["IP_SF_ACCOUNT"],
        user=os.environ["IP_SF_USER"],
        authenticator="SNOWFLAKE_JWT",
        private_key_file=os.environ["IP_SF_PRIVATE_KEY_FILE"],
        private_key_file_pwd=os.environ.get("IP_SF_PRIVATE_KEY_PASSWORD"),
        role=names["role"],
    ) as conn:
        with conn.cursor() as cursor:
            try:
                actual = select_target(cursor, **names)
            except ContextError as exc:
                raise SystemExit(f"Snowflake context check failed: {exc}") from None
            print(json.dumps({"state": "CONTEXT_OK", **actual}), flush=True)
            yield cursor
