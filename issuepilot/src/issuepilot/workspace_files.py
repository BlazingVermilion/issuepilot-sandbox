"""Local workspace storage used by the standard-library management CLI."""

import os
from pathlib import Path
import tempfile


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        file.write(data)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_private_once(path: Path, data: bytes):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as file:
        if os.name == "posix":
            os.fchmod(file.fileno(), 0o600)
        file.write(data)


def read_settings(data: bytes):
    values = {}
    for line in data.decode("utf-8-sig").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
    return values
