"""The normative policy owns the vocabulary. GitHub reports provisioned names only."""

import json
import re
from .ai.index import sections
from .contracts import digest


def policy_labels(settings):
    path = settings.policy_dir / "runtime" / "issuepilot_policy.md"
    docs = {d["id"]: d["text"] for d in sections(path)[1]}
    primary = re.search(r"^Canonical primary labels are ([^.]+)\.", docs["IP-LABEL-001"], re.M)
    if not primary:
        raise ValueError("IP-LABEL-001 must declare the canonical primary labels")
    names = re.split(r",\s*|\s+and\s+", primary[1])
    info = dict(table_rows(docs["IP-INFO-001"], 2))
    if set(names) != set(info):
        raise ValueError("Primary labels and IP-INFO-001 category rows must agree")
    entries = [
        dict(
            name=n,
            group="Primary",
            section="IP-LABEL-001",
            description="Required information: " + info[n],
            color="3972ad",
        )
        for n in names
    ]
    for name, add, remove in table_rows(docs["IP-LABEL-002"], 3):
        entries.append(
            dict(
                name=name,
                group="Workflow / resolution",
                section="IP-LABEL-002",
                description=add,
                remove_when=remove,
                color="617788",
            )
        )
    if len({e["name"] for e in entries}) != len(entries):
        raise ValueError("Duplicate canonical policy label")
    return entries


def table_rows(text, columns):
    lines = [
        line.strip().strip("|").split("|") for line in text.splitlines() if line.startswith("|")
    ]
    if len(lines) < 3 or not all(len(row) == columns for row in lines):
        raise ValueError("Malformed normative label table")
    return [tuple(cell.strip() for cell in row) for row in lines[2:]]


def catalog(settings, labels):
    rules = json.loads((settings.policy_dir / "label_rules.json").read_text(encoding="utf-8"))
    entries = policy_labels(settings)
    permitted = set(rules["agent_labels"])
    if not permitted <= {e["name"] for e in entries if e["group"] == "Primary"}:
        raise ValueError("Intake permissions must name canonical primary labels")
    if settings.allowed_labels is not None:
        permitted &= set(settings.allowed_labels)
    provisioned = {e["name"] for e in labels if not e.get("archived_at")}
    for e in entries:
        e["available"] = e["name"] in provisioned
        e["agent_allowed"] = e["available"] and e["name"] in permitted
    return {
        "source": "issuepilot_policy",
        "availability_source": "github" if settings.mode == "live" else "local_policy",
        "labels": entries,
        "hash": digest({"labels": entries, "rules": rules}),
        "allowed": [e["name"] for e in entries if e["agent_allowed"]],
        "legacy_filter_active": settings.allowed_labels is not None,
    }
