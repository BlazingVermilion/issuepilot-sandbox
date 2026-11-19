"""Offline, read-only IssuePilot evaluation. No GitHub mutations or UI dependencies."""

from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import math
import platform
from pathlib import Path
import re
import statistics
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
MODES = ["none", "full", "bm25", "dense", "hybrid", "hybrid-rerank", "policy"]
BGE = "BAAI/bge-small-en-v1.5"
RERANK = "cross-encoder/ms-marco-MiniLM-L6-v2"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
POLICY_MIN_SCORE = 8

# Search vocabulary for the normative sections. Phrases bind concepts before
# BM25 breaks ties, so an isolated word such as "request" cannot identify a rule.
POLICY_TOPICS = {
    "IP-GOV-001": r"ignore (?:the )?policy|override (?:the )?policy|normative|policy authority|(?:treat|regard) .{0,36}as maintainer approval",
    "IP-GOV-002": r"authenticated maintainer|maintainer identit|evidence .{0,30}fact|(?:prove|establish) .{0,30}(?:reply|replied|fact)",
    "IP-GOV-003": r"authorization matrix|approv(?:al|e) .{0,25}exact|write access",
    "IP-CAT-001": r"primary categor|category contract|classification contract",
    "IP-CAT-002": r"regression|existing behavior|bug categor",
    "IP-CAT-003": r"new capabilit|feature.request categor",
    "IP-CAT-004": r"supported behavior|question categor",
    "IP-CAT-005": r"documentation (?:issue|categor)|written instructions",
    "IP-CAT-006": r"multiple unrelated|ambiguous intent|other categor",
    "IP-LABEL-001": r"category label|canonical label",
    "IP-LABEL-002": r"workflow label|status label|resolution label",
    "IP-INFO-001": r"required (?:fields|information)|(?:screenshots?|reproduc\w+|performance traces?) .{0,35}(?:required|mandatory)|intermittent",
    "IP-INFO-002": r"(?:clarification|reporter) .{0,30}(?:repl(?:y|ies)|respond|edit)|(?:supplied|partial|unrelated) .{0,25}(?:information|reply|only|sentence)|clarification (?:send|question|rounds)",
    "IP-INFO-003": r"information (?:deadline|timeout|timer)|effective deadline|(?:day.5|late) reminder|reminder .{0,35}(?:deliver|sent|failed)|no.response timer",
    "IP-INFO-004": r"(?:three|third|fourth|3) (?:published )?(?:clarification|question|round)|round limit",
    "IP-PLAN-001": r"backlog|delivery commitment|milestone|assignment|(?:assign|unassign)\w* .{0,20}owner",
    "IP-VERIFY-001": r"fix (?:readiness|reference)|(?:accessible|available|testable) (?:build|artifact)|verification (?:readiness|instructions)|(?:start|initiate) .{0,16}verification",
    "IP-VERIFY-002": r"(?:successful|confirmed|inconclusive|failed) verification|verification (?:request|outcome|evidence|result)|(?:mark|add) .{0,20}verified|(?:old|wrong|different) build|fix .{0,18}failed|\bthanks\b",
    "IP-VERIFY-003": r"verification (?:silence|reminder|timeout)|(?:unanswered|ignored|silence|no one replied).{0,45}verification|verification.{0,45}(?:unanswered|silence|no response)|silence means verified",
    "IP-CLOSE-001": r"(?:common|closure) gates?|closure (?:eligibility|conditions?|proposal|decision|policy)|configured writes|write permissions|source readback|contradictory (?:reply|evidence)|conflicting .{0,20}conclusions",
    "IP-CLOSE-002": r"no.response|waiting.information|unanswered (?:information|clarification)|(?:information|clarification) (?:deadline|timer|timeout)",
    "IP-CLOSE-003": r"implemented and verified|verified closure|close .{0,26}verif\w+|successful verification",
    "IP-CLOSE-004": r"\bduplicate\b|canonical (?:issue|link)",
    "IP-CLOSE-005": r"out.of.scope|as.designed|intended behaviou?r",
    "IP-CLOSE-006": r"caused.by.extension|(?:external|responsible) extension|extension responsibility|support destination",
    "IP-CLOSE-007": r"(?:backlog|votes|old issue|information timeout).{0,50}(?:clos(?:e|ure)|auto.close)|(?:unanswered|ignored) verification|manual (?:resolution|clos(?:e|ure))",
    "IP-END-001": r"\b(?:terminal|closed) (?:case|issue)|reopen\w*|post.closure|delayed labels or comments",
    "IP-OPS-001": r"\bpause\w*\b|\bresume\b|readback|idempoten\w+|(?:write|read) outcome|checkpoint|retry recoverable|resend .{0,20}comment",
    "IP-RAG-001": r"cit(?:e|es|ed|ing|ation) .{0,25}policy|policy citation|retrieval (?:corpus|dependencies)",
    "IP-EVAL-001": r"benchmark|ground.truth|evaluation boundaries",
}
POLICY_TOPIC_PATTERNS = {key: re.compile(value) for key, value in POLICY_TOPICS.items()}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def jsonl(path):
    return [
        json.loads(x)
        for x in Path(path).read_text(encoding="utf-8-sig").splitlines()
        if x.strip()
    ]


def http(base, endpoint, payload=None, timeout=300):
    body = None if payload is None else json.dumps(payload).encode()
    req = Request(
        base.rstrip("/") + endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


def tokens(text):
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())


def sections(path):
    text = Path(path).read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## (IP-[A-Z]+-\d{3}) — ", text)
    result = [
        {"id": parts[i], "text": parts[i + 1].strip()} for i in range(1, len(parts), 2)
    ]
    if not result or len({r["id"] for r in result}) != len(result):
        raise ValueError("Policy must have unique IP section IDs.")
    if any(len(d["text"]) > 32768 for d in result): raise ValueError("Split policy sections over 32768 characters at semantic boundaries")
    return text, result


class Retriever:
    """Section-preserving BM25; optional token-bounded dense child chunks.

    Retrieval ranks sections. Dense child scores are max-pooled per section so
    long policy sections cannot silently lose their final conditions to truncation.
    """

    def __init__(
        self,
        policy,
        mode,
        device="cpu",
        candidates=10,
        cache=None,
        qdrant_url="http://localhost:6333",
        timeout=60,
    ):
        started = time.perf_counter()
        self.full, self.docs = sections(policy)
        self.mode, self.candidates = mode, candidates
        self.meta = {"mode": mode, "device": device, "index_seconds": None}
        self.tf = [Counter(tokens(d["text"])) for d in self.docs]
        self.lengths = [sum(x.values()) for x in self.tf]
        self.avg = sum(self.lengths) / len(self.lengths)
        self.df = Counter(t for row in self.tf for t in row)
        self.encoder = self.cross = None
        if mode == "policy":
            self.meta.update(ranking="policy-topics-bm25-v1", minimum_context_score=POLICY_MIN_SCORE)
        if mode in ("dense", "hybrid", "hybrid-rerank"):
            from huggingface_hub import snapshot_download, HfApi
            from sentence_transformers import SentenceTransformer

            # An existing lock prevents mutable model branches changing across arms.
            lockpath = ROOT / "models.lock.json"
            lock = json.loads(lockpath.read_text()) if lockpath.exists() else {}

            def resolve(name):
                revision = lock.get(name) or HfApi().model_info(name).sha
                if not revision:
                    raise RuntimeError("Missing immutable model revision")
                # Explicit local_dir avoids the Windows symlink-based hub cache path.
                directory = snapshot_download(
                    name,
                    revision=revision,
                    local_dir=ROOT / ".models" / name.replace("/", "--") / revision,
                    allow_patterns=["*.json", "*.txt", "*.model", "*.safetensors"],
                    ignore_patterns=["onnx/*", "openvino/*"],
                    max_workers=2,
                )
                lock[name] = revision
                dump(lockpath, lock)
                return directory

            self.encoder = SentenceTransformer(resolve(BGE), device=device)
            tok = self.encoder.tokenizer
            self.children, self.parents = [], []
            for i, doc in enumerate(self.docs):
                ids = tok.encode(doc["text"], add_special_tokens=False)
                for start in range(0, len(ids), 320):
                    self.children.append(tok.decode(ids[start : start + 384]))
                    self.parents.append(i)
            key = sha(
                json.dumps([sha(self.full.encode()), lock[BGE], self.children]).encode()
            )
            import numpy as np

            folder = Path(cache or ROOT / "cache")
            folder.mkdir(parents=True, exist_ok=True)
            file = folder / (key + ".npy")
            if file.exists():
                self.vectors = np.load(file, allow_pickle=False)
            else:
                self.vectors = self.encoder.encode(
                    self.children,
                    normalize_embeddings=True,
                    batch_size=16,
                    show_progress_bar=True,
                )
                np.save(file, self.vectors, allow_pickle=False)
            if mode == "hybrid-rerank":
                from sentence_transformers import CrossEncoder

                self.cross = CrossEncoder(
                    resolve(RERANK), device=device, max_length=512
                )
            self.meta["model_revisions"] = {
                name: lock[name]
                for name in ([BGE, RERANK] if mode == "hybrid-rerank" else [BGE])
            }
            self.meta["dense_child_count"] = len(self.children)
            from vector_store import QdrantIndex

            self.vector_index = QdrantIndex(self, qdrant_url, timeout)
            self.meta["vector_db"] = self.vector_index.meta
        self.meta["index_seconds"] = time.perf_counter() - started

    def search(self, query, k):
        n = len(self.docs)
        lexical = [0.0] * n
        for term in set(tokens(query)):
            df = self.df[term]
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, row in enumerate(self.tf):
                freq = row[term]
                lexical[i] += (
                    idf
                    * freq
                    * 2.5
                    / (freq + 1.5 * (0.25 + 0.75 * self.lengths[i] / self.avg))
                )
        rank_b = sorted(range(n), key=lambda i: (-lexical[i], self.docs[i]["id"]))
        scores = lexical
        rank = rank_b
        if self.mode == "policy":
            scores = self.policy_scores(query, lexical)
            rank = sorted(range(n), key=lambda i: (-scores[i], self.docs[i]["id"]))
        if self.encoder is not None:
            import numpy as np

            # Bound query inputs explicitly and report clipping; never silently
            # use an arbitrary provider-side truncation for long issue reports.
            tok = self.encoder.tokenizer
            ids = tok.encode(query, add_special_tokens=False)
            self.query_clipped = len(ids) > 400
            if self.query_clipped:
                raise ValueError(
                    "QUERY_TOO_LONG: dense query exceeds 400 tokens; no silent clipping"
                )
            dense_query = tok.decode(ids)
            vector = self.encoder.encode(
                [QUERY_PREFIX + dense_query], normalize_embeddings=True
            )[0]
            # Qdrant performs the exact cosine search and max-score grouping.
            # NumPy files above are embedding caches only, never a search fallback.
            hits = self.vector_index.search(vector, n)
            by_id = {d["id"]: i for i, d in enumerate(self.docs)}
            dense = [-float("inf")] * n
            for hit in hits:
                dense[by_id[hit["id"]]] = hit["score"]
            rank_d = sorted(range(n), key=lambda i: (-dense[i], self.docs[i]["id"]))
            if self.mode == "dense":
                rank, scores = rank_d, dense
            else:
                fused = {}
                for ranking in (rank_b, rank_d):
                    for pos, i in enumerate(ranking[: self.candidates], 1):
                        fused[i] = fused.get(i, 0) + 1 / (60 + pos)
                scores = [fused.get(i, 0) for i in range(n)]
                rank = sorted(fused, key=lambda i: (-scores[i], self.docs[i]["id"]))
            if self.cross is not None:
                selected = rank[: self.candidates * 2]
                # Score bounded child passages, then max-pool. Full sections are
                # restored as context, including qualifications and exceptions.
                qtok = self.cross.tokenizer
                query_ids = qtok.encode(query, add_special_tokens=False)
                if len(query_ids) > 96:
                    raise ValueError(
                        "QUERY_TOO_LONG: reranker query exceeds 96 tokens; no silent clipping"
                    )
                q = qtok.decode(query_ids)
                pairs, owners = [], []
                for i in selected:
                    ids = qtok.encode(self.docs[i]["text"], add_special_tokens=False)
                    for start in range(0, len(ids), 300):
                        pairs.append((q, qtok.decode(ids[start : start + 350])))
                        owners.append(i)
                cross_scores = self.cross.predict(
                    pairs, batch_size=16, show_progress_bar=False
                )
                best = {i: -float("inf") for i in selected}
                for i, value in zip(owners, cross_scores):
                    best[i] = max(best[i], float(value))
                rank = sorted(best, key=lambda i: (-best[i], self.docs[i]["id"]))
                scores = [best.get(i, -float("inf")) for i in range(n)]
        return [{**self.docs[i], "score": scores[i]} for i in rank[:k]]

    def policy_scores(self, query, lexical):
        query = query.lower().replace("_", "-")
        topics = {key for key, pattern in POLICY_TOPIC_PATTERNS.items() if pattern.search(query)}
        explicit = {value.upper() for value in re.findall(r"\bip-[a-z]+-\d{3}\b", query)}
        if re.search(r"\bclos(?:e|ed|ing|ure)\b", query):
            if "IP-INFO-003" in topics and "IP-VERIFY-003" not in topics:
                topics.add("IP-CLOSE-002")
            if topics & {f"IP-CLOSE-{number:03d}" for number in range(2, 7)}:
                topics.add("IP-CLOSE-001")
        return [
            score + 14 * (doc["id"] in topics) + 28 * (doc["id"] in explicit)
            for doc, score in zip(self.docs, lexical)
        ]
