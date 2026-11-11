"""HTTP boundary: signed intake and individually authenticated reviewer actions."""

import base64
import secrets
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .labels import catalog
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from .config import Settings
from .contracts import Proposal
from .db import Base, Case, Event, Action, DemoIssue, database, now, event
from .github import GitHub
from .service import Service, Conflict


class Review(BaseModel):
    version: int
    approved: bool
    edited: Proposal | None = None


class Envelope(BaseModel):
    raw_body_base64: str = Field(max_length=400000)
    signature: str = Field(max_length=100)
    delivery_id: str = Field(max_length=200)
    event: str = Field(max_length=100)


class DemoInput(BaseModel):
    number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(max_length=20000)


def create_app(settings=None):
    settings = settings or Settings()
    engine, sessions = database(settings.database_url)
    github = GitHub(settings, sessions)
    service = Service(settings, sessions, github)

    @asynccontextmanager
    async def lifespan(app):
        Base.metadata.create_all(engine)
        yield
        github.close()
        engine.dispose()

    app = FastAPI(title="IssuePilot", version="0.2.0", lifespan=lifespan)
    app.state.sessions, app.state.service = sessions, service
    app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")), name="static")

    def reviewer(authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        for name, expected in settings.reviewer_tokens.items():
            if secrets.compare_digest(token, expected):
                return name
        raise HTTPException(401, "A reviewer bearer token is required")

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(PermissionError)
    async def forbidden(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.middleware("http")
    async def limit_body(request, call_next):
        # Stream-limit even chunked requests; never buffer an unbounded body.
        if request.method in {"POST", "PUT", "PATCH"}:
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 400000:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
            request._body = bytes(data)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health():
        with sessions() as session:
            session.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "mode": settings.mode,
            "engine": settings.engine,
            "version": "0.2.0",
        }

    @app.get("/", response_class=HTMLResponse)
    def index():
        return Path(__file__).with_name("ui.html").read_text(encoding="utf-8")

    @app.post("/webhooks/github", status_code=202)
    async def webhook(request: Request):
        return service.ingest(
            await request.body(),
            request.headers.get("X-Hub-Signature-256", ""),
            request.headers.get("X-GitHub-Delivery", ""),
            request.headers.get("X-GitHub-Event", ""),
        )

    @app.post("/webhooks/n8n", status_code=202)
    def envelope(body: Envelope):
        # n8n carries the original bytes, not JSON.stringify(parsed_payload).
        raw = base64.b64decode(body.raw_body_base64, validate=True)
        return service.ingest(raw, body.signature, body.delivery_id, body.event)

    @app.exception_handler(httpx.HTTPError)
    async def upstream_error(request, exc):
        return JSONResponse(
            {
                "detail": "GitHub is unavailable or access was denied. Check repository and token permissions; no fallback labels were used."
            },
            status_code=502,
        )

    @app.get("/workspace")
    def workspace(who=Depends(reviewer)):
        return {
            "repo": settings.repo,
            "reviewer": who,
            "mode": settings.mode,
            "engine": settings.engine,
            "model": settings.model if settings.engine == "ollama" else None,
            "writes_enabled": settings.writes_enabled,
            "version": "0.2.0",
            "database": engine.dialect.name,
            "repo_url": f"https://github.com/{settings.repo}" if settings.mode == "live" else None,
        }

    @app.get("/repository/labels")
    def repository_labels(who=Depends(reviewer)):
        return catalog(settings, github.list_labels())

    @app.get("/cases")
    def cases(who=Depends(reviewer)):
        with sessions() as session:
            return [
                {
                    "id": c.id,
                    "number": c.number,
                    "status": c.status,
                    "version": c.version,
                    "title": c.source.get("title", "Waiting for source read"),
                    "updated_at": c.updated_at,
                }
                for c in session.scalars(
                    select(Case)
                    .where(Case.repo == settings.repo)
                    .order_by(Case.updated_at.desc())
                    .limit(100)
                )
            ]

    @app.get("/cases/{case_id}")
    def case_detail(case_id: str, who=Depends(reviewer)):
        with sessions() as session:
            case = session.get(Case, case_id)
            if not case or case.repo != settings.repo:
                raise HTTPException(404, "Case not found")
            events = list(
                session.scalars(
                    select(Event).where(Event.case_id == case_id).order_by(Event.created_at)
                )
            )
            actions = list(session.scalars(select(Action).where(Action.case_id == case_id)))
            return {
                "id": case.id,
                "number": case.number,
                "repo": case.repo,
                "issue_url": f"https://github.com/{case.repo}/issues/{case.number}"
                if settings.mode == "live"
                else None,
                "status": case.status,
                "version": case.version,
                "source": case.source,
                "proposal": case.proposal,
                "decision": case.decision,
                "error": case.error,
                "policy_hash": case.policy_hash,
                "events": [{"kind": e.kind, "data": e.data, "at": e.created_at} for e in events],
                "actions": [
                    {"kind": a.kind, "status": a.status, "payload": a.payload, "version": a.version}
                    for a in actions
                ],
            }

    @app.post("/cases/{case_id}/decision")
    def decide(case_id: str, body: Review, who=Depends(reviewer)):
        return service.decide(
            case_id,
            body.version,
            who,
            body.approved,
            body.edited.model_dump() if body.edited else None,
        )

    @app.post("/cases/{case_id}/regenerate")
    def regenerate(case_id: str, who=Depends(reviewer)):
        service.requeue(case_id)
        return {"state": "QUEUED"}

    @app.post("/cases/{case_id}/reconcile")
    def reconcile(case_id: str, who=Depends(reviewer)):
        return service.reconcile(case_id, who)

    @app.post("/demo/issues")
    def demo(body: DemoInput, who=Depends(reviewer)):
        if settings.mode != "demo":
            raise HTTPException(404)
        with sessions.begin() as session:
            row = session.get(DemoIssue, body.number)
            data = {
                **body.model_dump(),
                "state": "open",
                "updated_at": now().isoformat(),
                "labels": row.data.get("labels", []) if row else [],
                "assignees": [],
            }
            if row:
                row.data = data
            else:
                session.add(DemoIssue(number=body.number, data=data))
            case = session.scalar(
                select(Case)
                .where(Case.repo == settings.repo, Case.number == body.number)
                .with_for_update()
            )
            if not case:
                case = Case(repo=settings.repo, number=body.number)
                session.add(case)
                session.flush()
            elif case.status not in {"APPROVED", "EXECUTING", "UNCERTAIN"}:
                case.status, case.version = "QUEUED", case.version + 1
            event(session, case, "DEMO_EDIT", reviewer=who)
            return {"case_id": case.id}

    return app
