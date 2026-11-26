# IssuePilot evaluation targets and improvement plan

These are project acceptance targets, not externally certified industry thresholds.

## Scope and measurement rules

Classification proposes an issue category. RAG retrieves policy and provides supported recommendations. Backend code enforces authorization, timestamps, source freshness and workflow state before any external action. Automatic eligible closure does not require redundant maintainer approval, but all applicable deterministic gates must pass.

Keep baseline artifacts immutable. Freeze model digest, prompt, schema, policy, retrieval settings and split before each run. Errors and abstentions are not silently dropped. Report raw output, first-attempt contract validity, post-retry validity and semantic quality separately. No retries may inspect gold answers. If retries are introduced, count all latency/tokens and cap attempts. Report fixed-class confusion matrices and denominators. Latency targets below are local, sequential pilot goals, not multi-user service SLAs.

## Classification acceptance

| Metric | Development gate | Acceptance target |
|---|---:|---:|
| All-issue accuracy / macro-F1 | 85% / 0.85 | 90% / 0.90 |
| Bug precision / recall | 85% / 90% | 90% / 95% |
| F1 for each supported class | 0.80 | 0.88 |
| First-attempt valid output | 99% | 99.5% |
| Final technical error rate | ≤1% | ≤0.5% |
| Accuracy among accepted / coverage | Report both | ≥95% / ≥80% for selective use |
| Out-of-domain review recall | Requires independent OOD cases | ≥95% |
| Warm latency p95 | ≤10 s locally | Set for deployment load |

Report the confusion matrix (including errors and abstentions), per-class support, and whether results come from an original holdout or the combined corpus. The canonical source is specified by [its manifest](fixtures/dataset_source.json).

## Retrieval, answers and operations

| Metric | Development gate | Acceptance target |
|---|---:|---:|
| Recall@4 / MRR@4 / nDCG@4 | 90% / .85 / .85 | 95% / .90 / .90 |
| Precision@4 | Report | Report; no arbitrary threshold |
| Final evidence recall / queries with complete context | 95% / 90% | 98% / 95% |
| Critical closure cases missing required context | 0 observed | 0 observed |
| First-attempt RAG contract validity | 99% | 99.5% |
| All-case action accuracy / per-verdict recall | 90% / 85% | 95% / 90% |
| Correctness / completeness / groundedness | 90% / 85% / 95% | 95% / 90% / 98% |
| Citation ID validity / semantic precision / recall | 100% / 95% / 90% | 100% / 98% / 95% |
| False answer on unsupported / false abstention on supported | ≤5% / ≤10% | ≤2% / ≤5% |
| Semantic task success | 85% | 90% |
| Unsafe recommendations / unauthorized writes / duplicate confirmed actions | 0 observed | Release blocker if observed in regression tests |
| Local sequential end-to-end / retrieval p95 | ≤15 s / ≤1 s | Establish a separate deployment SLO |
| Technical errors | ≤1% | ≤0.5% |
| Traceable model, policy and configuration | 100% | 100% |

The [quality assessment](quality_gates.py) checks development thresholds, sample counts and available evidence. Passing it is not a production certification. Measure ranking before dependency expansion and final context after expansion. Report latency p50/p95, token counts, warm and cold starts, judge overhead and cost when known. Never infer semantic support from a syntactically valid citation. An uncalibrated judge or an empty denominator produces an unmeasured result, not zero errors. Two systems evaluated on the same cases need a paired comparison; scores from the same holdout are not independent.

The [serving profile](../issuepilot/docs/ai-selection.md) gives the measured values for the selected model and their limitations. Freeze data, policy, prompt and targets before making a holdout claim.
