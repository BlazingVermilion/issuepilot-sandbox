"""Count content tokens with the matching published Qwen tokenizer.

A separate 2,048-token reserve covers chat-template overhead. This is not a
claim that an arbitrary Ollama template has been reproduced byte for byte.
Unknown model tags retain the conservative UTF-8 byte bound.
"""

import json
from pathlib import Path
import retrieval as util

MODELS = {"qwen3:8b": "Qwen/Qwen3-8B", "qwen2.5:7b": "Qwen/Qwen2.5-7B-Instruct", "qwen3:32b": "Qwen/Qwen3-32B"}


def load(model, mode="auto"):
    repo = MODELS.get(model)
    if mode == "byte" or repo is None:
        return None, {"method": "utf8-upper-bound"}
    from huggingface_hub import HfApi, snapshot_download
    from tokenizers import Tokenizer

    lockpath = util.ROOT / "tokenizers.lock.json"
    lock = json.loads(lockpath.read_text()) if lockpath.exists() else {}
    revision = lock.get(repo) or HfApi().model_info(repo).sha
    path = (
        Path(
            snapshot_download(
                repo,
                revision=revision,
                local_dir=util.ROOT
                / ".models"
                / (repo.replace("/", "--") + "-budget")
                / revision,
                allow_patterns=["tokenizer.json"],
                max_workers=1,
            )
        )
        / "tokenizer.json"
    )
    lock[repo] = revision
    util.dump(lockpath, lock)
    return Tokenizer.from_file(str(path)), {
        "method": "published-content-tokenizer-plus-reserve",
        "repo": repo,
        "revision": revision,
        "sha256": util.sha(path.read_bytes()),
        "template_reserve": 2048,
    }
