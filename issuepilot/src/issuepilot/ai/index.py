"""Policy topic ranking, BM25, and mandatory dependencies from the selected benchmark arm."""

from collections import Counter
import hashlib
import math
from pathlib import Path
import re
from ..contracts import digest
from .profile import PROFILE

POLICY_MIN_SCORE = 8

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


def tokens(text):
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())


def sections(path):
    text = Path(path).read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## (IP-[A-Z]+-\d{3}) — ", text)
    result = [{"id": parts[i], "text": parts[i + 1].strip()} for i in range(1, len(parts), 2)]
    if not result or len({r["id"] for r in result}) != len(result):
        raise ValueError("Policy must have unique IP section IDs.")
    if any(len(r["text"]) > 32768 for r in result):
        raise ValueError(
            "A policy section exceeds the evidence limit; split it at a semantic boundary"
        )
    return text, result


DEPS = {
    "IP-CLOSE-002": ["IP-CLOSE-001", "IP-INFO-003", "IP-INFO-004"],
    "IP-CLOSE-003": ["IP-CLOSE-001", "IP-VERIFY-001", "IP-VERIFY-002"],
    "IP-CLOSE-004": ["IP-CLOSE-001"],
    "IP-CLOSE-005": ["IP-CLOSE-001"],
    "IP-CLOSE-006": ["IP-CLOSE-001"],
}


def expand(ids):
    result = list(dict.fromkeys(ids))
    todo = list(result)
    while todo:
        for dep in DEPS.get(todo.pop(0), []):
            if dep not in result:
                result.append(dep)
                todo.append(dep)
    return result


class PolicyIndex:
    def __init__(self, directory, inference_profile=PROFILE):
        self.path = Path(directory) / "runtime" / "issuepilot_policy.md"
        full, self.docs = sections(self.path)
        self.content_sha256 = hashlib.sha256(full.encode()).hexdigest()
        self.hash = digest({"profile": inference_profile, "policy": self.content_sha256})
        self.by_id = {d["id"]: d["text"] for d in self.docs}
        if (
            not (set(POLICY_TOPICS) | set(DEPS) | {d for ds in DEPS.values() for d in ds})
            <= self.by_id.keys()
        ):
            raise ValueError("Selected policy profile is missing normative sections")
        self.tf = [Counter(tokens(d["text"])) for d in self.docs]
        self.lengths = [sum(x.values()) for x in self.tf]
        self.avg = sum(self.lengths) / len(self.lengths)
        self.df = Counter(t for row in self.tf for t in row)

    def rank(self, query):
        n = len(self.docs)
        scores = [0.0] * n
        for term in sorted(set(tokens(query))):
            df = self.df[term]
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, row in enumerate(self.tf):
                freq = row[term]
                scores[i] += (
                    idf * freq * 2.5 / (freq + 1.5 * (0.25 + 0.75 * self.lengths[i] / self.avg))
                )
        scores = self.policy_scores(query, scores)
        return [
            (self.docs[i]["id"], scores[i])
            for i in sorted(range(n), key=lambda i: (-scores[i], self.docs[i]["id"]))
        ][:8]

    def retrieve(self, query):
        rank = self.rank(query)
        ids = expand([key for key, score in rank[:4] if score >= POLICY_MIN_SCORE])
        return {i: self.by_id[i] for i in ids}, [i for i, _ in rank]

    def evidence(self, context):
        return [
            {
                "id": i,
                "section_id": i,
                "text": text,
                "file": "runtime/issuepilot_policy.md",
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
            for i, text in context.items()
        ]

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
