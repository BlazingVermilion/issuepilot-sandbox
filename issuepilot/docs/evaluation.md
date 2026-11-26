# What each evaluation measures

- **Classification:** Supply only the original title and description. Use the reference label only after prediction; count abstentions and errors in all-issue accuracy. The full VS Code corpus is descriptive. Use the original held-out split, not the full corpus, for held-out claims.
- **Retrieval and policy decisions:** The RAG set has queries with reference policy sections and expected decisions. Report ranking before dependency expansion and context coverage afterwards. Report abstention, citation validity and decision quality separately. A valid source ID does not establish that the source proves a claim.
- **Workflow regressions:** From the application folder, `uv run python scripts/benchmark.py B1 --engine rules --out artifacts/benchmarks/policy` exercises fixture evidence and `uv run python scripts/benchmark.py B2 --engine rules --out artifacts/benchmarks/workflow` exercises checkpoints, waiting, review and recovery with local adapters. These do not call the serving LLM. PostgreSQL integration tests require a dedicated disposable test database; the test fixture drops tables.
- **Operational evidence:** Sender receipts and intake counts measure transport and persistence. Worker and reviewer events measure actual case stages. Snowflake and CI each need their own verification.

See the [benchmark commands](../../ai-bench/BENCHMARK.md), [targets](../../ai-bench/EVALUATION_TARGETS.md), and [measured serving profile](ai-selection.md).
