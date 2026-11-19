"""Read-only smoke test of the installed profile and Ollama model; never opens a GitHub client."""

from issuepilot.config import Settings
from issuepilot import replay
from issuepilot.ai.index import PolicyIndex
from issuepilot.ai.triage import draft
import argparse
import httpx
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-gpu",
        action="store_true",
        help="Require observed GPU offload on the Ollama host.",
    )
    args = parser.parse_args()
    settings = Settings()
    if settings.engine != "ollama":
        raise SystemExit("Use IP_ENGINE=ollama for this check")
    if settings.dataset_replay:
        rows = replay.snapshots(settings.replay_path)
        # The pilot always starts with the first test row, irrespective of its label.
        snapshot = next(row for row in rows.values() if row["split"] == "test")
        issue = replay.issue_source(snapshot)
    else:
        issue = {
            "title": "Saving a draft now crashes the editor",
            "body": "The save command stopped working after the latest update.",
        }
    proposal, _, trace, metrics = draft(
        settings,
        PolicyIndex(settings.policy_dir, settings.inference_profile),
        issue,
        {"allowed": ["bug", "feature-request"]},
    )
    result = {
        "model": metrics["model"],
        "digest": metrics["model_digest"],
        "stages": metrics["stages"],
        "category": proposal.category,
        "recommendation": proposal.next_step,
        "github_writes": 0,
    }
    if settings.dataset_replay:
        result["dataset_issue"] = issue["number"]
    if args.require_gpu:
        with httpx.Client(
            base_url=settings.ollama_url.rstrip("/"), trust_env=False, timeout=15
        ) as http:
            response = http.get("/api/ps")
            response.raise_for_status()
            vram = max(
                (
                    m.get("size_vram", 0)
                    for m in response.json().get("models", [])
                    if m.get("digest") == metrics["model_digest"]
                ),
                default=0,
            )
        if not vram:
            raise SystemExit("No GPU offload observed for the configured Ollama model")
        result["observed_gpu_bytes"] = vram
    print(json.dumps(result, indent=2))
    if metrics["stages"] != ["classification", "rag"]:
        raise SystemExit("Smoke issue did not exercise both model stages")


if __name__ == "__main__":
    main()
