"""Create local secrets once; never overwrite an existing .env."""

import secrets
from pathlib import Path

path = Path(".env")
if path.exists():
    raise SystemExit(".env already exists; edit it deliberately instead of overwriting secrets.")
text = Path(".env.example").read_text(encoding="utf-8")
text = text.replace("GENERATE_WEBHOOK_SECRET", secrets.token_hex(32))
text = text.replace("GENERATE_REVIEWER_TOKEN", secrets.token_urlsafe(40))
text = text.replace("GENERATE_DATABASE_PASSWORD", secrets.token_hex(24))
text = text.replace("GENERATE_N8N_ENCRYPTION_KEY", secrets.token_hex(32))
path.write_text(text, encoding="utf-8")
path.chmod(0o600)
print("Created .env. Reviewer token is available in IP_REVIEWER_TOKENS; keep it private.")
