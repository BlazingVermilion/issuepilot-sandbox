"""Fetch upstream evidence for local inspection; do not silently update the approved RAG corpus."""

import hashlib
import json
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "policies/sources.json").read_text())
for source in manifest:
    page = source["url"].rsplit("/", 1)[-1]
    url = f"https://raw.githubusercontent.com/wiki/microsoft/vscode/{page}.md"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(2_000_001)
    if hashlib.sha256(data).hexdigest() != source["sha256"]:
        raise SystemExit(
            f"Upstream changed: {page}. Review the difference; do not overwrite policy automatically."
        )
    target = root / "data/policy_sources" / f"{page}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(f"Verified {page}")
