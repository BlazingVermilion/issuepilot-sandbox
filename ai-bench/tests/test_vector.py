"""Ensure every dense branch searches Qdrant, not a local matrix fallback."""

import sys, unittest
from pathlib import Path
from types import SimpleNamespace
from collections import Counter
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import retrieval, vector_store, lab


def retriever(mode):
    r = object.__new__(retrieval.Retriever)
    r.docs = [
        {"id": "a", "text": "alpha policy"},
        {"id": "b", "text": "beta policy"},
        {"id": "c", "text": "gamma policy"},
    ]
    r.mode = mode
    r.candidates = 10
    r.tf = [Counter(retrieval.tokens(d["text"])) for d in r.docs]
    r.df = Counter(t for row in r.tf for t in row)
    r.lengths = [2, 2, 2]
    r.avg = 2
    tok = SimpleNamespace(encode=lambda *a, **kw: [1, 2], decode=lambda ids: "query")
    r.encoder = SimpleNamespace(
        tokenizer=tok, encode=lambda *a, **kw: np.array([[1.0, 0.0]])
    )
    r.vectors = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])
    r.parents = [0, 1, 2]
    r.children = ["alpha", "beta", "gamma"]
    r.full = "synthetic"
    r.meta = {"model_revisions": {retrieval.BGE: "synthetic-test"}}
    r.cross = None
    if mode == "hybrid-rerank":
        r.cross = SimpleNamespace(
            tokenizer=tok,
            predict=lambda pairs, **kw: np.arange(len(pairs), dtype=float),
        )
    return r


class VectorTests(unittest.TestCase):
    def test_all_dense_modes_call_database(self):
        for mode in ("dense", "hybrid", "hybrid-rerank"):
            with self.subTest(mode=mode):
                r = retriever(mode)

                class DB:
                    calls = 0

                    def search(self, vector, k):
                        self.calls += 1
                        return [
                            {"id": "c", "score": 0.9},
                            {"id": "b", "score": 0.7},
                            {"id": "a", "score": 0.1},
                        ]

                db = DB()
                r.vector_index = db
                rows = r.search("query", 3)
                self.assertEqual(db.calls, 1)
                self.assertEqual(len(rows), 3)
                if mode == "dense":
                    self.assertEqual(rows[0]["id"], "c")

    def test_failure_never_falls_back_to_numpy(self):
        r = retriever("dense")
        r.vector_index = SimpleNamespace(
            search=lambda *a: (_ for _ in ()).throw(ConnectionError("Database down"))
        )
        with self.assertRaises(ConnectionError):
            r.search("query", 3)

    def test_bm25_no_vector_database(self):
        import dataset

        with patch.object(
            vector_store, "QdrantIndex", side_effect=AssertionError("Unexpected DB")
        ):
            self.assertTrue(
                retrieval.Retriever(dataset.POLICY, "bm25").search("bug", 4)
            )

    def test_jev_not_available(self):
        self.assertNotIn("jev-rag", lab.MODES)

    def test_qdrant_payload_mismatch_rejected(self):
        v = object.__new__(vector_store.QdrantIndex)
        v.collection = "c"
        v.docs = [{"id": "a", "text": "good"}]
        v.api = lambda *a: {
            "result": {
                "groups": [
                    {
                        "hits": [
                            {
                                "score": 0.5,
                                "payload": {"section_id": "a", "text": "tampered"},
                            }
                        ]
                    }
                ]
            }
        }
        with self.assertRaises(ValueError):
            v.search(np.array([1.0, 0.0]), 1)

    def test_qdrant_missing_groups_rejected(self):
        v = object.__new__(vector_store.QdrantIndex)
        v.collection = "c"
        v.docs = [{"id": "a", "text": "good"}]
        v.api = lambda *a: {"result": {"groups": []}}
        with self.assertRaises(ValueError):
            v.search(np.array([1.0, 0.0]), 1)
