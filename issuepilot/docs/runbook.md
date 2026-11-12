# Runbook: from local demo to verified sandbox deployment

Run commands from the extracted repository root. Do not paste credentials into chat,
screenshots, commits or issue comments. Instructions below assume Windows PowerShell
and Docker Desktop running Linux containers. The repo does not need your old RDI venv.

## 1. Create configuration

```powershell
cd C:\path\to\issuepilot
py -3.12 scripts/init_env.py
```

This creates `.env` once. Open it locally: `IP_REVIEWER_TOKENS` contains your named
reviewer token. Keep `IP_MODE=demo`, `IP_ENGINE=rules`, and `IP_WRITES_ENABLED=false`
for the first run. Demo mode never calls GitHub writes.

```powershell
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
```

Open <http://localhost:8000>. Paste the maintainer token from `.env` into Reviewer
access. Create demo issue #1 with the default crash report. The backend queues it;
the worker drafts a proposal. Refresh until status is REVIEW. Read the policy quotes,
approve, then refresh until COMPLETED. Audit history shows your identity and actions.
Demo writes are stored in PostgreSQL `demo_issues`, not sent to a real repository.

```powershell
docker compose logs --tail 80 worker
docker compose logs --tail 80 api
```

The rules mode is a runnable comparison baseline, not an LLM demonstration.
If a container cannot connect to Ollama, keep rules mode while you finish infrastructure.

## 2. Verify replay and stale proposals

In demo mode, create issue #1 before replaying its event. The script signs the exact
bytes and posts the same delivery ID twice:

```powershell
docker compose exec api python scripts/replay_webhook.py
```

Expected responses: ACCEPTED then DUPLICATE. One case remains. Wait for REVIEW.
Edit the demo issue before approving the old proposal; reload the case. Old versions
cannot be approved. The tests also cover an edit that arrives without a webhook and
an edit after approval but before execution.

A failed draft without an action ledger can be regenerated after repair. Cases with
external actions use **Recover execution** instead. See the recovery instructions below;
do not reset database rows or blindly retry POSTs.

## 3. Start n8n and import the workflow

```powershell
docker compose --profile automation up -d
```

1. Open <http://localhost:5678> and create the local n8n owner account.
2. Create a workflow, use **Import from file**, select `n8n/github-intake.json`.
3. Inspect `GitHub delivery`: POST, raw body enabled, path `issuepilot-github`,
   response after the last node. Keep `Verify and persist` URL as
   `http://api:8000/webhooks/n8n` when both services run in this Compose project.
4. Save and publish/activate it. Use its **production** webhook URL, not webhook-test.
5. Replay through n8n from your host, or from the API container using the n8n hostname:

```powershell
docker compose exec api python scripts/replay_webhook.py --url http://n8n:5678/webhook/issuepilot-github
```

Expected: ACCEPTED then DUPLICATE. A 403 indicates the raw bytes/signature changed.
Inspect the Webhook node's binary output: the `data` binary property must exist.
Do not “fix” a signature error by removing validation or serializing `body` again.
The workflow export is version-pinned but still requires this runtime acceptance check.
Backend acceptance is durable before the HTTP response. n8n retries are bounded and
safe for intake; they do not retry GitHub action POSTs.

## 4. Enable the local LLM

Install/open Ollama on the host, then:

```powershell
ollama pull qwen2.5:7b
```

Change `.env` to `IP_ENGINE=ollama` and `IP_MODEL=qwen2.5:7b`. In Docker, the supplied
URL is `http://host.docker.internal:11434`. Ollama must listen on an interface reachable
from the container; check host firewall and Ollama host settings. Keep access local,
not exposed to the public Internet. A 7B model may be slow depending on RAM/GPU; choose
another tool-calling-capable model only after rerunning the evaluation.

```powershell
docker compose up -d --force-recreate api worker
```

Create a new demo issue. Inspect actual tool calls and retrieved evidence in the UI.
The model can abstain or fail contract validation. Do not substitute rules output while
labeling it an LLM success. A model failure sets FAILED and performs no GitHub action.

For offline evaluation, create a separate local environment:

```powershell
py -3.12 -m pip install uv
uv sync --frozen --extra dev --extra snowflake
uv run python scripts/evaluate.py --engine rules --out artifacts/rules.json
uv run python scripts/evaluate.py --engine ollama --model qwen2.5:7b --out artifacts/ollama.json
```

The evaluator ignores `.env` and uses host Ollama at localhost:11434. Evaluation
fixtures are synthetic. Read `docs/evaluation.md` before making performance claims.

## 5. Connect one real GitHub sandbox

Create a new empty sandbox repository under your account. Do not start on someone
else's repository or your production RDI repository. Keep its existing labels.
Live mode reads the paginated GitHub label catalog; it never invents a missing label.
The default policy permits `bug`, `documentation`, `enhancement`, `question` and
`accessibility` only when present. Missing bug details result in a clarification
comment, not an automatic `needs-info` label. Other labels require maintainer handling.

Create a fine-grained token limited to that repository with Issues read/write and
Metadata read. Store it locally as `IP_GITHUB_TOKEN`. In `.env` set:

```dotenv
IP_MODE=live
IP_REPO=YOUR_LOGIN/YOUR_SANDBOX
IP_WRITES_ENABLED=false
```

Edit `policies/triage.md` to describe the actual sandbox policy. Leave assignees empty
unless you deliberately configure valid collaborators in both policy and
`IP_ALLOWED_ASSIGNEES`. The repository allowlist contains exactly one repository.
Restart api/worker after changing environment settings.

A public HTTPS webhook URL is required for GitHub to reach your machine/server.
After deployment (next section), configure the sandbox's Webhook:

- Payload URL: `https://YOUR_WEBHOOK_DOMAIN/webhook/issuepilot-github`
- Content type: `application/json`
- Secret: exact value of `IP_WEBHOOK_SECRET`
- Events: **Issues** only. Keep SSL verification enabled.

Create a genuine issue in your sandbox, wait for REVIEW, inspect the proposal. To
actually execute an approved write, set `IP_WRITES_ENABLED=true` and recreate api/worker.
The write flag is intentional: approval while disabled fails safely and must be
regenerated after configuration is repaired. Never publish this credential.

## 6. Deploy with HTTPS and traceable releases

The repository includes CI, a GHCR release workflow, and a digest-based deployment
workflow. They are source files until you create/push the new repository. No cloud
resources or billing have been created by this package.

1. Create a new GitHub repository named `issuepilot` (or your preferred name).
2. From VS Code's source control initialize Git and commit this project. Verify `.env`,
   `.venv`, runtime data and keys are not staged. Publish to the new repository.
3. Check all CI jobs. PostgreSQL/checkpointer and Docker jobs must pass; a local unit
   test result alone does not establish that the containers work.
4. Create a version tag after CI passes, e.g. `v0.3.0`. The release workflow runs CI
   again, then publishes a GHCR image with a source-commit label and provenance.
5. Provision your chosen Linux host with Docker Compose. Place the source/configuration
   under `/opt/issuepilot`, generate its own `.env`, and create DNS names for app/webhook.
   No host provider is prescribed and no paid account is necessary for local development.
6. Put `APP_DOMAIN` and `WEBHOOK_DOMAIN` in the server `.env`. Open ports 80/443 for Caddy.
   Keep 8000,5678,5432 closed externally. Back up Postgres/n8n volumes.
7. Import and publish the n8n workflow through a private tunnel to its editor. With
   secure cookies enabled, use a private HTTPS reverse proxy for editor access rather
   than turning off secure cookies on the public host.
8. Run the production profile using the release image digest:

```bash
export ISSUEPILOT_IMAGE=ghcr.io/YOUR_LOGIN/issuepilot@sha256:YOUR_VERIFIED_DIGEST
docker compose --profile automation -f compose.yaml -f deploy/compose.production.yaml up -d --no-build --wait
```

For the optional GitHub deployment workflow, set an environment named `production`
with approval rules and secrets `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`,
`DEPLOY_KNOWN_HOSTS` (verify the SSH host key independently). The deploy user must
have access to the pre-provisioned project and Docker. Pass the exact release digest
to **Deploy approved image**. The workflow deploys backend/worker images; n8n and
infrastructure configuration changes are reviewed separately on the host.

Rollback: redeploy the previous known-good digest. This does not roll back database
schema; v0.1 creates the initial schema only. Back up before future migrations.
Never delete Docker volumes as a routine repair command.

## 7. Snowflake operational analytics

This is a new analytics dataset, independent of the RDI monthly KPI tables. Use your
existing Snowflake account if desired. Run `warehouse/setup.sql` with a provisioning
role, then grant `ISSUEPILOT_SYNC` to your key-pair user.

Set these variables in the local shell, not in source control:

```powershell
$env:IP_SF_ACCOUNT = "YOUR_ACCOUNT_IDENTIFIER"
$env:IP_SF_USER = "YOUR_KEYPAIR_USER"
$env:IP_SF_PRIVATE_KEY_FILE = "C:\private\snowflake_key.p8"
$env:IP_SF_ROLE = "ISSUEPILOT_SYNC"
$env:IP_SF_WAREHOUSE = "ISSUEPILOT_WH"
$env:IP_SF_DATABASE = "ISSUEPILOT_DB"
$env:IP_SF_SCHEMA = "ANALYTICS"
# If encrypted, set IP_SF_PRIVATE_KEY_PASSWORD privately in the shell.
```

The sync script needs access to the *same* PostgreSQL database as the app. Prefer
running it in a one-off container on the Compose network; the base runtime omits
Snowflake dependencies deliberately. For local host access, use a temporary private
port override (bind 127.0.0.1 only), set `IP_DATABASE_URL` to that local endpoint, and
run the optional Snowflake environment installed in step 4. Do not export an empty
SQLite demo database by accident.

A ready analytics container is provided with the optional profile:

```powershell
docker compose --profile analytics run --rm analytics python scripts/sync_snowflake.py --export-only
docker compose --profile analytics run --rm analytics python scripts/sync_snowflake.py
docker compose --profile analytics run --rm analytics python scripts/sync_snowflake.py
```

Before running the analytics profile, put a private copy of your key at
`secrets/snowflake.p8`, create the `artifacts` directory and run
`docker compose --profile analytics build analytics`. On Linux, grant the container
UID 10001 read access to that key and write access to artifacts without making the
key world-readable. Set the Snowflake environment variables above; the container
uses `/run/secrets/snowflake.p8` regardless of the host key path. Both sync calls must finish VERIFIED with the same event set;
query the destination for duplicate IDs and run `warehouse/metrics.sql`. Absence of
new rows is a successful repeat, not a new accuracy measurement. Warehouse auto-suspend
is configured, but actual Snowflake charges depend on your account and usage.

## 8. Operating notes

- `GET /health` verifies API/database connectivity, not model quality or worker liveness.
  A stuck QUEUED case requires checking worker logs/container status.
- Model failures do not auto-retry forever. Repair the model/config and regenerate.
- Review credentials establish a named private-demo reviewer, not corporate identity proof.
- SQLite fallback: one API and one worker only. Use PostgreSQL for concurrent use.
- No enterprise SLA, broad security certification or productivity gain is claimed.
- Complete the live acceptance checklist in README and attach sanitized evidence before
  calling the portfolio deployment finished.

## Upgrading to 0.3.0 and using the console

Keep your existing `.env`, Compose project name, and PostgreSQL volume. Copy the
updated source over the old checkout, including `src/issuepilot/static/` and
`policies/label_rules.json`. Do not run `docker compose down -v` or reinitialize
credentials. Remove the old `IP_ALLOWED_LABELS` line from `.env` unless you
intentionally want that additional restriction. Do not change live/demo or engine
settings merely to upgrade the UI.

Rebuild the existing project's services (include your original `-p` project flag
and `-f` overrides if you used them before):

```powershell
docker compose up -d --build --force-recreate api worker
```

Refresh the browser with Ctrl+Shift+R. No database migration is required. Proposals
created before 0.2.0 have no label-catalog snapshot: approval marks them STALE;
use **Generate a fresh proposal** and review again. Completed actions remain intact.

1. **Overview**: enter your reviewer token and connect. Check mode and engine.
2. **Repository**: select **Refresh labels**. In live mode this reads GitHub; demo
   explicitly uses a fixture. Check which labels the policy permits.
3. **Inbox**: select an existing case. In demo, expand **Create a local demo issue**;
   in live mode, create an issue on GitHub and wait for the configured webhook/worker.
4. **Review**: read the source and evidence, inspect missing fields, edit the comment
   or permitted labels, then use **Review & approve** and confirm the exact action.
5. **Activity**: inspect action results and the audit timeline. In live mode also
   inspect the real GitHub issue. External Snowflake/CI status is not inferred here.

The bottom-left theme control switches light/dark. The theme uses localStorage; the reviewer token uses tab-scoped sessionStorage,
so a page refresh retains the authenticated session. Disconnect clears it. Browser
restore/duplicate-tab features may retain that storage: use Disconnect for explicit sign-out. Processing cases
refresh automatically; review edits are preserved. Technical JSON is collapsed.

## Recover an interrupted case

1. Fix the cause first. For GitHub 403, confirm the fine-grained token includes this
   repository and Issues read/write, and check expiry/organization approval. Preserve
   existing credentials; do not paste them into logs or screenshots.
2. After editing `.env`, apply it to both services:

   ```powershell
   docker compose up -d --build --force-recreate api worker
   docker compose logs --tail 80 worker
   ```

3. Refresh the UI with Ctrl+Shift+R and reconnect if needed. Open **Inbox**, select the
   affected issue, then **Review**. Open **Activity** to inspect the exact approved
   payload and action results; compare with the actual GitHub issue.
4. If no actions exist, use **Generate a fresh proposal** and review it again.
   Otherwise use **Recover execution** and choose **Resume remaining actions**.
5. Leave the uncertainty checkbox unchecked initially. Recovery reads GitHub before
   retrying. Already observed actions are preserved; confirmed rejected actions can
   resume after permission repair. Older v0.2 failures may have status UNKNOWN even
   when their log showed 403. If recovery requires acknowledgement, inspect the issue
   and logs, then explicitly acknowledge the potential delayed/duplicate write only
   if you accept that risk. The system cannot prove absence from a lost response.
6. Confirm recovery. Expect RESUME_QUEUED, then COMPLETED if remaining actions succeed.
   No new LLM draft is needed for an unchanged approved proposal. If another error
   occurs, repair it and use recovery again; prior DONE actions remain preserved.
7. If the issue, catalog or policy changed, choose **Cancel remainder and regenerate**.
   This keeps past actions, creates a new version and requires new approval. Inspect
   the draft for redundant comments before approving. It does not undo GitHub writes.

A proposal claiming no missing information can still be wrong. For the supplied bug
example, the source has no software version. Recovery does not correct model reasoning;
use a new reviewed proposal when its content needs changing.

## Browser acceptance after upgrading

- Connect once: Overview hides the token entry and shows the connected workspace.
- Refresh the page: the same tab reconnects without entering the token again.
- Disconnect, then refresh: token entry returns; the previous workspace is cleared.
- Check light and dark themes, Review and Activity at your normal screen size.
- Audit timeline is newest-first with a fixed-height internal scroll area; scrolling
  within it should not force the entire page to grow with every new event.
- A blocked case shows recovery choices and approved payload, with the original model
  draft available separately. Confirm a resumed case does not duplicate a DONE comment.

This release does not change the database schema. Keep `.env`, data, secrets, artifacts
and Docker volumes. Do not run `docker compose down -v` during this upgrade.
