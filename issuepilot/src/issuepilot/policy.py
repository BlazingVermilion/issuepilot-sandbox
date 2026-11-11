"""Local, maintainer-controlled policy corpus with lexical retrieval and immutable hashes."""

import re
from pathlib import Path
from rank_bm25 import BM25Okapi
from .contracts import digest


def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())


class Policies:
    def __init__(self, directory):
        self.docs = []
        for path in sorted(Path(directory).glob("*.md")):
            for index, chunk in enumerate(path.read_text(encoding="utf-8").split("\n## ")):
                self.docs.append(
                    {
                        "id": f"{path.stem}:{index}",
                        "file": path.name,
                        "text": chunk.strip(),
                        "sha256": digest(chunk.strip()),
                    }
                )
        self.hash = digest(self.docs)
        self.index = BM25Okapi([tokens(d["text"]) for d in self.docs]) if self.docs else None

    def search(self, query, limit=4):
        if not self.index:
            return []
        scores = self.index.get_scores(tokens(query))
        ranked = sorted(range(len(scores)), key=lambda i: float(scores[i]), reverse=True)
        return [self.docs[i] for i in ranked[:limit] if scores[i] > 0]
