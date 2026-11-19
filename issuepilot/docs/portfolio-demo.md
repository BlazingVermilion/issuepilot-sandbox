# Demonstrate IssuePilot with the VS Code corpus

A case starts from a frozen historical report, not a manually written issue. The ordinary n8n, FastAPI, PostgreSQL, LangGraph and Ollama services run locally. The original historical issue number is preserved. Approvals write only to this sandbox; no new issue, label or comment is published to GitHub. The public issue link is for provenance; its current content can differ from the snapshot.

## Start on Windows

Use a **fresh clone** when an older checkout contains uncommitted edits. From the Git root, use Python 3.12, Docker Desktop and Ollama. The root launcher works without host packages. You do not need an old archive, a recovery download or a GitHub token.

```powershell
ollama list
# Only if qwen3:8b is absent: ollama pull qwen3:8b
py -3.12 demo.py start
```

The first run downloads three pinned splits, verifies their hashes and 1,993 unique issue numbers, then builds the runtime. The Docker input contains the issue number, time, split, title and body; it excludes gold labels and historical assistant messages. The hashes are checked before use. If you already have the exact source files, `demo.py start --source <directory-or-original-benchmark.zip>` also works. `demo.py prepare` performs just the dataset step.

1. Open <http://localhost:5698>, create/sign in to the local n8n owner, and import **issuepilot/n8n/github-intake.json** from the Git root once. Publish/activate it. The sender uses the production webhook `/webhook/issuepilot-github`, not n8n's test listener.
2. Open <http://localhost:8021> → **Overview → Connect**. Copy only the `maintainer` token from the private file whose location the launcher prints. On Windows this is `%LOCALAPPDATA%\IssuePilot\issuepilot-replay-full1993\.env.replay`. GitHub writes are simulated. Do not share the file or a screenshot of its values.
3. Run **`py -3.12 demo.py all`** to submit all 1,993 issues; no pilot or model smoke is required. Run **`py -3.12 demo.py status`** from the Git root, also in a second terminal while the worker is busy. A re-delivery of the same issue does not create another case.

**Troubleshooting:** use `py -3.12 demo.py logs`. If `all` reports an unregistered webhook, publish the workflow in **this** n8n instance and repeat `all`. A Git clone does not reset Docker volumes. The root launcher uses the separate `issuepilot-replay-full1993` project and preserves its configuration between checkouts. If its configuration goes missing while volumes exist, restore that file from your backup. The launcher refuses to invent a replacement password. PostgreSQL health now tests a real TCP login. For the legacy 8011/5688 project only, `demo.py recover --from <original-.env.replay>` verifies the original credentials before restoring them; continue that instance with `issuepilot/scripts/demo.py`. Do not mix the two launchers. No volume deletion is necessary.

**What to measure:** `received` means durable intake, `latest_model_classification` / `latest_model_rag` mean a persisted latest proposal whose recorded telemetry shows that actual model stage. An abstaining classifier can legitimately skip RAG. `distinct_cases_reviewed` requires a real human decision. Review three proposals manually: approve/edit/reject only as justified. Future reporter replies and historical maintainer decisions are not supplied by the dataset. Do not silently simulate them as truth. Use `pause`/`resume` to stop/restart only the worker; `stop` stops the isolated stack without deleting volumes. Use `logs` if a service fails; do not publish raw logs.

## Snowflake: show an operational answer rather than an object browser

1. After at least one real proposal and review, run `py -3.12 demo.py export`. This reports the local export path (on Windows: `%LOCALAPPDATA%\IssuePilot\issuepilot-replay-full1993\artifacts\analytics\events.json`); `EXPORTED` is **not** Snowflake verification. Raw issue descriptions and comments are excluded. Older RDI tables are not used.
2. In Snowsight, open a SQL worksheet. Run `issuepilot/warehouse/setup.sql` once under a provisioning role with warehouse, database, role and grant privileges. Execute its final commented grant with your actual key-pair username. The runtime uses `ISSUEPILOT_SYNC`, `ISSUEPILOT_WH` and `ISSUEPILOT_DB.ANALYTICS.TRIAGE_EVENTS`. A matching private key must already be registered to the user.
3. Privately place the matching key at `issuepilot/secrets/snowflake.p8`. In the PowerShell session set `$env:IP_SF_ACCOUNT` and `$env:IP_SF_USER`. For an encrypted key, set `IP_SF_PRIVATE_KEY_PASSWORD` locally as described in the operator runbook. Docker must be able to read that file. The Snowflake calls below contact your account.
4. For a frozen comparison: finish the demo reviews, stop sending deliveries, then pause the worker. Run the two syncs against the same isolated database:

```powershell
py -3.12 demo.py pause
py -3.12 demo.py sync
py -3.12 demo.py sync
py -3.12 demo.py status
```

Both syncs must say `VERIFIED` with the same batch hash and count; `inserted` must be 0 on the second one. The first insert count can also be 0 if the batch already existed. In `issuepilot/warehouse/replay.sql` replace `PASTE_RUN_ID_HERE` with your exact `run_id`, run the SET and SELECTs in the same worksheet session. Check received issues against local status, zero duplicate event IDs, genuinely reviewed cases and latency. An empty review denominator produces a null edit rate. The historical SQL measure "ever failed" may remain nonzero after a successful retry. The model-proposal count is historical; the CLI counts the latest saved version. `resume` restarts the worker. Do not call an edit/rejection rate classification accuracy.

## CI/CD: local preparation now, real evidence after publication

The root of this Git checkout contains the active GitHub Actions workflows. Open the Actions tab on the exact commit: tests run on pushes and pull requests. Local model runs, warehouse syncs and deployments each require their own evidence.

- **CI:** push the branch and open a PR when you choose. GitHub Actions → **CI** → **tests** and **container**. The first job runs lint, tests with SQLite and a dedicated PostgreSQL/checkpointer and rules controls. The other builds and boots Docker, probes health and checks the tokenizer. CI does not generate Qwen benchmark results or use your laptop's GPU.
- **Release / disposable staging:** after the approved commit is on the default branch, publish a version tag (for example `v0.5.3`, once). **Release image** runs CI, builds/publishes GHCR, and **verify-release** pulls the published digest, checks the source-commit label, starts that exact image with PostgreSQL and checks health. Capture the successful run summary containing commit and digest. A staging runner disappears after the job.
- **Persistent deployment, only if you have a host:** provision Linux/Docker, DNS/TLS, PostgreSQL storage and an Ollama host accessible from the worker. Clone the reviewed release commit at `/opt/issuepilot` (Compose in `/opt/issuepilot/issuepilot`). Prepare private runtime configuration incl. `APP_DOMAIN` and `WEBHOOK_DOMAIN`, registry read access if private, and SSH access for a deploy user. Protect the `production` GitHub Environment with required review where available. Set `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` there. Run **Deploy approved image → Run workflow** against the reviewed default branch, paste the exact verified `ghcr.io/owner/repo@sha256:…`. Host checkout must match the image's OCI source commit. Verify its HTTPS health and a real model smoke on the host; a runner alone is not a persistent deployment. Rollback uses the previous checked-out release and its verified digest. GitHub Actions status is never inferred from the local console.

## Capture once; select four images for LinkedIn

| Order and filename | Screen and framing | What the image actually proves |
|---|---|---|
| 1 · `01-review.png` | **Review** of a real received report: number, a short source excerpt, `Ollama · qwen3:8b`, proposal and one cited policy section. | A report reached the model and the maintainer can inspect its basis. |
| 2 · `02-activity.png` | Same case → **Open audit history**. Show received, proposed, human decision and the result. If needed scroll inside the timeline or collapse event details. | Human decision and recorded local actions, not a comment posted to GitHub. |
| 3 · `03-snowflake.png` | Snowsight `replay.sql`, cohort query and result in one frame. Include received, model proposals, reviewed and duplicate IDs. | Actual warehouse readback and the difference between intake and inference. |
| 4 · `04-release.png` | **Release image** → checks, publish, verify-release + digest/commit in Summary. Use a real successful deployment instead if available. | Green checks, a traceable release and the environment actually tested. |

For the GitHub README, the same four are enough. Optionally add one small Inbox screenshot showing `1993` received, or the n8n workflow plus the CLI's duplicate receipt. A workflow diagram on its own does not demonstrate execution. Triggered workflow may not retain execution data: this project disables saving successful raw n8n payloads. Keep screenshots legible in a small thumbnail; crop other tabs, paths, personal text or identifiers unrelated to the example, emails, keys and tokens. Do not screenshot a credentials or request-header panel. Do not attach the full corpus.

**LinkedIn:** Me → View profile → Add section → Add project (some layouts: Recommended → Projects) → enter details → Add media. Add the images in order, link the actual release/README once public, then Save. Suggested title: **IssuePilot — Human-Reviewed AI Triage for GitHub Issues**. Pick five skills you actually demonstrate, for example Python, LangGraph, Retrieval-Augmented Generation (RAG), GitHub Actions and Snowflake. Only after the corresponding runs, adapt this English description:

> Built an issue-triage assistant that replays historical VS Code reports through signed n8n webhooks. Qwen3:8B classifies issue intent, BM25 retrieves relevant policy sections, and the maintainer can inspect the evidence before acting. Replayed [received] reports; [proposed] received model proposals and [reviewed] were reviewed. Mirrored audit events to Snowflake and verified a second sync added no duplicates. Published a Docker image through GitHub Actions and verified the released digest in staging.

On the frozen, separate 200-issue classification test, the selected profile got 173/200 correct (86.5%; macro-F1 0.8691). The 1,993 include 1,593 train, 200 validation and 200 test records; do not present all of them as unseen, or operational reviews as independent ground truth. RAG gold lives in a separate scenario suite. No time savings, live GitHub deliveries, production deployment or online accuracy should be claimed without the corresponding evidence.

References: [source dataset](https://huggingface.co/datasets/Tilakoid/vscode-bug-feature-triage), [LinkedIn Projects](https://www.linkedin.com/help/linkedin/answer/a8064614), [GitHub container publication](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images).
