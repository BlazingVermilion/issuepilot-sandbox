"""Extract eligible historical section links; never label missing references as negatives."""

import json
from pathlib import Path
from urllib.parse import urlsplit
from .policy import Policies
from .benchmarks import ROOT, read_jsonl, dump


def extract(path, out):
    policies = Policies(ROOT / "policies")
    rows = read_jsonl(path)
    accepted = []
    excluded = []
    import re

    for row in rows:
        # Caller must supply an evidenced opening snapshot; current edited bodies are not sufficient.
        if not row.get("opening_snapshot_verified") or not row.get("snapshot_provenance"):
            excluded.append({"id": row.get("id"), "reason": "unverified opening snapshot"})
            continue
        comments = sorted(row.get("comments", []), key=lambda c: c["created_at"])
        match = None
        for c in comments:
            if c.get("author_association") not in {"OWNER", "MEMBER", "COLLABORATOR"}:
                continue
            urls = re.findall(
                r"https://github.com/microsoft/vscode/wiki/[A-Za-z0-9#_-]+", c.get("body", "")
            )
            targets = []
            for url in urls:
                if not urlsplit(url).fragment:
                    continue
                targets.extend(
                    d["section_id"]
                    for d in policies.docs
                    if d.get("source_url", "") and d["source_url"].lower() == url.lower()
                )
            if targets:
                match = (c, targets)
                break
        if match:
            c, targets = match
            prefix = [x["body"] for x in comments if x["created_at"] < c["created_at"]]
            accepted.append(
                {
                    "id": str(row["id"]),
                    "query": row["title"] + "\n" + row["body"] + "\n" + "\n".join(prefix),
                    "relevant_sections": sorted(set(targets)),
                    "evidence": "linked-silver",
                    "reference_comment_id": c["id"],
                    "snapshot_provenance": row["snapshot_provenance"],
                }
            )
        else:
            excluded.append({"id": row.get("id"), "reason": "no matching explicit section link"})
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    (target / "linked.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in accepted), encoding="utf-8"
    )
    result = {
        "total": len(rows),
        "eligible": len(accepted),
        "excluded": excluded,
        "limits": [
            "Links are silver references, not exhaustive relevance gold",
            "Snapshot provenance must be independently acquired, not guessed",
        ],
    }
    dump(target / "coverage.json", result)
    return result
