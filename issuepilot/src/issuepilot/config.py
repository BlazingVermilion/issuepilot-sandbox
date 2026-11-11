"""Explicit environment settings; live writes require a separate opt-in."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator


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
    engine: str = "rules"
    model: str = "qwen2.5:7b"
    ollama_url: str = "http://localhost:11434"
    policy_dir: Path = Path("policies")
    allowed_labels: list[str] | None = None
    allowed_assignees: list[str] = []
    max_body_bytes: int = 262144

    @model_validator(mode="after")
    def validate_runtime(self):
        if self.mode not in {"demo", "live"} or self.engine not in {"rules", "ollama"}:
            raise ValueError("Unsupported mode or engine")
        if self.mode == "live":
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
