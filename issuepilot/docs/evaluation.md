# Evaluation and evidence

## Questions to answer

1. Does the system enforce its workflow contract regardless of model output?
2. Does an LLM improve triage decisions relative to the fixed workflow?
3. Does it reduce useful maintainer effort at an acceptable latency/cost?

These are different questions. Passing API tests cannot answer model-quality or
productivity questions. A polished UI is not a substitute for either.

## Executed local tests

The delivery includes `docs/validation.json` recording actual commands and results.
API/worker/SQLite-checkpoint tests exercise signed ingress, duplicate delivery,
repo allowlist, reviewer auth, version conflicts, source/policy staleness,
approval/rejection, permission validation, durable graph resume, ambiguity after
remote acceptance, reconciliation, and metadata export filtering.

The LLM tool-calling test uses a fake model to exercise orchestration. It is **not**
a Qwen evaluation. The PostgreSQL and container CI jobs are supplied but require a
runner with PostgreSQL/Docker; their status must be recorded after they actually run.

## Dataset

`fixtures/evaluation.json` contains 20 authored synthetic cases and explicit expected
category, missing-field set and abstention outcome. Cases include natural-language
versions, conflicting keywords, vague requests, a documentation typo, non-English
text, and an instruction-injection attempt. They use the included synthetic policy.

These fixtures were authored for this project. They are not a random sample of
enterprise tickets, not historical GitHub ground truth, and not enough to claim broad
accuracy. Do not tune against this set and still call it an untouched holdout. Before
optimizing prompts, create a separate development set and freeze a larger blind test
set with independently reviewed labels.

## Baseline result

A recorded run of the initial fixed workflow on the 20 fixtures yielded:

| Metric | Result |
|---|---:|
| Contract-valid outputs | 20/20 (100%) |
| Correct category | 18/20 (90%) |
| Exact missing-field set | 16/20 (80%) |
| Correct abstention decision | 19/20 (95%) |

These results describe **rules**, not LLM accuracy. The initial regex baseline makes
mistakes on natural phrasing, empty headings, mixed-intent keywords and Vietnamese.
They are useful targets for the LLM comparison, not evidence that the LLM wins.

Run the two evaluation commands from the runbook. Retain raw proposals, traces,
policy hash, model tag and output JSON. Report all 20 cases, including failures.
Record Ollama's exact model digest separately (`ollama show`) because a tag can change.
Do not automatically switch the deployed engine based on this small fixture set.

## Next evaluation gate before portfolio claims

- Collect public or authored issues you have the right to use, with a documented
  policy. Record provenance and anonymize where necessary.
- Label intent, genuinely missing required fields, permitted action and supporting
  policy passage. Have a second reviewer resolve ambiguous labels if possible.
- Freeze a test set separate from prompt development. Group similar/duplicate issues
  together to avoid near-duplicate leakage.
- Compare rules, a fixed LLM workflow, and tool-calling agent under the same policy,
  same issue snapshots and model, using several repeats for nondeterminism.
- Report category macro-F1, missing-field precision/recall, unnecessary-question rate,
  invalid-policy-citation rate, abstention coverage, median/p95 latency and model cost.
- For productivity, time the same review task with/without assistance using randomized
  order and report sample size. Approval/edit rates alone are not time savings.

No routing improvement, security guarantee, LLM score or time-saving percentage has
been claimed in this initial delivery. Snowflake metrics are operational observations,
not causal proof that an agent helped a team.
