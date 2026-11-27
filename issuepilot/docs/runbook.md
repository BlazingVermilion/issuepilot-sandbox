# Operating IssuePilot

For normal use, follow [Run on Windows](../../README.md#run-on-windows) from the extracted project. On Linux/macOS substitute `python3.12` for `py -3.12`. The CLI prints the location of local files when clipboard copying is unavailable.

## Local services and intake

The application uses port 8021. n8n stays at the local address in the editor URL you supplied. If n8n runs in Docker, the launcher identifies the container bound to that port and attaches it and the API to an internal bridge. Only the intake nodes use the DNS name `issuepilot-intake-api`. With a native n8n process the callback is on loopback. Do not use the editor URL or n8n's temporary test URL as the sender endpoint.

| Command (after `py -3.12 manage.py`) | What it verifies or changes |
|---|---|
| `workflow` | Copies the three linked nodes for the detected runtime. Paste into the existing canvas once, then publish. |
| `check` | A signed challenge followed the published route and the configured workflow returned the backend's signed proof. |
| `submit --resume` | Submits the source's missing deliveries. It verifies PostgreSQL receipts, even if the previous command stopped mid-batch. |
| `status` | Counts issues received, model stages recorded and issues reviewed. Queued issues have not yet been classified. |
| `verify-model` | Calls the installed model on a source issue and checks the selected inference path. Add `--require-gpu` to require observed GPU offload. |
| `reviewer` | Copies the local reviewer token on Windows. Disconnect in the console to end the reviewer session. |
| `logs` | Shows recent errors from the worker, API and PostgreSQL. |
| `pause` / `resume` | Stops / restarts the inference worker; this is separate from resuming submission. |
| `stop` / `start` | Stops / starts the case services and retains the database. Existing n8n is left running. |
| `configure --workflow URL` | Selects a different local workflow deliberately. Run `start`, install its nodes and check it. |

Run `py -3.12 manage.py settings-path` to print the path without revealing any values. On Windows the active settings are in `%LOCALAPPDATA%/IssuePilot/workspace/settings.env`. Keep secrets private. If the launcher finds one matching earlier data store and its credentials, it uses the **same Docker volume, case identities and reviewer credentials**. Its physical Docker name can therefore remain unchanged. If the original credentials and volume do not match, the launcher stops instead of silently opening a different database. Use the Docker context that owns the original volume. Do not delete volumes to change their display names.

The latest intake summary is stored under `artifacts/intake/summary.json` in the user's workspace. PostgreSQL holds the durable per-case history. Posting a case does not mean the worker completed a model call or that a person approved anything.

## Diagnose the failed step

- **Webhook 404:** Open the configured workflow. Its Webhook node must show method POST, path `issuepilot/issues` and Production URL ending in `/webhook/issuepilot/issues`. Publish the version containing the connected nodes, then use `check`. Do not send the corpus until the check succeeds.
- **Webhook runs but no receipt:** Inspect the last node in that execution. A Docker-hosted n8n should call `http://issuepilot-intake-api:8000/webhooks/n8n/intake`; a native installation should call `http://127.0.0.1:8021/webhooks/n8n/intake`. Re-copy the generated nodes if the installation type changed. Keep the response set to the last node and preserve raw request bytes.
- **Cases arrive without drafts:** Run `status`, `logs` and `verify-model`. Confirm Ollama has the configured local model. Fix the underlying error before generating a fresh proposal for a failed case. Queueing the corpus is faster than running inference.
- **External action interrupted (live sandbox):** Open the affected case's audit history. Repair credentials or connectivity, then use **Recover execution**. Reconcile against GitHub before retrying uncertain actions; completed actions are retained. If source or policy changed, regenerate and review a fresh proposal. The snapshot workspace simulates approved actions locally.

## Snowflake

Follow the [Windows and Snowsight walkthrough](snowflake.md) for first-time setup. An authorized account administrator provisions [setup.sql](../warehouse/setup.sql), grants the sync role to the key-pair user, and sets warehouse permissions. Place the user's private key at `issuepilot/secrets/snowflake.p8` (readable by container UID 10001); configure `IP_SF_ACCOUNT`, `IP_SF_USER` and, if needed, `IP_SF_PRIVATE_KEY_PASSWORD` in the local workspace settings or process environment. Defaults for role, warehouse, database and schema are in [Compose](../compose.yaml). No key is provided.

From the project root, `py -3.12 manage.py check-snowflake` uses the same service connection as sync. It explicitly selects the configured database, schema and warehouse, checks the active role and reads the target columns with `LIMIT 0`. `CONTEXT_OK` confirms the destination is readable. It does not check INSERT, modify records or export PostgreSQL events. If a step fails, fix the named object or grant in Snowsight. Selecting a database in a browser worksheet does not configure the container's session.

`py -3.12 manage.py export` creates a local metadata-only extract. `py -3.12 manage.py sync` repeats the context check in its own session, then writes missing events and checks their contents. Only `VERIFIED` confirms remote readback and commit. Repeat with a stable source to check idempotence. In Snowsight use [intake_metrics.sql](../warehouse/intake_metrics.sql) and locate the workspace ID printed by `status` in the results. Each SELECT works in a fresh worksheet; no session variable is required. Check received issues, cases with a model proposal, reviews and duplicate event IDs separately. Historical events without a recorded source size produce NULL if there are no newer receipts. [metrics.sql](../warehouse/metrics.sql) covers broader event analytics.

## GitHub sandbox and deployment

The snapshot workspace makes no changes to GitHub. For the separately configured live app, use the ordinary Compose configuration in the application folder and `scripts/init_env.py` to create local settings once. Set `IP_MODE=live`, the exact sandbox `IP_REPO`, a fine-grained token limited to that repository and Issues permission, `IP_ENGINE=ollama` and `IP_MODEL=qwen3:8b`. Enable `IP_WRITES_ENABLED=true` only for the intended sandbox. Give the GitHub webhook the same signing secret as the API. Use the separate [GitHub webhook template](../n8n/github-intake.json); set its HTTP node to the reachable API `/webhooks/n8n` and publish it. Set GitHub’s payload URL to your HTTPS n8n host plus `/webhook/issuepilot-github`. The signed snapshot workflow at `/webhook/issuepilot/issues` has a different envelope. The production Compose deployment includes its own n8n service only when explicitly using its automation profile on a provisioned host; the local workspace does not use it.

CI runs backend tests on SQLite and PostgreSQL, workflow regressions, portable benchmark checks, UI tests and container smoke checks. A Git tag matching `v*` triggers CI, a GHCR image, and a staging digest check. The optional deploy workflow requires a prepared host, matching checkout, environment protection and the SSH secrets listed in [deploy.yml](../../.github/workflows/deploy.yml). It deploys the chosen digest and checks API health. An offline ZIP cannot establish a GitHub Actions run, a successful hosted deployment or a Snowflake sync.
