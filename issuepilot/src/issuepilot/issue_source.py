"""Pinned VS Code issue snapshots. This module also runs on a stdlib-only Windows host."""

from collections import Counter
from functools import lru_cache
import hashlib
import hmac
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZipFile

MANIFEST_PATH = Path(__file__).with_name("assets") / "issue-source.json"
SOURCE = json.loads(MANIFEST_PATH.read_bytes())
DATASET = SOURCE["repo"]
REVISION = SOURCE["revision"]
SANDBOX_REPOSITORY = "sandbox/issuepilot"
SPLIT_SIZES = SOURCE["splits"]
PROJECTION_SHA = SOURCE["projection_sha256"]


def canonical_json(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def read_verified_split(source, split, spec):
    source = Path(source)
    name = f"data/{split}.jsonl"
    if source.is_dir():
        path = source / name
        raw = (path if path.is_file() else source / f"{split}.jsonl").read_bytes()
    else:
        with ZipFile(source) as z:
            files = [
                i for i in z.infolist() if i.filename == name or i.filename.endswith("/" + name)
            ]
            if len(files) != 1 or files[0].file_size > 12_000_000:
                raise ValueError(
                    f"Expected exactly one benchmark {name}; provide the source split files"
                )
            raw = z.read(files[0])
    if hashlib.sha256(raw).hexdigest() != spec["sha256"][split]:
        raise ValueError(f"Source checksum differs for {split}; use the original benchmark files")
    return raw


def build_issue_projection(source, manifest, dest):
    """Validate original bytes before projecting. Never extract archive paths or store gold."""
    source, dest = Path(source), Path(dest)
    spec = json.loads(Path(manifest).read_bytes())
    if (spec["repo"], spec["revision"], spec["splits"]) != (DATASET, REVISION, SPLIT_SIZES):
        raise ValueError("The source manifest does not match the pinned corpus")
    output = []
    seen = set()
    for split, size in SPLIT_SIZES.items():
        raw = read_verified_split(source, split, spec)
        lines = raw.splitlines()
        if len(lines) != size:
            raise ValueError(f"Wrong {split} size")
        for line in lines:
            row = json.loads(line)
            number = row["issue_number"]
            if type(number) is not int or number < 1 or number in seen:
                raise ValueError(f"Invalid or duplicate issue number: {number}")
            seen.add(number)
            title, sep, body = row["input"].partition("\n\nDescription:")
            if not sep or not title.startswith("Title: "):
                raise ValueError(f"Malformed input for issue {number}")
            # Only these fields cross the boundary. label and messages are never serialized.
            snap = dict(
                number=number,
                title=title[7:],
                body=body.removeprefix("\n"),
                split=split,
                created_at=row["created_at"],
            )
            output.append(canonical_json(snap) + "\n")
    data = "".join(output).encode()
    if hashlib.sha256(data).hexdigest() != PROJECTION_SHA:
        raise ValueError("Projected issue text differs from the benchmark")
    dest.parent.mkdir(exist_ok=True, parents=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return {
        "state": "PREPARED",
        "unique_issues": len(seen),
        "splits": SPLIT_SIZES,
        "revision": REVISION,
        "projection_sha256": PROJECTION_SHA,
    }


@lru_cache(maxsize=2)
def load_snapshots(path):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROJECTION_SHA:
        raise ValueError("Pinned dataset projection is missing or changed; run prepare again")
    result = {r["number"]: r for r in map(json.loads, raw.splitlines())}
    if Counter(r["split"] for r in result.values()) != SPLIT_SIZES:
        raise ValueError("Dataset cohort size mismatch")
    return result


def issue_delivery_id(number, repository=SANDBOX_REPOSITORY):
    return str(uuid5(NAMESPACE_URL, f"{repository}/{REVISION}/issues/{number}/opened"))


def sign_issue_delivery(number, secret, repository=SANDBOX_REPOSITORY):
    obj = {
        "action": "opened",
        "repository": {"full_name": repository},
        "issue": {"number": number},
        "issuepilot_dataset": REVISION,
    }
    raw = canonical_json(obj).encode()
    return raw, {
        "Content-Type": "application/json",
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": issue_delivery_id(number, repository),
        "X-Hub-Signature-256": "sha256="
        + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
    }


def to_issue_document(row):
    return {
        "number": row["number"],
        "title": row["title"],
        "body": row["body"],
        "state": "open",
        "created_at": row["created_at"],
        "updated_at": row["created_at"],
        "reporter_replies": [],
        "labels": [],
        "assignees": [],
    }
