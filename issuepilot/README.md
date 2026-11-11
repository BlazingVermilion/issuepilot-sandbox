# IssuePilot

**A human-approved GitHub issue triage assistant with evidence you can inspect.**

IssuePilot helps a maintainer decide what to do with a new issue: identify its intent,
find missing information, consult repository policy, and propose a clarification or
labels. The maintainer reviews and may edit the proposal. Only then can a restricted
backend act on the configured sandbox repository.

This project demonstrates AI workflow engineering: webhook verification, durable
work, read-only agent tools, retrieval-backed evidence, human approval, controlled
external writes, operational analytics and deployable services. It complements RDI's
data-engineering focus. It does not train a model or autonomously fix software.

For example, an issue saying **“API crashes when saving a draft”** without a
version or reproduction steps gets a proposal to ask for those details, supported
by the sandbox's bug-report policy. A README typo follows a different policy and
should not trigger a request for runtime version. The maintainer can correct or
reject either suggestion before it becomes a GitHub comment.

## What you can run

- A light/dark review console with five workflow tabs, editable proposals, policy evidence and audit history.
- Repository-aware label discovery and permission checks; no invented labels.
- A synthetic demo that requires no GitHub credential or LLM to exercise the full flow.
- A configurable Ollama/LangGraph agent with policy retrieval and durable interruption.
- Signed GitHub intake directly or through an importable n8n workflow.
- PostgreSQL cases/checkpoints and an action ledger that stops ambiguous retries.
- Snowflake event export, repeatable sync/readback and maintainer-decision SQL.
- Docker Compose, test CI, GHCR release and explicit digest-based server deployment.

## Start here on Windows

Install Python 3.12 and Docker Desktop, open PowerShell in this folder:

```powershell
py -3.12 scripts/init_env.py
docker compose up -d --build --wait
```

Open <http://localhost:8000>, enter the maintainer token from your local `.env`,
open **Inbox** to create a demo issue, inspect **Review**, then verify **Activity**. The initial engine is
**rules**, clearly labeled; see the runbook to enable the actual LLM.

| Read | Purpose |
|---|---|
| [Architecture contract](docs/architecture.md) | Accepted scope, technology responsibilities, trust boundaries and decisions. |
| [Operator runbook](docs/runbook.md) | Local demo, n8n import, Ollama, GitHub sandbox, HTTPS deployment and Snowflake. |
| [Evaluation](docs/evaluation.md) | Baseline, fixtures, measurement methodology and limits. |
| [Validation record](docs/validation.json) | What was actually executed for this delivery. |

## Delivery status — evidence, not promises

| Area | Implemented | Verified in this delivery |
|---|---|---|
| FastAPI + review UI + restricted executor | Yes | API/worker tests with simulated GitHub; five-tab demo workflow and light/dark themes checked in Chrome. |
| Duplicate events, approval, stale proposals, ambiguous writes | Yes | Local automated tests. |
| LangGraph durable interrupt and resume | Yes | SQLite checkpoint closed/reopened in a test. |
| Ollama tool calling + lexical RAG | Yes | Orchestration fake-model test; actual model-quality run pending. |
| PostgreSQL runtime/checkpointer | Yes | CI job supplied; live Postgres run pending. |
| n8n webhook relay | Yes | Envelope endpoint tested; actual imported workflow replay pending. |
| Snowflake sync and readback | Yes | Sanitized event contract tested; actual account sync pending. |
| Docker, CI, release and deploy workflows | Yes | Source supplied; container/host/GitHub Actions execution pending. |

The 0.2.0 update passed 32 automated tests and a local browser walkthrough with
SQLite and the rules engine. The walkthrough covered demo intake, review, approval,
completed actions, label permissions and both themes. Live GitHub label reads/writes,
Ollama, n8n, PostgreSQL, Snowflake and hosted deployment were not revalidated in this
update. Use the runbook to verify those integrations in your environment.

- [ ] Run PostgreSQL and container CI successfully in the new repository.
- [ ] Import n8n; show ACCEPTED then DUPLICATE for the same signed delivery.
- [ ] Run an actual Ollama evaluation and review failed cases without hiding them.
- [ ] Approve a real sandbox proposal; verify a single comment and intended labels.
- [ ] Demonstrate stale proposal rejection and retained audit history.
- [ ] Sync operational events twice to Snowflake and verify content/unique IDs.
- [ ] Publish a release image; deploy its digest to HTTPS and check the review UI.
- [ ] Add sanitized evidence and measured results; keep secrets and private issue text out.

## Development

```powershell
py -3.12 -m pip install uv
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python scripts/evaluate.py --engine rules
```

To run without Docker, use `.env` with host Ollama URL and SQLite defaults. Start
`uv run uvicorn issuepilot.api:create_app --factory` and, in another terminal,
`uv run python -m issuepilot.worker`. Exactly one worker is supported in this local
SQLite mode. Production uses PostgreSQL and a database advisory lock.

Tests using `TEST_DATABASE_URL` **drop application tables**: only point this variable
at a dedicated disposable test database. Never use your RDI or application database.

## Project layout

```text
src/issuepilot/     API, reviewer UI, agent, policies, worker, action ledger, analytics
policies/          Version-controlled synthetic sandbox policy
n8n/               Importable webhook workflow
warehouse/         Snowflake provisioning and business queries
fixtures/          Labeled synthetic evaluation cases
scripts/           Environment setup, replay, evaluation and Snowflake sync
tests/             API, workflow, persistence and agent contract tests
docs/              Architecture, runbook, evaluation, recorded validation
.github/workflows/ CI, image release and explicit deployment
```

## Limits that matter

Citation checks verify that quoted evidence was retrieved; a human still checks that
it actually supports the suggestion. The model never gets a write tool. Approval and
freshness checks cannot make GitHub and PostgreSQL one atomic transaction. A timed-out
write becomes UNCERTAIN and is not blindly replayed. Reviewer bearer tokens suit a
small private demo, not enterprise SSO. Synthetic evaluations do not prove enterprise
accuracy or saved staff time. See the architecture for the full contract.
