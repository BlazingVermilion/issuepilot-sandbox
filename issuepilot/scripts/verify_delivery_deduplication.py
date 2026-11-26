"""Send a synthetic, signed delivery twice to prove ingestion deduplication."""

import argparse
import hashlib
import hmac
import json
import uuid
import httpx
from issuepilot.config import Settings

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://localhost:8000/webhooks/github")
parser.add_argument("--number", type=int, default=1)
args = parser.parse_args()
settings = Settings()
raw = json.dumps(
    {
        "action": "opened",
        "repository": {"full_name": settings.repo},
        "issue": {"number": args.number},
    },
    separators=(",", ":"),
).encode()
headers = {
    "Content-Type": "application/json",
    "X-GitHub-Event": "issues",
    "X-GitHub-Delivery": str(uuid.uuid4()),
    "X-Hub-Signature-256": "sha256="
    + hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest(),
}
with httpx.Client(timeout=15) as client:
    for attempt in range(2):
        response = client.post(args.url, content=raw, headers=headers)
        response.raise_for_status()
        print(json.dumps({"attempt": attempt + 1, "result": response.json()}))
