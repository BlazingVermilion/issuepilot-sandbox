> Historical v0.4 rules controls. For the current selected AI profile and measured targets, see [AI selection](ai-selection.md). Evaluation is offline only.

# Evaluation contract — VS Code profile v0.4

Classification and workflow quality are different questions. Every primary scorer is
automatic; none requires a human judge. A scripted reviewer is used only in isolated
tests. Production GitHub writes still require authenticated human approval.

## Data and provenance

Layer A uses the third-party [Tilakoid VS Code dataset](https://huggingface.co/datasets/Tilakoid/vscode-bug-feature-triage),
not a Microsoft-maintained benchmark. `fixtures/dataset_source.json` pins commit
`15c7d77e083d0cd30ae84cc5de6add1dce6cf950` and SHA256 for every split.
Downloaded counts: 1,593 train (796 bug / 797 feature-request), 200 validation
(100 / 100), 200 test (100 / 100). Only `input` enters the predictor. `messages`
contains assistant answers and is deliberately excluded. No live GitHub tools run.

The audit checks hashes, counts, schema, issue IDs, normalized exact text and
cross-split near-duplicate candidates (word 5-shingles, 12 minimum SHA signatures,
Jaccard >= 0.85). The delivered audit found zero ID/exact-text duplicates and zero
near-duplicate candidates. This heuristic cannot prove the absence of semantic
leakage. Creation-time splits do not establish that bodies are opening-time snapshots.
Labels are inherited silver references, not independently verified truth. Inputs may
contain incidental label cues; no claim of leak-free natural-language gold is made.
The frozen test split was audited but **not scored** in this delivery.

Raw issue text and full upstream documents are not redistributed in this package.
Use fetch manifests locally; review source terms before publishing derived text.
Public availability is not a redistribution license. JSON/CSV reports retain IDs,
predictions and synthetic scenarios; Layer A does not export issue bodies.

## Layer A — classification component

The LLM is pretrained Qwen through Ollama; no classifier training or fine-tuning.
`Predictor.classify` in `src/issuepilot/benchmarks.py` uses a deliberately small
`Classification` schema (category + abstain). This isolates classification from
comment generation and tool execution. The deployed `Analyst.llm` uses the larger
`Proposal` schema; its policy adherence and workflow are evaluated in B1/B2. Do not
call A's schema-valid rate a full-production-Proposal pass rate.

Arms: `rules`, `none` (LLM without policy), `full` (same LLM, entire frozen policy),
`bm25` (same LLM, top four policy chunks). No-policy still receives the classification
task/schema. Feature requests use `feature-request` directly; legacy `enhancement`
is mapped only at the scoring boundary. GitHub label permission is a separate check.

| Metric | Denominator / calculation |
|---|---|
| Primary macro-F1 | Mean class F1 for bug and feature-request; OTHER/ABSTAIN/ERROR contribute false negatives |
| Accuracy | Correct / all scheduled records |
| Class precision, recall, F1 | Fixed confusion matrix with OTHER, ABSTAIN, ERROR columns |
| Coverage | In-scope predictions / scheduled records |
| Selective accuracy | Correct / covered, null when coverage is zero |
| Contract, error, timeout rates | All scheduled records; errors are retained |
| Latency | p50/p95 for all attempts and non-error attempts separately |
| Usage | Per-attempt calls and measured tokens; unknown telemetry is null |
| Uncertainty | 500 issue-bootstrap samples, seed 42, percentile 95% interval |
| Paired comparison | Same ordered IDs/references, policy, dataset and split; paired bootstrap delta |

Classification uses temperature 0, output budget 1,600 tokens, Ollama context 32,768,
character budget 60,000 including context. Oversized inputs fail instead of being
silently shortened by our code. BM25 uses the first 5,000 query characters; the model
still gets the complete eligible input. Backend tokenization/truncation is not fully
observable; inspect prompt token telemetry and reduce input size if necessary.
Model digest is queried from `/api/tags`; missing digest remains null. Tag names alone
are not immutable model identifiers. Usage is measured inference usage, not price.

Choose prompts/settings on validation only. Run each chosen LLM arm three times with
distinct output directories; retain every report, including unsuccessful ones. Use
`compare` for paired deltas; do not report only the best repeat. `--test-locked` is an
explicit operator acknowledgement, not cryptographic prevention of test-set tuning.

## Layer B1 — retrieval and policy application

The main corpus is `policies/triage.md`: an **IssuePilot adaptation** of public VS Code
workflow, with source URLs and explicit local contracts. It does not reproduce all
Microsoft policies or automatically close issues. `policies/sources.json` fingerprints
upstream evidence; `scripts/fetch_policy_sources.py` downloads hash-checked originals
for inspection. Those originals are not automatically indexed. Each local section
has a stable heading-derived `section_id` and content hash; positional citation IDs
remain for compatibility. Corpus edits invalidate pending approvals.

| Track / metric | Reference and scope |
|---|---|
| Controlled Recall@1/4/8 | Seven authored queries with known source sections; macro-average over relevant sets |
| Linked-source Hit@k / MRR | Optional eligible historical maintainer section links; **zero real cases collected so far** |
| Effective k / unreachable refs | Corpus size and unreachable targets disclosed; absent targets are misses |
| Citation provenance | ID/hash/quote membership, both pre-validator attempts and accepted outputs |
| Citation coverage | Accepted actionable proposals with valid evidence / accepted actionable proposals |
| Missing-field micro-F1 / exact-set accuracy | Independent synthetic scenario references; errors count as wrong/missing |
| Already-known requests | Requested structured fields intersecting known fields / requested fields |
| Next-step / feature-area accuracy | Controlled scenario references, not real-world routing accuracy |
| No-evidence violations | Actionable accepted output on unsupported cases / unsupported cases; paired with contract rate |

Seven policy-application cases cover absent/partial/complete bug facts, extensions,
feature requests, terminal routing and unsupported intent. They are small authored
probes, not a held-out generalization benchmark. Their expected fields are stored in
fixtures and never passed to `Analyst.draft`. Exact citation validity proves provenance,
not that a quote logically supports a recommendation. Raw rejected proposals are
retained when parsing succeeded. An unavailable model can have zero accepted unsafe
outputs but a zero contract rate; never call that successful abstention.

`links` extracts an optional silver subset from locally supplied historical snapshots:
`id`, `title`, `body`, `opening_snapshot_verified`, `snapshot_provenance`, and `comments`
with `id`, `body`, `created_at`, `author_association`. Only OWNER/MEMBER/COLLABORATOR
comments linking a known explicit section qualify. The linked comment and later
responses are hidden from input. Generic links and missing/unverified snapshots are
excluded with counts. A supplied verification flag is an acquisition contract, not
independent proof of archival correctness. Current GitHub bodies cannot substitute
for verified opening history. No synthetic fixture is relabeled as real linked data.

## Layer B2 — actual compiled graph and action ledger

The runner executes the production graph with SQLite checkpoints and separately the
real API/worker/ledger with a fault-injected local GitHub adapter. No network writes.
Every graph call reopens the checkpoint, including reviewer and reporter wait states.
There are 18 named episodes: complete/incomplete/partial input, rejection, two restart
boundaries, wrong resume, round exhaustion, unauthorized decision, duplicate delivery,
403 recovery, accepted-but-timed-out comment, failed source read, stale source/policy,
reviewer edit, reporter resume and reordered unchanged updates.

| Metric | Actual check |
|---|---|
| Episode success | Expected boundary plus field/action assertions / all 18 episodes |
| Transition conformance | Allowed observed checkpoint transitions / 8 graph episodes |
| Resume correctness | Correct wait node, rounds and facts after reopening persistent checkpoints |
| Unauthorized attempts | Instrumented adapter writes without matching approved current version |
| Confirmed-action duplicates | Attempts matching an already-DONE current-version action |
| Event idempotency | Duplicate signed delivery produces no additional logical effect |
| Recovery success | Read/403/unknown-response fault cases reach expected repaired state |
| Bounded termination | Eight graph episodes reach specified terminal or waiting boundary within budget |
| Persistence control | Same two waits using recreated in-memory savers lose resumable state |

Graph-only execution receipts are scripted. Ledger scenarios use actual executor code
and simulated GitHub effects. `pytest` additionally tests absent unknown-write readback
requiring explicit acknowledgement, source/case version conflicts and permission rules.
This finite suite is not an exactly-once guarantee or proof against all failures.
Use the separate ai-bench for model comparisons; these B1/B2 regression fixtures run with rules. There is no measured maintainer time saving or closure rate.

## Current measurements and acceptance

See `docs/benchmark-results/` for executed rules reports and `docs/validation.json`
for command evidence. On all 200 validation issues, rules macro-F1 is **0.4082**,
accuracy **34.0%**, coverage **50.5%**, selective accuracy **67.33%**. This baseline
is intentionally weak and is not evidence that the LLM wins. Model comparisons must
be run on the user's Ollama runtime; no Qwen results are fabricated.

Security gates are zero unauthorized attempts and zero confirmed duplicates in the
specified tests. Quality improvement has no invented 95% target: compare paired
accuracy/F1, coverage, retrieval and latency with uncertainty. No blended A+B score.
NLBSE, hybrid retrieval, independent LLM judging and human productivity measurements
remain outside this release. Semantic faithfulness/helpfulness remain UNMEASURED.
