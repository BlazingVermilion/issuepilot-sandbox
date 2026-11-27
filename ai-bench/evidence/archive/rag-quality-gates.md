# RAG pilot assessment

Thresholds apply to this benchmark split. Semantic values are judge estimates. Executor authorization, recovery, idempotency, and deployment load need separate evidence.

| Arm | Split | Cases | Status | Judge scored / eligible | Judge coverage, all cases | Judge failures | Unsupported errors | Unsupported wrong or error rate |
|---|---|---:|---|---:|---:|---:|---:|---:|
| policy--pilot-refined | validation | 36 | INCOMPLETE | 0 / 36 | 0 | 0 | 0 | 0 |

A ranked arm needs every applicable gate to pass. Control arms provide diagnostics.

## policy--pilot-refined

| Gate | Observed | Target | Support | Status |
|---|---:|---:|---:|---|
| planned_cases_missing | 0 | <= 0 | 36 | PASS |
| full_split_verified | 1 | >= 1 | N/A | PASS |
| answerable_queries_ranked | 32 | >= 32 | 32 | PASS |
| recall_at_4 | 1 | >= 0.9 | 32 | PASS |
| mrr_at_4 | 0.9844 | >= 0.85 | 32 | PASS |
| ndcg_at_4 | 0.9802 | >= 0.85 | 32 | PASS |
| context_reference_recall | 1 | >= 0.95 | 32 | PASS |
| complete_reference_context_rate | 1 | >= 0.9 | 32 | PASS |
| critical_closure_missing_evidence | 0 | <= 0 | 19 | PASS |
| first_attempt_protocol_validity | 1 | >= 0.99 | 36 | PASS |
| final_error_rate | 0 | <= 0.01 | 36 | PASS |
| action_accuracy | 1 | >= 0.9 | 26 | PASS |
| action_recall_ALLOW | 1 | >= 0.85 | 4 | PASS |
| action_recall_BLOCK | 1 | >= 0.85 | 18 | PASS |
| action_recall_NEED_FACTS | 1 | >= 0.85 | 4 | PASS |
| unsafe_allow | 0 | <= 0 | 22 | PASS |
| citation_id_validity | 1 | >= 1 | 92 | PASS |
| false_abstention_rate | 0 | <= 0.1 | 32 | PASS |
| false_answer_rate | 0 | <= 0.05 | 4 | PASS |
| latency_seconds_p95 | 8.519 | <= 15 | 36 | PASS |
| latency_seconds_observations | 36 | >= 36 | 36 | PASS |
| retrieval_seconds_p95 | 0.01724 | <= 1 | 36 | PASS |
| retrieval_seconds_observations | 36 | >= 36 | 36 | PASS |
| judge_evaluator_current | N/A | >= 1 | N/A | UNMEASURED |
| judge_coverage_valid | N/A | >= 1 | 36 | UNMEASURED |
| semantic_correctness | N/A | >= 0.9 | 0 | UNMEASURED |
| semantic_completeness | N/A | >= 0.85 | 0 | UNMEASURED |
| semantic_groundedness | N/A | >= 0.95 | 0 | UNMEASURED |
| semantic_citation_precision | N/A | >= 0.95 | 0 | UNMEASURED |
| semantic_citation_recall | N/A | >= 0.9 | 0 | UNMEASURED |
| semantic_task_success_all | N/A | >= 0.85 | 36 | UNMEASURED |

Generation paths by case count — deadline_check (1), model (31), no_context (4).

Raw model action accuracy is 1 over 25 model-path action cases.
