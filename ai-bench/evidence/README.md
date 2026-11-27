# Evaluation evidence

[selection.json](selection.json) is a compact transcription of the archived run summaries used to select the serving profile. The `source` fields give the archive member names and the verbatim reports below contain the unrounded values. Displayed decimals are rounded where only the published precision is needed. Missing semantic and held-out generation results are null, never zero. [Read the analysis and targets](../../issuepilot/docs/ai-selection.md).

| Original file | Contents |
|---|---|
| [Qwen classifier](archive/classification-qwen3.json) · [same-split rules comparator](archive/classification-rules.json) | Overall and per-split metrics including confusion, support, latency, errors and abstentions. |
| [RAG validation](archive/rag-validation.json) · [quality gates](archive/rag-quality-gates.md) | Generation paths, decisions, citations, latency and the incomplete semantic gate. |
| [Held-out retrieval](archive/rag-retrieval-test.json) | Per-scenario ranked IDs, selected policy context and reference IDs. |
| [Judge calibration](archive/judge-calibration.json) | Expected versus matched calibration cases and false accepts. |
| [Checksums and original archive paths](archive/provenance.json) | Exact member byte lengths and SHA-256. No prompts, issue bodies, credentials or raw predictions are published. |

| Archived run | Question it can answer | Limitation |
|---|---|---|
| `classification-qwen3/{none,rules}` | How well does the selected classifier perform against the rule comparator on the same issue split? | Only two labeled categories and one repository. |
| `rag-policy-pilot-final-validation/policy--pilot-refined` | Does the final contract retrieve the policy and return the expected decision on development scenarios? | Selected on validation; 26 decisions within 36 scenarios. |
| `retrieval-policy-test/policy.json` | Does the retriever find the required evidence on held-out questions? | No held-out generation or human task success measured. |
| `judge-calibration-qwen25-v3` | Is the semantic judge reliable enough to report semantic quality? | 11/19 calibration cases passed; four false acceptances. The final arm was not semantically judged. |

[Recorded CPU rerun of the held-out retrieval set](ci-retrieval.json) · [CI run and per-scenario artifact](https://github.com/BlazingVermilion/issuepilot-sandbox/actions/runs/45928627277).

The CI **ai-bench** job reruns the selected lexical retriever on all 16 held-out questions using the checked-in policy and publishes `policy-retrieval-test` with per-question ranked IDs and references. The regression test checks aggregate coverage and ranking against the recorded figures, not timings. No generation or judging is performed by this check.

The serving tag/digest, policy hash, dataset revision, protocols and denominators are pinned in the summary and [source manifest](../fixtures/dataset_source.json). Online outcomes (case counts, approvals, mirror receipts) are recorded separately. Earlier rules-only application fixtures under `issuepilot/docs/benchmark-results/` are **not** Qwen evaluations. The [benchmark guide](../BENCHMARK.md) explains how to regenerate per-case results on your own model and data, without publishing private issue text.
