"""Application records are authoritative; LangGraph stores execution checkpoints separately."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    JSON,
    String,
    Integer,
    DateTime,
    Text,
    ForeignKey,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (UniqueConstraint("repo", "number"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    repo: Mapped[str] = mapped_column(String)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="QUEUED")
    version: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[dict] = mapped_column(JSON, default=dict)
    source_hash: Mapped[str] = mapped_column(String, default="")
    policy_hash: Mapped[str] = mapped_column(String, default="")
    proposal: Mapped[dict] = mapped_column(JSON, default=dict)
    thread_id: Mapped[str] = mapped_column(String, default="")
    decision: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    kind: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (UniqueConstraint("case_id", "version", "kind"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="PENDING")
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class DemoIssue(Base):
    __tablename__ = "demo_issues"
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    comments: Mapped[list] = mapped_column(JSON, default=list)


def database(url):
    if url.startswith("sqlite"):
        from pathlib import Path

        Path("data").mkdir(exist_ok=True)
    engine = create_engine(url, pool_pre_ping=True)
    return engine, sessionmaker(engine, expire_on_commit=False)


def event(session, case, kind, **data):
    session.add(Event(case_id=case.id, kind=kind, data=data))
    case.updated_at = now()
