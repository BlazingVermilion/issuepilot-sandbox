"""Read-only Ollama client for the single selected model. Never fetch a model in a worker."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import httpx

from .profile import CONTEXT, TOKENIZER_SHA


@lru_cache(maxsize=2)
def _tokenizer(path):
    from tokenizers import Tokenizer

    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != TOKENIZER_SHA:
        raise ValueError("Qwen tokenizer digest mismatch; run prepare_tokenizer.py")
    return Tokenizer.from_str(data.decode("utf-8"))


def budget(system, user, ctx, maximum, tokenizer_path):
    available = ctx - maximum - 2048  # Same template reserve as the measured profile.
    if len((system + user).encode("utf-8")) <= available:
        return
    if tokenizer_path is None:
        raise ValueError("Input exceeds experimental byte context budget; no text was clipped")
    tok = _tokenizer(str(tokenizer_path))
    if sum(len(tok.encode(t, add_special_tokens=False).ids) for t in (system, user)) > available:
        raise ValueError(
            "Input exceeds this stage's context budget; no issue or policy was clipped"
        )


class Client:
    def __init__(self, settings, transport=None):
        self.url = settings.ollama_url.rstrip("/")
        self.selected = settings.ai_profile == "selected"
        self.model = settings.model
        self.digest = settings.expected_digest
        self.profile = settings.inference_profile
        self.thinking = {"think": False} if self.selected else {}
        self.tokenizer_path = settings.tokenizer_path if self.selected else None
        self.transport = transport
        self.verified = False
        self.calls = []

    def chat(self, stage, system, user, schema):
        ctx, maximum = CONTEXT[stage]
        budget(system, user, ctx, maximum, self.tokenizer_path)
        with httpx.Client(
            base_url=self.url, timeout=180, trust_env=False, transport=self.transport
        ) as http:
            if not self.verified:
                resp = http.get("/api/tags")
                resp.raise_for_status()
                if not any(
                    m.get("name") == self.model and m.get("digest") == self.digest
                    for m in resp.json()["models"]
                ):
                    raise ValueError(
                        f"Missing expected digest for {self.model} on the configured Ollama host"
                    )
                if not self.selected:
                    resp = http.post("/api/show", json={"model": self.model})
                    resp.raise_for_status()
                    data = resp.json()
                    if "thinking" in data.get("capabilities", []):
                        self.thinking = {"think": False}
                    declared = [
                        v
                        for k, v in data.get("model_info", {}).items()
                        if k.endswith(".context_length") and type(v) is int
                    ]
                    if declared and min(declared) < max(c for c, _ in CONTEXT.values()):
                        raise ValueError(
                            "This model does not support the context required by the runtime profile"
                        )
                self.verified = True
            resp = http.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    **self.thinking,
                    "keep_alive": "15m",
                    "format": schema,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "options": {
                        "num_ctx": ctx,
                        "num_predict": maximum,
                        "temperature": 0,
                        "seed": 42,
                    },
                },
            )
            resp.raise_for_status()
            result = resp.json()
        if (
            result.get("done") is not True
            or result.get("done_reason") != "stop"
            or result.get("model") != self.model
        ):
            raise ValueError("Incomplete response or unexpected Ollama model; no proposal produced")
        if (
            not isinstance(result.get("prompt_eval_count"), int)
            or result["prompt_eval_count"] + maximum > ctx
        ):
            raise ValueError("Missing or inconsistent model context usage")
        obj = json.loads(result["message"]["content"])
        self.calls.append(
            {
                "stage": stage,
                "input_tokens": result["prompt_eval_count"],
                "output_tokens": result.get("eval_count"),
            }
        )
        return obj

    def telemetry(self):
        return {
            "profile": self.profile,
            "model": self.model if self.calls else None,
            "model_digest": self.digest if self.verified and self.calls else None,
            "calls": len(self.calls),
            "stages": [r["stage"] for r in self.calls],
            **{
                k: sum(r[k] for r in self.calls)
                if all(isinstance(r[k], int) for r in self.calls)
                else None
                for k in ("input_tokens", "output_tokens")
            },
        }
