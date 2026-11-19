"""Real Qdrant integration with synthetic embeddings; no LLM downloads.
Run after docker compose up -d qdrant. Uses an isolated content-addressed test
collection, and removes only that collection after checking all dense branches.
"""

import sys, uuid
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_vector import retriever
from vector_store import QdrantIndex
import retrieval

r = retriever("dense")
r.full = "integration-" + uuid.uuid4().hex
index = None
try:
    index = QdrantIndex(r)
    index2 = QdrantIndex(r)
    assert index.collection == index2.collection
    assert (
        index.api(
            "/collections/" + index.collection + "/points/count",
            "POST",
            {"exact": True},
        )["result"]["count"]
        == 3
    )
    for mode in ("dense", "hybrid", "hybrid-rerank"):
        candidate = retriever(mode)
        candidate.vector_index = index
        rows = candidate.search("query", 3)
        assert len(rows) == 3 and len({row["id"] for row in rows}) == 3
        if mode == "dense":
            assert [row["id"] for row in rows] == ["a", "b", "c"], rows
        print("PASS:", mode, "via real Qdrant")
    print(
        "PASS: idempotent index, same collection, exact count, cosine search and all dense branches"
    )
finally:
    if index is not None:
        index.api("/collections/" + index.collection, "DELETE")
