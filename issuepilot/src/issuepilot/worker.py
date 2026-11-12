"""One durable worker per database. PostgreSQL advisory lock prevents competing executors."""

import argparse
import logging
import time
from contextlib import ExitStack
from sqlalchemy import select, text
from langgraph.types import Command
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.postgres import PostgresSaver
from .agent import Analyst
from .config import Settings
from .contracts import revision
from .db import Base, Case, Action, database, event, uid
from .github import GitHub
from .policy import Policies
from .labels import catalog
from .service import Service

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings, sessions, github, checkpointer):
        self.settings, self.sessions, self.github = settings, sessions, github
        self.checkpointer = checkpointer
        self.service = Service(settings, sessions, github)

    def recent(self):
        with self.sessions() as session:
            return [
                {"number": c.number, "title": c.source.get("title", ""), "state": c.status}
                for c in session.scalars(
                    select(Case)
                    .where(Case.repo == self.settings.repo)
                    .order_by(Case.created_at.desc())
                    .limit(10)
                )
            ]

    def recover(self):
        with self.sessions.begin() as session:
            for case in session.scalars(
                select(Case).where(
                    Case.repo == self.settings.repo, Case.status.in_(["PROCESSING", "EXECUTING"])
                )
            ):
                case.status = "QUEUED" if case.status == "PROCESSING" else "UNCERTAIN"
                event(session, case, "WORKER_RECOVERY", state=case.status)
            for action in session.scalars(
                select(Action)
                .join(Case, Action.case_id == Case.id)
                .where(Case.repo == self.settings.repo, Action.status == "SENDING")
            ):
                action.status = "UNKNOWN"

    def tick(self):
        with self.sessions.begin() as session:
            case = session.scalars(
                select(Case)
                .where(
                    Case.repo == self.settings.repo,
                    Case.status.in_(["QUEUED", "APPROVED", "REJECTED", "RESUME_QUEUED"]),
                )
                .order_by(Case.created_at)
                .with_for_update(skip_locked=True)
            ).first()
            if not case:
                return False
            case_id, version, number, state, thread_id, decision = (
                case.id,
                case.version,
                case.number,
                case.status,
                case.thread_id,
                case.decision,
            )
            if state == "QUEUED":
                case.status = "PROCESSING"
        try:
            if state == "RESUME_QUEUED":
                self.service.resume_actions(case_id)
                return True
            policies = Policies(self.settings.policy_dir)
            graph = Analyst(self.settings, policies, self.recent).graph(self.checkpointer)
            if state == "QUEUED":
                issue = self.github.read(number)
                if issue["state"] != "open":
                    with self.sessions.begin() as session:
                        case = session.get(Case, case_id)
                        if case.version == version:
                            case.status = "CLOSED"
                            event(session, case, "CLOSED")
                    return True
                thread_id = uid()
                started = time.monotonic()
                label_catalog = catalog(self.settings, self.github.list_labels())
                output = graph.invoke(
                    {"issue": issue, "label_catalog": label_catalog},
                    {"configurable": {"thread_id": thread_id}},
                )
                with self.sessions.begin() as session:
                    case = session.execute(
                        select(Case).where(Case.id == case_id).with_for_update()
                    ).scalar_one()
                    if case.version != version or case.status != "PROCESSING":
                        return True  # An edit arrived while the read-only graph was running.
                    case.source, case.source_hash = issue, revision(issue)
                    case.policy_hash, case.thread_id = policies.hash, thread_id
                    case.proposal = {
                        "value": output["proposal"],
                        "label_catalog": label_catalog,
                        "documents": output["documents"],
                        "trace": output["trace"],
                    }
                    case.status, case.error = "REVIEW", ""
                    event(
                        session,
                        case,
                        "PROPOSED",
                        engine=self.settings.engine,
                        model=self.settings.model if self.settings.engine == "ollama" else None,
                        label_catalog_hash=label_catalog["hash"],
                        policy_hash=policies.hash,
                        version=version,
                        latency_ms=round((time.monotonic() - started) * 1000),
                        category=output["proposal"]["category"],
                        missing_count=len(output["proposal"]["missing_fields"]),
                        abstain=output["proposal"]["abstain"],
                    )
            else:
                config = {"configurable": {"thread_id": thread_id}}
                saved = graph.get_state(config)
                # Crash after resume but before DB transition: do not resume a finished graph twice.
                if saved.next:
                    graph.invoke(Command(resume=decision), config)
                if state == "REJECTED":
                    with self.sessions.begin() as session:
                        session.get(Case, case_id).status = "DECLINED"
                elif self.service.prepare_actions(case_id):
                    self.service.execute(case_id)
        except Exception as exc:
            log.warning("Case processing failed: case=%s type=%s", case_id, type(exc).__name__)
            with self.sessions.begin() as session:
                case = session.execute(
                    select(Case).where(Case.id == case_id).with_for_update()
                ).scalar_one()
                if case.version == version:
                    if case.status == "EXECUTING":
                        case.status = "UNCERTAIN"
                    elif case.status not in {"UNCERTAIN", "STALE", "BLOCKED"}:
                        case.status = "FAILED"
                    case.error = (
                        type(exc).__name__ + ": inspect worker diagnostics and configuration"
                    )
                    event(session, case, "FAILED", exception=type(exc).__name__, state=case.status)
            log.debug("Worker exception", exc_info=True)
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    engine, sessions = database(settings.database_url)
    Base.metadata.create_all(engine)
    with ExitStack() as stack:
        # Keep this connection open for the worker's lifetime, not just for a transaction.
        if engine.dialect.name == "postgresql":
            lock = stack.enter_context(engine.connect())
            if not lock.execute(text("SELECT pg_try_advisory_lock(84622101)")).scalar():
                raise RuntimeError("Another worker owns this database")
            checkpointer = stack.enter_context(
                PostgresSaver.from_conn_string(settings.checkpoint_url)
            )
            checkpointer.setup()
        else:
            # Local SQLite demo must run exactly one worker; production enforces this with PostgreSQL.
            checkpointer = stack.enter_context(
                SqliteSaver.from_conn_string(settings.checkpoint_url)
            )
        github = GitHub(settings, sessions)
        stack.callback(github.close)
        worker = Worker(settings, sessions, github, checkpointer)
        worker.recover()
        while True:
            worked = worker.tick()
            if args.once:
                break
            if not worked:
                time.sleep(1)


if __name__ == "__main__":
    main()
