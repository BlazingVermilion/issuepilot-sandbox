"""One durable worker per database. PostgreSQL advisory lock prevents competing executors."""

import argparse
import logging
import time
from contextlib import ExitStack
from sqlalchemy import select, text
from sqlalchemy import case as sql_case
from langgraph.types import Command
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.postgres import PostgresSaver
from .agent import Analyst
from .config import Settings
from .contracts import revision
from .db import Base, Case, Action, database, event, uid
from .github import GitHub
from .policy import runtime_policies
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

    def finish_execution(self, case_id):
        with self.sessions() as session:
            case = session.get(Case, case_id)
            if case.status != "COMPLETED" or case.proposal.get("workflow_version") != 2:
                return
            thread_id = case.thread_id
            decision = case.decision
        graph = Analyst(self.settings, runtime_policies(self.settings), self.recent).graph(
            self.checkpointer
        )
        config = {"configurable": {"thread_id": thread_id}}
        saved = graph.get_state(config)
        if saved.next == ("human_review",) and decision.get("approved"):
            graph.invoke(Command(resume=decision), config)
            saved = graph.get_state(config)
        if saved.next == ("execution_wait",):
            graph.invoke(Command(resume={"state": "COMPLETED"}), config)
            saved = graph.get_state(config)
        if saved.next == ("reporter_wait",):
            with self.sessions.begin() as session:
                case = session.get(Case, case_id)
                if case.status == "COMPLETED":
                    case.status = "WAITING_REPORTER"
                    event(session, case, "WAITING_REPORTER", version=case.version)

    def tick(self):
        with self.sessions() as session:
            completed = list(
                session.scalars(
                    select(Case.id).where(
                        Case.repo == self.settings.repo, Case.status == "COMPLETED"
                    )
                )
            )
        for cid in completed:
            self.finish_execution(cid)
        with self.sessions.begin() as session:
            case = session.scalars(
                select(Case)
                .where(
                    Case.repo == self.settings.repo,
                    Case.status.in_(
                        ["QUEUED", "APPROVED", "REJECTED", "RESUME_QUEUED", "REPLY_QUEUED"]
                    ),
                )
                .order_by(sql_case((Case.status == "QUEUED", 1), else_=0), Case.created_at, Case.id)
                .limit(1)
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
            prior_rounds = case.proposal.get("rounds", 0)
            if state == "QUEUED":
                case.status = "PROCESSING"
        try:
            if state == "RESUME_QUEUED":
                self.service.resume_actions(case_id)
                self.finish_execution(case_id)
                return True
            policies = runtime_policies(self.settings)
            graph = Analyst(self.settings, policies, self.recent).graph(self.checkpointer)
            if state in {"QUEUED", "REPLY_QUEUED"}:
                issue = self.github.read(number)
                if issue["state"] != "open":
                    with self.sessions.begin() as session:
                        case = session.get(Case, case_id)
                        if case.version == version:
                            case.status = "CLOSED"
                            event(session, case, "CLOSED")
                    return True
                thread_id = thread_id if state == "REPLY_QUEUED" else uid()
                started = time.monotonic()
                label_catalog = catalog(self.settings, self.github.list_labels())
                config = {"configurable": {"thread_id": thread_id}}
                if state == "REPLY_QUEUED":
                    saved = graph.get_state(config)
                    if saved.next == ("reporter_wait",) and all(
                        issue.get(k, [] if k == "reporter_replies" else None)
                        == saved.values["issue"].get(k, [] if k == "reporter_replies" else None)
                        for k in ("title", "body", "state", "reporter_replies")
                    ):
                        with self.sessions.begin() as session:
                            c = session.get(Case, case_id)
                            c.status = "WAITING_REPORTER"
                            event(session, c, "UNCHANGED_REPLY_IGNORED")
                        return True
                    # A crash after checkpoint persistence may leave only the DB update pending.
                    if saved.next == ("human_review",):
                        output = saved.values
                        issue = output["issue"]
                    elif saved.next == ("draft",):
                        # Resume a model/read-only failure after reporter input was checkpointed.
                        # The saved snapshot remains the proposal source; approval rechecks freshness.
                        output = graph.invoke(None, config)
                        issue = output["issue"]
                    elif saved.next == ("reporter_wait",):
                        output = graph.invoke(
                            Command(
                                resume={
                                    "kind": "reporter_snapshot",
                                    "issue": issue,
                                    "label_catalog": label_catalog,
                                }
                            ),
                            config,
                        )
                    else:
                        raise ValueError("Unexpected reporter checkpoint; inspect saved workflow")
                else:
                    output = graph.invoke(
                        {"issue": issue, "label_catalog": label_catalog, "rounds": prior_rounds},
                        {"configurable": {"thread_id": thread_id}},
                    )
                with self.sessions.begin() as session:
                    case = session.execute(
                        select(Case).where(Case.id == case_id).with_for_update()
                    ).scalar_one()
                    if case.version != version or case.status != (
                        "REPLY_QUEUED" if state == "REPLY_QUEUED" else "PROCESSING"
                    ):
                        return True  # An edit arrived while the read-only graph was running.
                    if state == "REPLY_QUEUED":
                        case.version += 1
                        case.decision = {}
                    case.source, case.source_hash = issue, revision(issue)
                    recorded = output.get("telemetry", {})
                    if recorded.get("policy_hash") != policies.hash:
                        case.status, case.error = (
                            "STALE",
                            "Policy changed; regenerate this proposal.",
                        )
                        event(session, case, "STALE", at="draft_recovery")
                        return True
                    case.policy_hash, case.thread_id = recorded["policy_hash"], thread_id
                    case.proposal = {
                        "value": output["proposal"],
                        "label_catalog": label_catalog,
                        "documents": output["documents"],
                        "trace": output["trace"],
                        "workflow_version": 2,
                        "rounds": output.get("rounds", 0),
                        "telemetry": output.get("telemetry", {}),
                    }
                    case.status, case.error = "REVIEW", ""
                    event(
                        session,
                        case,
                        "PROPOSED",
                        engine=self.settings.engine,
                        model=recorded.get("model"),
                        model_digest=recorded.get("model_digest"),
                        profile=recorded.get("profile"),
                        stages=recorded.get("stages", []),
                        label_catalog_hash=label_catalog["hash"],
                        policy_hash=policies.hash,
                        version=case.version,
                        latency_ms=round((time.monotonic() - started) * 1000),
                        category=output["proposal"]["category"],
                        missing_count=len(output["proposal"]["missing_fields"]),
                        abstain=output["proposal"]["abstain"],
                    )
            else:
                config = {"configurable": {"thread_id": thread_id}}
                saved = graph.get_state(config)
                # Crash after resume but before DB transition: do not resume a finished graph twice.
                if saved.next == ("human_review",):
                    graph.invoke(Command(resume=decision), config)
                if state == "REJECTED":
                    with self.sessions.begin() as session:
                        session.get(Case, case_id).status = "DECLINED"
                elif self.service.prepare_actions(case_id):
                    self.service.execute(case_id)
                    self.finish_execution(case_id)
        except Exception as exc:
            log.warning("Case processing failed: case=%s type=%s", case_id, type(exc).__name__)
            with self.sessions.begin() as session:
                case = session.execute(
                    select(Case).where(Case.id == case_id).with_for_update()
                ).scalar_one()
                if case.version == version:
                    if state == "REPLY_QUEUED":
                        case.status = "REPLY_FAILED"
                    elif case.status == "EXECUTING":
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
