# IssuePilot

**A human-approved GitHub issue triage assistant with evidence you can inspect.**

IssuePilot helps a maintainer decide what to do with a new issue: identify its intent,
find missing information, consult repository policy, and propose a clarification or
labels. The maintainer reviews and may edit the proposal. Only then can a restricted
backend act on the configured sandbox repository.

This project demonstrates AI workflow engineering: webhook verification, durable
work, read-only agent tools, retrieval-backed evidence, human approval, controlled
external writes, operational analytics and deployable services. It does not train a model or autonomously fix software.

**Portfolio demo:** replay 1,993 historical VS Code reports into an isolated sandbox.
A signed delivery enters n8n; the real worker classifies the issue with Qwen, retrieves
the policy and pauses for review. No public issues, comments or labels are created.
[Windows walkthrough, screenshots, Snowflake and releases](issuepilot/docs/portfolio-demo.md).
The 1,993 are the replay population, not the held-out test size.

## Replay all 1,993 issues from a fresh clone

Start Docker Desktop (Linux containers) and the Ollama app. Install Python 3.12.
Run these commands in Windows PowerShell; no ZIP, virtual environment, GitHub token
or previous installation is required. If `ollama list` already shows `qwen3:8b`, skip the pull.

```powershell
git clone https://github.com/BlazingVermilion/issuepilot-sandbox.git issuepilot-clean
cd issuepilot-clean
ollama pull qwen3:8b
py -3.12 demo.py start
```

1. Open [n8n](http://localhost:5698). On first use, create a local owner, import
   **`issuepilot/n8n/github-intake.json`** from this checkout, then **publish/activate** the workflow.
2. From the same Git root, run **`py -3.12 demo.py all`**. This sends all 1,993 reports through
   the published n8n webhook. No training or manually created GitHub issues are needed.
3. Open [IssuePilot](http://localhost:8021) → **Overview → Connect → Inbox**. Read the local
   maintainer token from the file named by `start`. Run **`py -3.12 demo.py status`**:
   `received=1993` means delivery finished; model proposal and review counts are separate.

If intake is interrupted, rerun `all`. To stop without losing the Inbox, use
`py -3.12 demo.py stop`; restart with `start`. The launcher keeps the credentials and
run ID outside the checkout, reuses the same Docker volumes and never resets the old 8011
Inbox. [Troubleshooting, evidence and Snowflake](issuepilot/docs/portfolio-demo.md).
For a single scoring split covering the same 1,993 reports, see [the benchmark guide](ai-bench/BENCHMARK.md#score-the-entire-corpus-as-one-test-split).

## Run the selected AI workflow

One local Ollama model, **qwen3:8b**, serves two stages. The classifier reads the
issue title and description without policy and can abstain. For supported cases,
the worker retrieves the relevant IssuePilot policy sections and asks the same
model whether the issue is ready to route. A maintainer reviews the recommendation.
No evaluator, judge or model selector runs in the console.

For the **standard sandbox** (port 8000, independent of the dataset replay above), start Ollama and Docker Desktop. From the `issuepilot/` subdirectory:

```powershell
ollama pull qwen3:8b
py -3.12 scripts/init_env.py
docker compose up -d --build --wait
docker compose exec -T worker python scripts/check_ai.py
```

For an **existing** install, keep the current project directory, Compose name,
volumes, secrets and `.env`. Follow the [upgrade steps](issuepilot/docs/runbook.md#upgrade-an-existing-installation-to-v05).
The container build installs a checksum-verified tokenizer. The model weights stay on the Ollama host.

Open <http://localhost:8000>. Open an issue from **Inbox → Review** to read its Markdown ticket. **View policy evidence** shows the saved policy sections. Use **Open audit history** on any inbox row (the primary action for completed cases) or in **Review → Execution trace**. The audit belongs to that case even when another review has unsaved edits. **Repository** shows the policy vocabulary, availability and runtime model;
each new proposal records which model actually ran. In demo mode, GitHub writes
are simulated. The optional rules-only rehearsal is limited to demo mode.

| Decision | Evidence from the supplied benchmark |
|---|---|
| Category: direct Qwen, no retrieval | Test: 173/200 correct (86.5%), macro-F1 0.8691; 2 abstentions, no technical errors. |
| Policy: topic-weighted BM25 + required dependencies + refined single-call Qwen | Final validation: 26/26 action decisions, 0/18 unsafe permissions, Recall@4 1.000 on 32 answerable queries. Separate **retrieval-only** test: full required context on 14/14 answerable queries. |

These are frozen **offline** results. Classification misses the 90% production
accuracy target. The final RAG arm lacks calibrated semantic scores and a held-out
end-to-end generation result. Use it for reviewed recommendations; a policy answer
never authorizes closure. [Selection, targets and limits](issuepilot/docs/ai-selection.md).

- [Architecture](issuepilot/docs/architecture.md): model inputs, policy retrieval, approval and execution.
- [Operator runbook](issuepilot/docs/runbook.md): upgrades, n8n, GitHub, recovery, Snowflake and deployment.
- [Historical evaluation contract](issuepilot/docs/evaluation.md) and [validation record](issuepilot/docs/validation.json).
- [Workflow design history](issuepilot/issuepilot_workflow_and_evaluation_plan.md).

## Run or adapt the AI benchmark

The [benchmark walkthrough](ai-bench/BENCHMARK.md) covers a clean checkout on Windows/Linux, quick offline checks, both selected architectures, optional vector retrieval and judging, and a working [custom dataset profile](ai-bench/examples/local/profile.json). Choose another Ollama model with `--model`. The evaluation CLI lives outside the reviewer UI. No old ZIP or GitHub credentials are required. Changed models and datasets have their own manifests and results.

## Development

Run these commands inside **issuepilot/**.

```powershell
py -3.12 -m pip install uv
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python scripts/benchmark.py B1 --engine rules --out artifacts/benchmarks/b1
uv run python scripts/benchmark.py B2 --engine rules --out artifacts/benchmarks/b2
```

For a native Ollama worker, install the tokenizer and asset first (see runbook). To run without Docker, use `.env` with host Ollama URL and SQLite defaults. Start
`uv run uvicorn issuepilot.api:create_app --factory` and, in another terminal,
`uv run python -m issuepilot.worker`. Exactly one worker is supported in this local
SQLite mode. Production uses PostgreSQL and a database advisory lock.

Tests using `TEST_DATABASE_URL` **drop application tables**: only point this variable
at a dedicated disposable test database. Never use your RDI or application database.

## Project layout

```text
.github/workflows/        CI, image release and deployment
ai-bench/                 Offline classification, RAG, retrieval and calibrated judging
                          Includes pinned source manifest, gold scenarios and local-data example
issuepilot/src/issuepilot/ API, reviewer UI, agent, worker, action ledger and analytics
issuepilot/policies/      Runtime policy and permitted labels
issuepilot/n8n/           Importable webhook workflow
issuepilot/warehouse/     Snowflake provisioning and operational queries
issuepilot/fixtures/      Pinned dataset manifest and controlled scenarios
issuepilot/scripts/       Environment setup, replay, evaluation and Snowflake sync
issuepilot/tests/         API, workflow, persistence and agent contract tests
issuepilot/docs/          Architecture, runbook, evaluation and recorded validation
```

## Limits that matter

Citation checks verify that quoted evidence was retrieved; a human still checks that
it actually supports the suggestion. The model never gets a write tool. Approval and
freshness checks cannot make GitHub and PostgreSQL one atomic transaction. A timed-out
write becomes UNCERTAIN and is not blindly replayed. Reviewer bearer tokens suit a
small private demo, not enterprise SSO. Synthetic evaluations do not prove enterprise
accuracy or saved staff time. See the architecture for the full contract.
