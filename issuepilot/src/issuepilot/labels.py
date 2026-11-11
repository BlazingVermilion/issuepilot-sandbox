"""Repository vocabulary and maintainer permissions are separate, versioned inputs."""

import json
from .contracts import digest

# Offline fixture only. Live mode must fetch GitHub and must never fall back here.
DEMO_LABELS = [
    {"name": name, "description": description, "color": color}
    for name, description, color in [
        ("accessibility", "Barrier affecting people with disabilities", "7057ff"),
        ("bug", "Something isn't working", "d73a4a"),
        ("documentation", "Improvements or additions to documentation", "0075ca"),
        ("duplicate", "This issue or pull request already exists", "cfd3d7"),
        ("enhancement", "New feature or request", "a2eeef"),
        ("good first issue", "Good for newcomers", "7057ff"),
        ("help wanted", "Extra attention is needed", "008672"),
        ("invalid", "This doesn't seem right", "e4e669"),
        ("question", "Further information is requested", "d876e3"),
        ("wontfix", "This will not be worked on", "ffffff"),
    ]
]


def catalog(settings, labels):
    rules = json.loads((settings.policy_dir / "label_rules.json").read_text(encoding="utf-8"))
    permitted = set(rules["agent_labels"]) - set(rules["maintainer_only"])
    if settings.allowed_labels is not None:
        permitted &= set(settings.allowed_labels)
    entries = sorted(
        [
            {
                "name": x["name"],
                "description": x.get("description") or "",
                "color": x.get("color", "808080"),
                "agent_allowed": x["name"] in permitted and not x.get("archived_at"),
            }
            for x in labels
        ],
        key=lambda x: x["name"],
    )
    # Color-only changes do not change the meaning of an approved proposal.
    fingerprint = digest(
        {"labels": [{k: v for k, v in x.items() if k != "color"} for x in entries], "rules": rules}
    )
    return {
        "source": "github" if settings.mode == "live" else "demo_fixture",
        "labels": entries,
        "hash": fingerprint,
        "allowed": [x["name"] for x in entries if x["agent_allowed"]],
        "legacy_filter_active": settings.allowed_labels is not None,
    }
