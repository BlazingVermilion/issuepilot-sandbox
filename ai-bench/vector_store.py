"""Persistent vector storage and search for all dense retrieval branches."""

import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import retrieval as util


def request(base, path, method="GET", body=None, headers=None, timeout=60):
    req = Request(
        base.rstrip("/") + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


class QdrantIndex:
    """Persistent Qdrant server with exact cosine search grouped by policy section.

    The original dense encoder/chunking is reused for controlled comparisons.
    Query retrieval executes in Qdrant, never in the NumPy cache.
    """

    def __init__(self, base, url="http://localhost:6333", timeout=60):
        import numpy as np
        vectors = np.asarray(base.vectors)
        if vectors.ndim != 2 or vectors.shape[0] != len(base.children) or not vectors.size:
            raise ValueError("Invalid embedding shape")
        if len(base.parents) != len(base.children) or any(type(p) is not int or not 0 <= p < len(base.docs) for p in base.parents):
            raise ValueError("Invalid child-to-parent mapping")
        if not np.isfinite(vectors).all() or (np.linalg.norm(vectors, axis=1) == 0).any():
            raise ValueError("Non-finite or zero embedding")
        self.base = base
        self.docs = base.docs
        self.url = url
        self.timeout = timeout
        self.server = request(self.url, "/", timeout=timeout)
        fingerprint = util.sha(
            json.dumps(
                {
                    "policy": util.sha(base.full.encode()),
                    "embedding_revision": base.meta["model_revisions"][util.BGE],
                    "children": base.children,
                    "vectors": util.sha(base.vectors.tobytes()),
                },
                sort_keys=True,
            ).encode()
        )
        self.collection = "issuepilot_" + fingerprint[:24]
        self.meta = {
            "backend": "qdrant",
            "collection": self.collection,
            "server_version": self.server.get("version"),
            "corpus_fingerprint": fingerprint,
            "distance": "Cosine",
            "exact": True,
            "group_by": "section_id",
        }
        self.ensure_index()

    def api(self, path, method="GET", body=None):
        return request(self.url, path, method, body, timeout=self.timeout)

    def ensure_index(self):
        path = "/collections/" + self.collection
        try:
            self.api(path)
        except HTTPError as e:
            if e.code != 404:
                raise
            self.api(
                path,
                "PUT",
                {"vectors": {"size": len(self.base.vectors[0]), "distance": "Cosine"}},
            )
        # Content-addressed collection; idempotent upsert repairs interrupted indexing.
        points = [
            {
                "id": i,
                "vector": v.tolist(),
                "payload": {
                    "section_id": self.docs[parent]["id"],
                    "text": self.docs[parent]["text"],
                },
            }
            for i, (v, parent) in enumerate(zip(self.base.vectors, self.base.parents))
        ]
        for i in range(0, len(points), 64):
            self.api(path + "/points?wait=true", "PUT", {"points": points[i : i + 64]})
        count = self.api(path + "/points/count", "POST", {"exact": True})["result"][
            "count"
        ]
        if count != len(points):
            raise ValueError("QDRANT_INDEX_COUNT_MISMATCH")

    def search(self, vector, k):
        body = {
            "query": vector.tolist(),
            "group_by": "section_id",
            "group_size": 1,
            "limit": k,
            "with_payload": True,
            "params": {"exact": True},
        }
        groups = self.api(
            "/collections/" + self.collection + "/points/query/groups", "POST", body
        )["result"]["groups"]
        rows = []
        expected = {d["id"]: d["text"] for d in self.docs}
        for g in groups:
            hit = g["hits"][0]
            p = hit["payload"]
            if expected.get(p["section_id"]) != p["text"]:
                raise ValueError("QDRANT_PAYLOAD_MISMATCH")
            rows.append(
                {"id": p["section_id"], "text": p["text"], "score": hit["score"]}
            )
        if len(rows) != min(k, len(self.docs)) or len({d["id"] for d in rows}) != len(
            rows
        ):
            raise ValueError("QDRANT_CANDIDATE_COUNT_MISMATCH")
        return sorted(rows, key=lambda d: (-d["score"], d["id"]))
