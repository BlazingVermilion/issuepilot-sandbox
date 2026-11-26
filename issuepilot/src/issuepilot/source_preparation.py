"""Fetch or reuse a verified source; keep evaluation labels outside the running application."""

import hashlib
import json
import os
from urllib.request import urlopen
from . import issue_source
from .workspace_files import atomic_write


def prepare_source(root, folder, source=None, merged=False):
    manifest = issue_source.MANIFEST_PATH
    spec = json.loads(manifest.read_bytes())
    if (spec["repo"], spec["revision"], spec["splits"]) != (
        issue_source.DATASET,
        issue_source.REVISION,
        issue_source.SPLIT_SIZES,
    ):
        raise ValueError("Dataset manifest differs from this release.")
    # Standard Compose may have created issuepilot/data as root. The workspace
    # projection belongs to the same per-user instance as its database credentials.
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    dest = folder / "projection/issues.jsonl"
    if not source and not merged and dest.is_file():
        rows = issue_source.load_snapshots(str(dest))
        print(f"Dataset ready: {len(rows)} verified issues.")
        return
    if source is None:
        source = root.parent / "ai-bench/data"
        for split in issue_source.SPLIT_SIZES:
            path = source / f"{split}.jsonl"
            if path.exists():
                continue  # The projection builder verifies cached bytes too.
            print(f"Downloading pinned {split} split...", flush=True)
            url = f"https://huggingface.co/datasets/{issue_source.DATASET}/resolve/{issue_source.REVISION}/{split}.jsonl"
            with urlopen(url, timeout=90) as response:
                data = response.read(12_000_001)
            if len(data) > 12_000_000 or hashlib.sha256(data).hexdigest() != spec["sha256"][split]:
                raise ValueError(f"Checksum mismatch for {split}; the download was not saved.")
            atomic_write(path, data)
    report = issue_source.build_issue_projection(source, manifest, dest)
    if os.name == "posix":
        # Only the public, gold-free leaf is bound into the non-root container.
        dest.parent.chmod(0o755)
        dest.chmod(0o644)
    print(f"Dataset ready: {report['unique_issues']} verified issues (reference labels excluded).")
    if merged:
        # This folder is deliberately outside every app/worker volume.
        folder = root.parent / "ai-bench/data/full-corpus"
        rows = []
        for split in issue_source.SPLIT_SIZES:
            for line in issue_source.read_verified_split(source, split, spec).splitlines():
                row = json.loads(line)
                sample = {k: row[k] for k in ("issue_number", "input", "label")}
                sample["source_split"] = split
                sample["created_at"] = row["created_at"]
                rows.append(json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n")
        atomic_write(folder / "issues.jsonl", "".join(rows).encode())
        profile = {
            "classification": {
                "splits": {"test": "issues.jsonl"},
                "fields": {"id": "issue_number", "text": "input", "label": "label"},
            }
        }
        atomic_write(folder / "profile.json", (json.dumps(profile, indent=2) + "\n").encode())
        print(f"Full-corpus scoring profile (not a new held-out test): {folder / 'profile.json'}")
