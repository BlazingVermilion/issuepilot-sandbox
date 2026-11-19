"""Install the bench tokenizer once, from an existing download or its pinned upstream revision."""

import argparse
import hashlib
import tempfile
from pathlib import Path
from urllib.request import urlopen

from issuepilot.ai.profile import TOKENIZER_REV, TOKENIZER_SHA


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-file", type=Path)
    p.add_argument("--out", type=Path, default=Path("models/qwen3-tokenizer.json"))
    args = p.parse_args()
    if args.out.exists() and hashlib.sha256(args.out.read_bytes()).hexdigest() == TOKENIZER_SHA:
        print("Tokenizer already verified:", args.out)
        return
    url = f"https://huggingface.co/Qwen/Qwen3-8B/resolve/{TOKENIZER_REV}/tokenizer.json"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.from_file.open("rb") if args.from_file else urlopen(url, timeout=120) as src:
        data = src.read(16 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != TOKENIZER_SHA:
        raise SystemExit("Downloaded tokenizer does not match the benchmark digest")
    with tempfile.NamedTemporaryFile(dir=args.out.parent, delete=False) as out:
        out.write(data)
    Path(out.name).replace(args.out)
    print("Verified tokenizer:", args.out)


if __name__ == "__main__":
    main()
