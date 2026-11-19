"""Versioned benchmark loading; test labels are never passed to normal inference."""

from pathlib import Path
import retrieval as util
import re
import bench_config as cfg

ROOT = Path(__file__).resolve().parent
POLICY = cfg.POLICY
DATA = cfg.SCENARIOS
DEPS = {
    "IP-CLOSE-002": ["IP-CLOSE-001", "IP-INFO-003", "IP-INFO-004"],
    "IP-CLOSE-003": ["IP-CLOSE-001", "IP-VERIFY-001", "IP-VERIFY-002"],
    "IP-CLOSE-004": ["IP-CLOSE-001"],
    "IP-CLOSE-005": ["IP-CLOSE-001"],
    "IP-CLOSE-006": ["IP-CLOSE-001"],
}
DEPS = cfg.RAG.get("dependencies", DEPS if POLICY == cfg.BUILTIN_POLICY else {})


def load(split):
    return [r for r in util.jsonl(DATA) if split == "all" or r["split"] == split]


def validate():
    docs = {d["id"]: d["text"] for d in util.sections(POLICY)[1]}
    if not isinstance(DEPS,dict) or any(not isinstance(v,list) for v in DEPS.values()): raise ValueError("Dependencies must map section IDs to lists of section IDs")
    for key, deps in DEPS.items():
        if key not in docs or not set(deps) <= set(docs):
            raise ValueError("Unknown dependency section")
    rows = load("all")
    if not rows: raise ValueError("No RAG scenarios")
    seen = set()
    families = {}
    for r in rows:
        if not isinstance(r,dict) or not isinstance(r.get("id"),str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}",r["id"]):
            raise ValueError("Scenario IDs must be safe record identifiers")
        if not all(isinstance(r.get(k),str) and r[k].strip() for k in ("family","reference_answer")) or not all(isinstance(r.get(k),list) and all(isinstance(v,str) and v.strip() for v in r[k]) for k in ("reference_claims","relevant")):
            raise ValueError("Invalid scenario family, reference answer, claims or relevant section IDs")
        if r["task"] not in ("action", "explanation") or r["split"] not in ("validation", "test"):
            raise ValueError("Invalid task/split")
        if type(r["answerable"]) is not bool or not isinstance(r["query"], str) or not r["query"].strip():
            raise ValueError("Invalid answerability/query")
        if len(r["relevant"]) != len(set(r["relevant"])):
            raise ValueError("Duplicate relevant IDs")
        if r["id"] in seen or not set(r["relevant"]) <= set(docs):
            raise ValueError("Invalid dataset IDs")
        seen.add(r["id"])
        families.setdefault(r["family"], set()).add(r["split"])
        if r["answerable"] != bool(r["relevant"]) or not r["reference_claims"]:
            raise ValueError("Incomplete reference")
        if r["task"] == "action" and r["expected_action"] not in [
            "ALLOW",
            "BLOCK",
            "NEED_FACTS",
        ]:
            raise ValueError("Action reference missing")
        if r["task"] == "explanation" and r["expected_action"] is not None:
            raise ValueError("Explanation incorrectly has action reference")
    if any(len(v) > 1 for v in families.values()):
        raise ValueError("Family split leakage")
    return {
        "n": len(rows),
        "validation": len(load("validation")),
        "test": len(load("test")),
        "families": len(families),
        "status": "PASS",
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


def assemble(ret, case, mode, k):
    rank = None
    if mode == "none":
        ids = []
    elif mode == "full":
        ids = [d["id"] for d in ret.docs]
    elif mode == "oracle":
        ids = expand(case["relevant"])
    else:
        ranked = ret.search(case["query"], max(8, k))
        rank = [d["id"] for d in ranked]
        if mode == "policy":
            ids = expand([d["id"] for d in ranked[:k] if d["score"] >= util.POLICY_MIN_SCORE])
        else:
            ids = expand(rank[:k])
    by = {d["id"]: d["text"] for d in ret.docs}
    return {i: by[i] for i in ids}, rank
