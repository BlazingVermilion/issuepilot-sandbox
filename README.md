# IssuePilot

IssuePilot helps a maintainer decide what to do with an incoming issue. It reads the report, proposes an issue category, looks up the applicable triage policy and drafts a response with citations. The reviewer sees the original ticket, can edit the proposal, and approves a specific version. The case keeps a record of what was actually done.

Use the public VS Code issue snapshots to exercise this workflow without creating or changing GitHub issues. Each source issue becomes one case, even if its webhook is delivered twice. The worker never receives the dataset's reference label or historical assistant messages. In a separately configured live sandbox, approved actions can add labels, assign permitted users or post a comment.

## Run on Windows

Start Docker Desktop, Ollama with `qwen3:8b`, and your **existing n8n**. Open a terminal in the extracted folder that contains `manage.py`. Python 3.12 is sufficient on the host. The first start downloads checksum-verified public source data and builds the application containers.

1. Run `py -3.12 manage.py start`. At the first prompt paste the URL of your **existing workflow editor**, such as `http://localhost:5678/workflow/<id>`. The workspace remembers it.
2. Run `py -3.12 manage.py workflow`. On Windows this copies three connected nodes. In that same workflow, remove the previous **issue snapshot branch only**, if present. Click empty canvas space, paste once, **save and publish**. Keep unrelated GitHub nodes. If the branch is already installed, skip this step.
3. Run `py -3.12 manage.py check`. Expect `INTAKE_READY`, your workflow ID and an execution ID. This sends a signed challenge without creating a case. A 404 means the production webhook is not registered at that URL: check the published workflow and its POST path.
4. Run `py -3.12 manage.py submit --resume`. This sends every issue without a verified receipt and checks the stored case IDs. It works for both an empty inbox and an interrupted submission. Run `py -3.12 manage.py status` to distinguish issues received, model proposals and human decisions.
5. Run `py -3.12 manage.py reviewer`, open the [reviewer console](http://localhost:8021), paste the token and connect. From **Inbox**, open a case in **Review**; inspect policy evidence or open the case's audit history.

The n8n branch is **Receive issue event → Preserve signed payload → Submit issue to triage**. Its production endpoint is `POST /webhook/issuepilot/issues`. The local launcher starts only PostgreSQL, the API and the worker. Installation size, workflow ID and port are not embedded in resource names.

## Project map

| Component | Responsibility |
|---|---|
| [Application and policy](issuepilot/) | FastAPI, PostgreSQL, checkpointed LangGraph workflow, reviewer console and policy documents |
| [n8n workflow](issuepilot/n8n/issue-intake.json) | Transports signed issue deliveries and returns the backend receipt |
| [AI benchmark](ai-bench/BENCHMARK.md) | Offline classification, retrieval, policy decisions and calibrated judging; supports local datasets and models |
| [Warehouse SQL](issuepilot/warehouse/) | Mirrors only event metadata into Snowflake and checks receipt, proposal and review counts separately |
| [GitHub Actions](.github/workflows/) | Tests on push/PR; publishes a tagged image after CI; deploys an approved digest on request |

The selected classifier scored **173/200** on the original test split (macro-F1 **0.8691**). The selected policy strategy gave the expected action on **26/26 validation action scenarios**. Those are different populations; the latter is a development result. Submitting the full corpus is an intake check, not a fresh accuracy measurement. See the [model selection and limitations](issuepilot/docs/ai-selection.md).

[Architecture](issuepilot/docs/architecture.md) · [Operations and troubleshooting](issuepilot/docs/runbook.md) · [Screenshots for a walkthrough](issuepilot/docs/portfolio-demo.md) · [Evaluation targets](ai-bench/EVALUATION_TARGETS.md)
