"""Ollama transport with explicit preflight, digests, budgets and visible errors."""

import json, time
from urllib.error import HTTPError, URLError
import retrieval as util
import token_budget


class CallError(RuntimeError):
    def __init__(self, kind, detail="", meta=None):
        super().__init__(detail or kind)
        self.kind = kind
        self.meta = meta or {}


class Ollama:
    def __init__(self, args):
        self.a = args
        listed = util.http(args.url, "/api/tags")["models"]
        normalized = lambda s: s if ":" in s else s + ":latest"
        match = [
            m
            for m in listed
            if normalized(m.get("name", m.get("model", ""))) == normalized(args.model)
        ]
        if not match:
            raise ValueError(
                f"Model not installed: {args.model}; use ollama pull first"
            )
        self.meta = dict(match[0])
        if not self.meta.get("digest"): raise ValueError("Ollama did not report a model digest")
        self.show = util.http(args.url, "/api/show", {"model": args.model})
        has_thinking = "thinking" in self.show.get("capabilities",[]) or (not self.show.get("capabilities") and args.model.startswith("qwen3:"))
        self.thinking_option = {"think": False} if has_thinking else {}
        self.meta["thinking_control"] = False if has_thinking else "omitted"
        info = self.show.get("model_info", {})
        limits = [
            v
            for k, v in info.items()
            if k.endswith(".context_length") and isinstance(v, int)
        ]
        self.meta["declared_context_length"] = min(limits) if limits else None
        if limits and args.num_ctx > min(limits):
            raise ValueError(
                f"Requested context {args.num_ctx} exceeds declared {min(limits)}. Reduce --num-ctx; do not silently assume a larger window works."
            )
        self.tokenizer, self.meta["budget_tokenizer"] = token_budget.load(
            args.model, getattr(args, "budget_tokenizer", "auto")
        )
        self.meta["ollama_version"] = util.http(args.url, "/api/version")
        try:
            util.http(
                args.url,
                "/api/chat",
                {
                    "model": args.model,
                    "messages": [{"role": "user", "content": "Reply OK."}],
                    **getattr(self,"thinking_option",{}),
                    "stream": False,
                    "keep_alive": "15m",
                    "options": {"num_ctx": args.num_ctx, "num_predict": 8},
                },
                args.timeout,
            )
        except HTTPError as e:
            raise CallError("MODEL_STARTUP_HTTP", str(e) + ": " + e.read(2000).decode("utf-8", errors="replace")) from e
        except (URLError, TimeoutError, OSError) as e:
            raise CallError("MODEL_STARTUP_TRANSPORT", str(e)) from e
        active = [
            m
            for m in util.http(args.url, "/api/ps").get("models", [])
            if m.get("digest") == self.meta.get("digest")
        ]
        if not active:
            raise ValueError("Model not resident after warmup")
        self.meta["runtime"] = active[0]
        if active[0].get("context_length", args.num_ctx) < args.num_ctx:
            raise ValueError("Server context smaller than requested")
        if not args.allow_cpu and active[0].get("size_vram", 0) <= 0:
            raise ValueError("No GPU offload; use --allow-cpu deliberately if required")

    def budget(self, system, user, maximum):
        # Conservative byte upper bound, not a claim of exact tokenizer counting.
        # Reserve chat-template overhead and generated output. Reject, never clip.
        tokenizer = getattr(self, "tokenizer", None)
        count = (
            sum(
                len(tokenizer.encode(s, add_special_tokens=False).ids)
                for s in (system, user)
            )
            if tokenizer
            else len((system + user).encode("utf-8"))
        )
        upper = count + maximum + 2048
        if upper > self.a.num_ctx:
            raise CallError(
                "CONTEXT_BUDGET",
                f"Conservative bound {upper} exceeds {self.a.num_ctx}; use a supported larger context or exclude this arm.",
            )
        return upper

    def chat(self, system, user, schema, maximum=None):
        a = self.a
        maximum = maximum or a.max_output
        upper = self.budget(system, user, maximum)
        start = time.perf_counter()
        try:
            data = util.http(
                a.url,
                "/api/chat",
                {
                    "model": a.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "format": schema,
                    **getattr(self,"thinking_option",{}),
                    "stream": False,
                    "keep_alive": "15m",
                    "options": {
                        "temperature": 0,
                        "seed": a.seed,
                        "num_ctx": a.num_ctx,
                        "num_predict": maximum,
                    },
                },
                a.timeout,
            )
        except HTTPError as e:
            raise CallError(
                "HTTP_ERROR", str(e) + ": " + e.read(2000).decode("utf-8", errors="replace"), {"latency_seconds": time.perf_counter() - start}
            ) from e
        except (URLError, TimeoutError, OSError) as e:
            raise CallError(
                "TRANSPORT_ERROR",
                str(e),
                {"latency_seconds": time.perf_counter() - start},
            ) from e
        meta = {
            k: data.get(k)
            for k in (
                "prompt_eval_count",
                "eval_count",
                "total_duration",
                "load_duration",
                "done_reason",
            )
        }
        meta.update(
            latency_seconds=time.perf_counter() - start,
            conservative_context_bound=upper,
            raw_response=data.get("message", {}).get("content", ""),
        )
        if data.get("model") is not None and data["model"] != self.meta.get("name", a.model):
            raise CallError("UNEXPECTED_MODEL", meta=meta)
        if data.get("done_reason") == "length":
            raise CallError("OUTPUT_TRUNCATED", meta=meta)
        if data.get("done") is not True:
            raise CallError("INCOMPLETE_RESPONSE", meta=meta)
        if (
            isinstance(meta.get("prompt_eval_count"), int)
            and meta["prompt_eval_count"] + maximum > a.num_ctx
        ):
            raise CallError("CONTEXT_RUNTIME_MISMATCH", meta=meta)
        try:
            return json.loads(meta["raw_response"]), meta
        except ValueError as e:
            raise CallError("JSON_INVALID", str(e), meta) from e
