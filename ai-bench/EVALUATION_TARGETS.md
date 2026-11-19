# IssuePilot evaluation targets and improvement plan

Version 1.1 (2026-11-18). These are project acceptance targets. Repository documentation language is English.

## Scope and measurement rules

Classification proposes an issue category. RAG retrieves policy and provides supported recommendations. Backend code enforces authorization, timestamps, source freshness and workflow state before any external action. Automatic eligible closure does not require redundant maintainer approval, but all applicable deterministic gates must pass.

Keep baseline artifacts immutable. Freeze model digest, prompt, schema, policy, retrieval settings and split before each run. Errors and abstentions are not silently dropped. Report raw output, first-attempt contract validity, post-retry validity and semantic quality separately. No retries may inspect gold answers. If retries are introduced, count all latency/tokens and cap attempts. Report fixed-class confusion matrices and denominators. Latency targets below are local, sequential pilot goals, not multi-user service SLAs.

## A. Classification targets

Current benchmark covers bug and feature-request only. Other production categories require a separate evaluation set. Baseline: Qwen2.5:7b, mode=none, seed=42, no fine-tuning demonstrated. Test summary(5).json and manifest(3).json uploaded 2026-11-13; test has 100 examples per class.

| Metric | Current test | Pilot target | Production acceptance target |
|---|---:|---:|---:|
| Accuracy, all examples | 84.00% | >=85% | >=90% |
| Macro-F1, fixed two classes | 0.8441 | >=0.85 | >=0.90 |
| Bug precision | 85.42% | >=85% | >=90% |
| Bug recall | 82.00% | >=90% | >=95% |
| Bug F1 | 0.8367 | >=0.80 | >=0.88 |
| Feature-request precision | 84.31% | Monitor | Monitor |
| Feature-request recall | 86.00% | Monitor | Monitor |
| Feature-request F1 | 0.8515 | >=0.80 | >=0.88 |
| First-attempt valid output | Not separately established | >=99% | >=99.5% |
| Technical errors after bounded retry | 0/200 reported; retry breakdown unknown | <=1% | <=0.5% |
| Accepted-subset accuracy | 84.85% (168/198) | Monitor | >=95% if selective classification is used |
| Coverage | 99% | Monitor jointly with accuracy | >=80% if selective classification is used |
| Out-of-domain review recall | Not measured | Separate OOD set | >=95% |
| Warm latency p95 | 2.95 seconds | <=10 seconds locally | Set for actual deployment load |

Test confusion: true bug -> 82 bug, 16 feature-request, 2 abstentions; true feature-request -> 14 bug, 86 feature-request, 0 abstentions. Test latency p50: 2.47 seconds. Dataset train split inference: n=1593, accuracy=89.96%, macro-F1=0.9067. Validation: n=200, accuracy=88.50%, macro-F1=0.8940. All splits together: 1778 correct, 184 confusions, 31 abstentions out of 1993; this aggregate is descriptive, not a held-out result. The train split name does not imply model training. Test results already inspected for development must not be represented as a fresh final holdout after further tuning.

## B1. Retrieval targets

RAG baseline: rag-validation-before-judge.zip, v0.2.1, Qwen2.5:7b, direct generation, k=4, CUDA, Qdrant 1.15.5. Seven arms x 36 validation cases = 252 records. Of 36 cases, 32 are policy-answerable and 26 require an action verdict. Hashes, record consistency and common configurations were checked; summaries were recomputed from predictions.

| Metric | Pilot | Production acceptance target |
|---|---:|---:|
| Recall@4 | >=90% | >=95% |
| MRR@4 | >=0.85 | >=0.90 |
| nDCG@4 | >=0.85 | >=0.90 |
| Final context reference recall | >=95% | >=98% |
| Queries with all required evidence in context | >=90% | >=95% |
| Missing mandatory evidence on critical closure regression cases | 0 observed | 0 observed |
| Precision@4 | Report, no fixed threshold | Report, no fixed threshold |

Thresholds apply to a fixed sectioning/relevance definition. Reference precision can penalize useful sources absent from provisional annotations. Missing critical evidence must block execution; zero observed misses is not proof of zero population risk.

| Arm | Recall@4 | Precision@4 | MRR@4 | nDCG@4 | Final context recall | All reference sources present |
|---|---:|---:|---:|---:|---:|---:|
| BM25 | 78.39% | 33.59% | 0.7891 | 0.7043 | 86.98% | 26/32 |
| Dense | 84.90% | 35.94% | 0.8177 | 0.7718 | 92.71% | 27/32 |
| Hybrid | 81.51% | 35.16% | 0.8724 | 0.7794 | 92.71% | 27/32 |
| Hybrid-rerank | 81.77% | 34.38% | 0.8542 | 0.7637 | 92.19% | 28/32 |

Also report Recall/Precision/MRR/nDCG at k=1 and k=8 as diagnostics. Full and Oracle have 100% reference context recall by construction, not competitive retrieval rankings. None has no supplied policy. None is an evidence-only negative control, not an unconstrained general-knowledge baseline.

## B2. Answer, citation and abstention targets

| Metric | Pilot | Production acceptance target |
|---|---:|---:|
| First-attempt contract validity | >=99% | >=99.5% |
| Action accuracy, failures counted wrong | >=90% | >=95% |
| Recall of each ALLOW/BLOCK/NEED_FACTS class | >=85% | >=90% |
| Claim correctness | >=90% | >=95% |
| Completeness | >=85% | >=90% |
| Groundedness | >=95% | >=98% |
| Citation ID validity | 100% | 100% |
| Semantic citation precision | >=95% | >=98% |
| Semantic citation recall | >=90% | >=95% |
| False-answer rate on unsupported questions | <=5% | <=2% |
| False-abstention rate on answerable questions | <=10% | <=5% |
| Semantic task success under fixed rubric | >=85% | >=90% |
| Unsafe recommendation on critical regression set | 0 observed | 0 observed |
| Unauthorized or policy-violating executed closure | 0 in integration tests | Release blocker if observed |

Judge-based numbers are estimates and require measured judge agreement, explicit coverage and failure rates. Citation ID validity alone is not entailment. Raw-action accuracy is diagnostic only: do not grant execution authority to invalid outputs. Unknown facts require NEED_FACTS; explicit failed prerequisites require BLOCK; lack of applicable policy requires abstention. A prohibition is an answer.

| Arm | Valid outputs /36 | Action correct /26 | Raw action correct /26 | False abstention /32 | Unsafe ALLOW /18 | End-to-end p95 seconds |
|---|---:|---:|---:|---:|---:|---:|
| BM25 | 27 | 4 | 10 | 13 | 1 | 7.72 |
| Dense | 29 | 4 | 10 | 15 | 1 | 8.64 |
| Hybrid | 29 | 4 | 9 | 15 | 1 | 9.50 |
| Hybrid-rerank | 27 | 4 | 12 | 13 | 1 | 8.59 |
| Full | 22 | 4 | 5 | 8 | 1 | 8.35 |
| Oracle | 26 | 5 | 10 | 12 | 1 | 7.42 |
| None | 29 | 0 | 3 | 26 | 0 | 4.51 |

All six policy-supplied arms failed the same deadline case RAG-003: effective deadline max(168,144+48)=192, not 168. No arm produced a correct raw NEED_FACTS verdict on the four such cases. All policy-supplied arms have 100% citation ID validity for measured citations; semantic citation quality is unmeasured. Four unsupported cases are far too few to establish a low false-answer rate; Oracle and None each have one error among them despite zero counted false answers.

63/252 outputs violate contracts: 41 answered outputs with missing claims or nonempty refusal_reason; 12 answered action tasks with null verdict; 8 claims missing required text/citations; 2 inconsistent abstentions. These are contract failures, not observed network/GPU failures. The generic error category currently obscures this distinction.

## C. Operations and evaluation integrity

| Gate | Proposed target |
|---|---|
| Local sequential RAG end-to-end p95 | <=15 seconds |
| Retrieval p95 | <=1 second; separate encoder, transport and database timing |
| Technical request errors after bounded retry | <=0.5% on a defined measurement window |
| Lost durable requests after restart | 0 in recovery tests |
| Duplicate external writes on replay/retry | 0 in idempotency tests |
| Unauthorized writes | 0 in permission tests |
| Versioned model/policy/config and trace | 100% |
| Cost and tokens | Report per request; do not invent zero electricity/hardware cost |
| Cold start vs warm latency | Report separately |
| Judge latency/tokens | Report separately from online inference |

Current dense-family retrieval p95 is about 2.10-2.19 seconds; the responsible stage is not yet isolated. Current end-to-end p95 meets the local pilot latency goal. No concurrency, execution, recovery or population error guarantees follow from this run.

## D. Evidence requirements

Keep validation for development and reserve unseen cases for final assessment. Split related/paraphrased scenarios by family. Preserve the existing 16 RAG test cases without further tuning on them, but expand evidence before broad claims. Suggested planning sizes: 300-500 varied policy scenarios and about 150 independent unsupported scenarios; these are not automatic sufficiency guarantees. With zero observed errors, approximately 150 independent samples only support an approximately 2% one-sided 95% upper bound. Report confidence intervals and support, not just point estimates. Many paraphrases of the same few cases are not independent evidence.

The original 12-case judge calibration checked basic rubric behavior. Subsequent reviews found that it passed judgments that accepted an incorrect computed deadline and an incorrect policy duration. Reference labels are provisional and automated scoring still depends on reliable references. Validate judge agreement on independently reviewed cases before accepting semantic quality estimates. Freeze the selected configuration after validation and evaluate on unseen cases.

## E. Root-cause assessment

1. Confirmed design weakness: generation schema permits combinations rejected by the later validator. Boolean answerability, nullable action and refusal_reason overlap semantically. The system prompt already distinguishes these concepts; repeating the same instruction alone is not an adequate repair.
2. Confirmed behavior: Qwen2.5:7b confuses prohibited action with unsupported policy, treats unknown facts as established, and misapplies deadline arithmetic. Attribution to model capacity alone is unproven because prompt/schema/context interact.
3. Retrieval is imperfect but not the dominant demonstrated bottleneck: Full and Oracle receive complete reference evidence yet action accuracy remains 4/26 and 5/26.
4. Small policy corpus is not itself a defect. The observed failed cases often have the necessary rules. Adding unrelated documents could add noise or conflicting authority. Policy ambiguity and normative dependency clarity still need auditing.
5. The evaluation set is small and authored. This limits confidence, representativeness and judge calibration; it does not explain away observed incorrect answers.

## F. Controlled improvement sequence (proposed, not implemented)

| Experiment | Change | Keep fixed | Evidence required |
|---|---|---|---|
| E0 | Preserve v0.2.1 baseline | Everything | Immutable original outputs and hashes |
| E1 | Replace overlapping response states with one discriminant; derive legacy fields deterministically; align generation schema and validator | Qwen2.5:7b, retrieval, policy, scenarios | Runtime-supported schema tests; first-pass validity; raw and valid action scores |
| E2 | Add a few disjoint generic examples and explicit known/unknown fact handling | E1 schema, same model and sources | Action confusion, false abstention, fabricated-fact audit; no benchmark-answer leakage |
| E3 | Compare evidence-first or structured fact/evidence extraction with direct response | Same model and retrieval; log additional calls | Accuracy gain vs latency/tokens, not just valid JSON |
| E4 | Compare generator models only after the interface is stable | Prompt/schema/policy/context and cases | Pilot batch fits hardware; fair quality and latency comparisons |
| E5 | Audit reference relevance, policy dependencies, context order and k; tune retrieval if still needed | Selected generator interface | Recall and complete-evidence coverage plus downstream correctness |
| E6 | Evaluate selected configurations on unseen cases; calibrate a hardware-feasible judge | Frozen configuration | Unseen results, semantic estimates and their limitations |

E1 candidate discriminants: ALLOW, BLOCK, NEED_FACTS, EXPLANATION, NO_POLICY, constrained by caller task. Derive answerability and nullable legacy action from that state; use a shared explanation field instead of overloading refusal_reason. Require evidence for answered states, with explicit distinct unsupported-state rules. Test the actual Ollama JSON-schema subset; do not assume complex conditionals are enforced. This is deterministic structural normalization, not converting a semantically wrong decision into a correct one.

Run E1/E2 initially on Full and Oracle to isolate generation; then run all arms. Preserve raw responses and report post-validation outcomes separately. If bounded repair is tested, report it as its own setting, never conceal first-attempt failures. Revised output contracts are not identical protocols: compare shared semantic tasks and show versioned validity metrics separately.

Backend timestamp calculations, authorization, source freshness and current workflow state remain deterministic. Evaluate the combined tool-assisted executor separately from the LLM-only benchmark; do not credit rule-based corrections as improved LLM reasoning.

Hardware constraint: i7-13620H, 16 GB RAM class, RTX 5060 Laptop with 7.96 GiB VRAM. Keep Qwen2.5:7b baseline. Qwen3:32b failed loading with OOM and is not the routine local judge. Qwen3:8b is a candidate local judge only after tokenizer support, prompt budget checks and fresh calibration; no quality guarantee or replacement implementation is implied here.

## G. Pilot benchmark report

Run `python bench.py rag report --out <run> --judges <judge-runs>` after generating and judging the selected arms. The comparison verifies run identities and prediction hashes. The pilot assessment shows each measured value, threshold, sample support and status in `pilot.md` and `pilot.json`. To inspect an existing comparison without modifying its files, run `python pilot.py --comparison <run>/comparison.json`.

- A ranked arm passes the recorded benchmark targets when every applicable threshold passes. Full, Oracle and None provide diagnostic controls. The assessed split and the underlying case counts remain part of the result.
- Missing rank or latency observations, absent action classes, partial splits and stale judge runs cannot establish a pass. Judge coverage is shown both among valid generator outputs and across the planned cases. Semantic task success counts generator failures in its denominator and requires the correct action on applicable cases.
- First-attempt validity and final errors are reported separately. The final error target here is 1%. The raw-action diagnostic over all action cases counts missing raw decisions as wrong. Generation path counts and a model-only raw-action result show which cases called the model. The benchmark reports unsupported errors alongside the false-answer rate and counts an `ALLOW` on either `BLOCK` or `NEED_FACTS` as unsafe. The critical closure evidence check uses all answerable action cases annotated with an `IP-CLOSE-*` section and flags cases missing any reference evidence.
- The benchmark can measure recommendation behavior and local sequential latency. Executor integration, authorization, restart recovery and load require their own results. A validation pass supports configuration selection. Use a frozen, adequately sized holdout for pilot acceptance.

Prioritize the explanation defects found during the pilot runs. RAG-007 needs a single round count after a successful retry. RAG-013 needs the exact label changes for failed verification. RAG-015 needs the first reminder before escalation at the next assessment. Represent these policy-controlled transitions as typed facts or rules with source IDs, then measure whether the model preserves them in the answer.

Before using semantic scores for acceptance, have reviewers independently label a set of claims, required references and citation pairs, including contradictions and refusals. Evaluate the judge on that set alongside the 19 calibration cases. Once a judge qualifies, apply it to the saved generator outputs and report calibrated judge coverage. Use an additional holdout with new scenario families, paraphrases and unsupported topics to confirm the selected configuration.

## References for methodology (not numerical target endorsements)

- NIST AI RMF Core: https://airc.nist.gov/airmf-resources/airmf/5-sec-core/
- scikit-learn classification report: https://scikit-learn.org/stable/modules/generated/sklearn.metrics.classification_report.html
- Ragas metrics: https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/

Threshold changes must be explicit, versioned and justified by deployment needs, not made retrospectively to pass a weak result. This document records targets and a proposed plan; it does not modify the lab code or certify production readiness.
