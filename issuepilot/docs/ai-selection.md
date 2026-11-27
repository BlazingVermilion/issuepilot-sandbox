# Serving model and evidence

**Ship one candidate for human-reviewed triage:** direct Qwen3 8B classification
followed, on supported cases, by the policy retriever and the citation-constrained single-call
Qwen3 8B protocol. No extra model or evaluator on the request path.

[Machine-readable results and run provenance](../../ai-bench/evidence/) · [Targets](../../ai-bench/EVALUATION_TARGETS.md)

## Evidence used for the decision

Numbers below are from the supplied ai-bench archive, not a new GPU run. Do not pool
multiple runs on the same issues as if they were independent observations.

| Metric | Selected result | Project production target |
|---|---:|---:|
| Classifier test accuracy (errors/abstentions count as incorrect) | 173/200 = 86.50% | 90% |
| Macro-F1 (two classes) | 0.8691 | 0.90 |
| Bug precision / recall | 85.71% / 90.00% | 90% / 95% |
| Bug / feature-request F1 | 0.8780 / 0.8601 | 0.88 each |
| Coverage / accuracy among accepted | 198/200 = 99.00% / 173/198 = 87.37% | ≥80% / ≥95% for selective use |
| Test classification technical errors / latency p95 | 0/200 / 3.50 s | ≤0.5% / deployment latency not set |
| Policy retrieval Recall@4 / MRR@4 / nDCG@4 | 1.000 / 0.9844 / 0.9802 (32 answerable validation questions) | ≥0.95 / 0.90 / 0.90 |
| Precision@4 | Validation 0.4531; retrieval-only test 0.3393 | Interpret against the number of relevant policy sections; do not equate precision with context coverage |
| Complete required context | Validation: 32/32; retrieval-only test: 14/14 | ≥95% |
| Action decisions correct | 26/26 validation: 4 ALLOW, 18 BLOCK, 4 NEED_FACTS | ≥95%, ≥90% recall per class |
| Unsafe permission vs an explicit blocker | 0/18 observed (0/22 including unknown facts) | 0 observed |
| Unsupported questions answered / supported questions refused | 0/4 / 0/32 | ≤2% / ≤5% |
| Citation IDs valid / first-attempt protocol validity | 92/92 / 36/36 | 100% / ≥99.5% |
| RAG p50 / p95; retrieval p95 | 6.14 / 8.52 s; 0.017 s | Local targets: end-to-end ≤15 s p95; retrieval ≤1 s p95 |
| Semantic quality and task success | Unmeasured on the final arm (judge coverage 0/36) | Correctness 95%, completeness 90%, groundedness 98%, citation precision/recall 98%/95%, task success 90% |

The classifier's validation accuracy was 91.50% (macro-F1 0.9195). The test result
is lower. Its rule comparator scored 18.00% and 0.2834 on the **same test**; the
1,993-example aggregate includes train-split inference and is not a test score.
No training or reliable out-of-domain recognition is established here.

For RAG, repaired direct generation reached at most **22/26** (full context).
BM25 with the base policy generator reached **25/26** on development using these
validation cases; policy retrieval with the base policy generator reached **24/26**;
the structured variant reached **11/26**, including 6 unsafe permissions.
Both citation-constrained experiments reached **26/26 on the same 26 decisions**. The final arm also
matched all 36 expected response shapes; 31 cases invoked generation, one deadline
block and four no-context refusals were deterministic. Full/oracle/none are controls.
The ranked lexical profile needs no GPU embedder, Qdrant or reranker.

The selected validation score is a development result on a small policy-authored
set. A separate 16-case test exercises retrieval only (14 answerable). No held-out
end-to-end result or semantic score for the final refined generator was supplied.
The stronger Qwen2.5:7b calibration (v3) passed 11/19 challenges with 4 false
acceptances; the earlier direct-arm judge scores cannot be transferred to this arm.
The archive's final quality gate is **INCOMPLETE**. No end-to-end production certificate.

## Reproduce the selection

Use the [benchmark guide](../../ai-bench/BENCHMARK.md) for a fresh run. The serving classifier is the `none` arm: direct classification without retrieval, context 16,384 / output 128. The RAG arm is `policy` with strategy `policy-decision`: topic-weighted BM25, top four qualifying sections, score floor eight, required policy dependencies, context 8,192 / output 512. Both stages use a fixed seed (42), temperature zero, and thinking disabled.

Model: `qwen3:8b`, digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`. Policy: 32 sections, content digest `8a964180e7143302236b94cdd18e533f8562f8770b0f64e67f3030447b749ee5`. These numbers apply to the specified evidence and model, not every run of a similarly named tag.

The [contract verifier](../scripts/verify_inference_contract.py) takes explicit classification, policy and retrieval results plus the source directory. It checks hashes, runtime rankings and recorded outputs without inference. Use its `--help` to select artifacts from any location. The [model check](../scripts/check_ai.py) instead calls the installed model without writing to GitHub.
