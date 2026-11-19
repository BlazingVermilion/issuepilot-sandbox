"""Atomic per-case checkpoints. No partially appended JSONL can corrupt resume."""

import json, os
from pathlib import Path
from contextlib import contextmanager
from retrieval import dump, sha


def digest(value):
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@contextmanager
def exclusive(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / ".active.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(
            f"Active/stale lock: {lock}. Confirm no runner remains before manually removing this lock."
        )
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def initialize(folder, identity, resume):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / "manifest.json"
    fp = digest(identity)
    if p.exists():
        m = read(p)
        if not resume or m["fingerprint"] != fp:
            raise ValueError(
                "Run exists or fingerprint changed: use --resume with identical settings, otherwise a new output directory"
            )
    else:
        if (folder / "records").exists():
            raise ValueError("Orphaned records without manifest")
        m = {"fingerprint": fp, "identity": identity, "status": "PREPARING"}
        dump(p, m)
    (folder / "records").mkdir(exist_ok=True)
    return m


def records(folder, ids):
    p = Path(folder) / "records"
    files = {f.stem: f for f in p.glob("*.json")}
    if set(files) - set(ids):
        raise ValueError("Unexpected record IDs in run")
    result = []
    for i in ids:
        if i in files:
            r = read(files[i])
            if r.get("id") != i:
                raise ValueError("Record filename/ID mismatch")
            result.append(r)
    return result


def export(folder, rows):
    folder = Path(folder)
    tmp = folder / "predictions.jsonl.tmp"
    tmp.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    tmp.replace(folder / "predictions.jsonl")
