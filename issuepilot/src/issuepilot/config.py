"""Explicit environment settings; live writes require a separate opt-in."""

from pathlib import Path
import re
from .ai.profile import MODEL, MODEL_DIGEST, PROFILE
from uuid import UUID
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator, Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IP_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./data/issuepilot.db"
    checkpoint_url: str = "data/checkpoints.db"
    mode: str = "demo"
    repo: str = "demo/issuepilot-sandbox"
    webhook_secret: str = ""
    reviewer_tokens: dict[str, str] = {}
    github_token: str = ""
    writes_enabled: bool = False
    engine: str = "ollama"
    dataset_replay: bool = False
    replay_run_id: UUID | None = None
    replay_path: Path = Path("data/replay/issues.jsonl")
    model: str = MODEL
    ai_profile: str = "selected"
    model_digest: str = ""

    @property
    def expected_digest(self):
        return MODEL_DIGEST if self.ai_profile == "selected" else self.model_digest

    @property
    def inference_profile(self):
        return (
            PROFILE
            if self.ai_profile == "selected"
            else "experimental/" + self.model + "/" + self.model_digest
        )

    tokenizer_path: Path = Path("models/qwen3-tokenizer.json")
    ollama_url: str = "http://localhost:11434"
    policy_dir: Path = Path("policies")
    allowed_labels: list[str] | None = None
    allowed_assignees: list[str] = []
    max_clarification_rounds: int = Field(default=3, ge=1, le=10)
    max_input_chars: int = Field(default=60000, ge=1000, le=60000)
    benchmark_dir: Path = Path("artifacts/benchmarks")
    max_body_bytes: int = 262144

    @model_validator(mode="after")
    def validate_runtime(self):
        if self.dataset_replay:
            from .replay import REPO

            if (
                (self.mode, self.repo, self.engine) != ("demo", REPO, "ollama")
                or self.github_token
                or self.writes_enabled
                or len(self.webhook_secret) < 32
                or not self.replay_run_id
            ):
                raise ValueError(
                    "Dataset replay requires the isolated demo repository, Ollama, a signing secret, no GitHub token and disabled external writes"
                )
        if self.mode not in {"demo", "live"} or self.engine not in {"rules", "ollama"}:
            raise ValueError("Unsupported mode or engine")
        if self.ai_profile not in {"selected", "experimental"}:
            raise ValueError("IP_AI_PROFILE must be selected or experimental")
        if self.ai_profile == "experimental" and (
            self.mode != "demo"
            or self.writes_enabled
            or self.engine != "ollama"
            or not re.fullmatch(r"[A-Za-z0-9._/-]+:[A-Za-z0-9._-]+", self.model)
            or not re.fullmatch(r"[a-f0-9]{64}", self.model_digest)
        ):
            raise ValueError(
                "Experimental profiles require an explicit Ollama model:tag, full local IP_MODEL_DIGEST and demo mode with writes disabled"
            )
        if (
            self.ai_profile == "selected"
            and self.model_digest
            and self.model_digest != MODEL_DIGEST
        ):
            raise ValueError("Digest differs from the selected benchmark profile")
        if self.engine == "ollama" and self.ai_profile == "selected" and self.model != MODEL:
            raise ValueError("The selected profile requires IP_MODEL=qwen3:8b")
        if self.mode == "live":
            if self.engine != "ollama":
                raise ValueError(
                    "Live triage requires IP_ENGINE=ollama; rules are for offline demos"
                )
            if len(self.webhook_secret) < 32 or not self.github_token:
                raise ValueError("Live mode requires a webhook secret and GitHub token")
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Live mode requires PostgreSQL")
            if not self.checkpoint_url.startswith("postgresql://"):
                raise ValueError("Live mode requires a PostgreSQL checkpointer")
        if not self.reviewer_tokens or any(len(v) < 32 for v in self.reviewer_tokens.values()):
            raise ValueError("Configure IP_REVIEWER_TOKENS with distinct 32+ character tokens")
        if len(set(self.reviewer_tokens.values())) != len(self.reviewer_tokens):
            raise ValueError("Each reviewer must have a distinct token")
        return self
