"""Serving configuration for direct issue classification and policy retrieval."""

MODEL = "qwen3:8b"
MODEL_DIGEST = "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"
PROFILE = "qwen-direct-classification+bm25-policy-decisions"
# Persisted cryptographic input, not a display name. Renaming it would invalidate
# approvals for unchanged model and policy bytes in an existing case store.
APPROVAL_CONTRACT_KEY = "qwen3-8b/category-v1-none+policy-pilot-refined-v3/triage-1"
CONTEXT = {"classification": (16384, 128), "rag": (8192, 512)}
TOKENIZER_SHA = "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"
TOKENIZER_REV = "b968826d9c46dd6066d109eabc6255188de91218"
