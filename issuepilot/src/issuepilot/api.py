"""HTTP boundary: signed intake and individually authenticated reviewer actions."""

import base64
import json
import secrets
from typing import Literal
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .labels import catalog
from . import issue_source, intake_protocol
from .workspace_compat import HISTORICAL_WORKSPACE_KEY
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from importlib.metadata import version
from .config import Settings
from .contracts import Proposal
from .db import Base, Case, Event, Action, DemoIssue, Delivery, database, now, event
from .github import GitHub
from .service import Service, Conflict


class Review(BaseModel):
    version: int
    approved: bool
    edited: Proposal | None = None


class Recovery(BaseModel):
    version: int
    mode: Literal["resume", "regenerate"] = "resume"
    acknowledge_unknown: bool = False


class Envelope(BaseModel):
    raw_body_base64: str = Field(max_length=400000)
    signature: str = Field(max_length=100)
    delivery_id: str = Field(max_length=200)
    event: str = Field(max_length=100)


class ReplyInput(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


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
        if settings.snapshot_intake:
            issue_source.load_snapshots(settings.snapshot_path)
        Base.metadata.create_all(engine)
        yield
        github.close()
        engine.dispose()

    app = FastAPI(title="IssuePilot", version=version("issuepilot"), lifespan=lifespan)
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
            "ai_profile": settings.ai_profile if settings.engine == "ollama" else None,
            "version": app.version,
            "model": settings.model if settings.engine == "ollama" else None,
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

    @app.post("/webhooks/n8n/intake", status_code=202)
    def ingest_issue_delivery(body: Envelope, request: Request):
        if not settings.snapshot_intake:
            if settings.n8n_workflow_id:
                intake_protocol.verify_workflow_context(
                    settings.n8n_workflow_id,
                    request.headers.get("X-IssuePilot-Workflow"),
                    request.headers.get("X-IssuePilot-Execution"),
                )
            return envelope(body)
        meta = intake_protocol.verify_workflow_context(
            settings.n8n_workflow_id,
            request.headers.get("X-IssuePilot-Workflow"),
            request.headers.get("X-IssuePilot-Execution"),
        )
        if body.event not in {"issues", "ping"}:
            raise HTTPException(422, "Only a signed issue or connectivity check is accepted")
        raw = base64.b64decode(body.raw_body_base64, validate=True)
        workspace_id = str(settings.workspace_id)
        # Both branches use the standard signature verifier before acting on the body.
        receipt = service.ingest(
            raw, body.signature, body.delivery_id, body.event, workflow_context=meta
        )
        if body.event == "ping":
            intake_protocol.validate_challenge(json.loads(raw), body.delivery_id, workspace_id)
            return {
                "state": "INTAKE_READY",
                "workspace_id": workspace_id,
                **meta,
                "proof": intake_protocol.sign_receipt(
                    settings.webhook_secret, workspace_id, body.delivery_id, meta
                ),
            }
        return {**receipt, "workspace_id": workspace_id, **meta}

    @app.get("/intake/receipts")
    def list_delivery_receipts(who=Depends(reviewer)):
        if not settings.snapshot_intake:
            raise HTTPException(404, "Snapshot intake is not configured")
        workspace_id = str(settings.workspace_id)
        with sessions() as session:
            records = session.execute(
                select(Delivery.id, Delivery.case_id)
                .join(Case, Case.id == Delivery.case_id)
                .join(Event, Event.case_id == Case.id)
                .where(
                    Case.repo == settings.repo,
                    Event.kind == "RECEIVED",
                    func.coalesce(
                        Event.data["workspace_id"].as_string(),
                        Event.data[HISTORICAL_WORKSPACE_KEY].as_string(),
                    )
                    == workspace_id,
                    Event.data["delivery_id"].as_string() == Delivery.id,
                )
            ).all()
        return {"workspace_id": workspace_id, "receipts": dict(records)}

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
            "ai_profile": settings.ai_profile if settings.engine == "ollama" else None,
            "model": settings.model if settings.engine == "ollama" else None,
            "writes_enabled": settings.writes_enabled,
            "version": app.version,
            "database": engine.dialect.name,
            "dataset": {
                "name": issue_source.DATASET,
                "revision": issue_source.REVISION,
                "total": len(issue_source.load_snapshots(settings.snapshot_path)),
            }
            if settings.snapshot_intake
            else None,
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
                    "title": c.title or "Waiting for source read",
                    "updated_at": c.updated_at,
                }
                for c in session.execute(
                    select(
                        Case.id,
                        Case.number,
                        Case.status,
                        Case.version,
                        Case.updated_at,
                        Case.source["title"].as_string().label("title"),
                    )
                    .where(Case.repo == settings.repo)
                    .order_by(Case.updated_at.desc())
                    .limit(
                        len(issue_source.load_snapshots(settings.snapshot_path))
                        if settings.snapshot_intake
                        else 100
                    )
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
                else f"https://github.com/microsoft/vscode/issues/{case.number}"
                if settings.snapshot_intake
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
                    {
                        "id": a.id,
                        "kind": a.kind,
                        "status": a.status,
                        "payload": a.payload,
                        "version": a.version,
                        "result": a.result,
                    }
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

    @app.post("/cases/{case_id}/recover")
    def recover(case_id: str, body: Recovery, who=Depends(reviewer)):
        return service.recover_actions(
            case_id, body.version, who, body.mode, body.acknowledge_unknown
        )

    @app.post("/cases/{case_id}/reconcile")
    def reconcile(case_id: str, who=Depends(reviewer)):
        return service.reconcile(case_id, who)

    @app.post("/cases/{case_id}/refresh-replies")
    def refresh_replies(case_id: str, who=Depends(reviewer)):
        if settings.snapshot_intake:
            raise Conflict("The frozen dataset has no subsequent reporter replies")
        with sessions.begin() as session:
            case = session.get(Case, case_id, with_for_update=True)
            if not case or case.repo != settings.repo:
                raise HTTPException(404)
            if case.status not in {"WAITING_REPORTER", "REPLY_FAILED"}:
                raise Conflict("This case is not waiting for reporter input")
            # Worker fetches the canonical snapshot, never a browser-supplied live reply.
            case.status = "REPLY_QUEUED"
            event(session, case, "REPORTER_REFRESH_REQUESTED", reviewer=who)
            return {"state": case.status}

    @app.post("/cases/{case_id}/demo-reply")
    def demo_reply(case_id: str, body: ReplyInput, who=Depends(reviewer)):
        if settings.mode != "demo" or settings.snapshot_intake:
            raise HTTPException(404)
        with sessions.begin() as session:
            case = session.get(Case, case_id, with_for_update=True)
            if not case or case.repo != settings.repo:
                raise HTTPException(404)
            if case.status != "WAITING_REPORTER":
                raise Conflict("This case is not waiting for reporter input")
            row = session.get(DemoIssue, case.number)
            replies = row.data.get("reporter_replies", []) + [
                {"id": secrets.token_hex(12), "body": body.body, "created_at": now().isoformat()}
            ]
            row.data = {**row.data, "reporter_replies": replies, "updated_at": now().isoformat()}
            case.status = "REPLY_QUEUED"
            event(session, case, "DEMO_REPORTER_REPLY", reviewer=who)
            return {"state": case.status}

    @app.post("/demo/issues")
    def demo(body: DemoInput, who=Depends(reviewer)):
        if settings.mode != "demo" or settings.snapshot_intake:
            raise HTTPException(404)
        with sessions.begin() as session:
            row = session.get(DemoIssue, body.number)
            data = {
                **body.model_dump(),
                "state": "open",
                "updated_at": now().isoformat(),
                "labels": row.data.get("labels", []) if row else [],
                "assignees": [],
                "reporter_replies": row.data.get("reporter_replies", []) if row else [],
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
            elif case.status not in {
                "APPROVED",
                "EXECUTING",
                "UNCERTAIN",
                "BLOCKED",
                "RESUME_QUEUED",
            } and not session.scalar(
                select(Action.id).where(
                    Action.case_id == case.id,
                    Action.version == case.version,
                    Action.status.in_(["PENDING", "SENDING", "UNKNOWN", "REJECTED"]),
                )
            ):
                case.status, case.version = "QUEUED", case.version + 1
            event(session, case, "DEMO_EDIT", reviewer=who)
            return {"case_id": case.id}

    return app
