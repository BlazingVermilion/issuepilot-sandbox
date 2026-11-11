"""Evaluate fixed workflow and optional LLM on a labeled, explicitly synthetic holdout."""

import argparse
import json
import time
from pathlib import Path
from issuepilot.agent import Analyst
from issuepilot.config import Settings
from issuepilot.policy import Policies

parser = argparse.ArgumentParser()
parser.add_argument("--engine", choices=["rules", "ollama"], default="rules")
parser.add_argument("--model", default="qwen2.5:7b")
parser.add_argument("--out", default="artifacts/evaluation.json")
args = parser.parse_args()
settings = Settings(
    _env_file=None,
    engine=args.engine,
    model=args.model,
    reviewer_tokens={"evaluation": "offline-evaluation-token-32-characters"},
)
policies = Policies(settings.policy_dir)
analyst = Analyst(settings, policies)
cases = json.loads(Path("fixtures/evaluation.json").read_text())
results = []
for case in cases:
    start = time.monotonic()
    row = {"id": case["id"], "expected": case["expected"]}
    try:
        output = analyst.draft({"issue": case["issue"]})
        proposal = output["proposal"]
        row.update(
            valid_contract=True,
            proposal=proposal,
            trace=output["trace"],
            correct_category=proposal["category"] == case["expected"]["category"],
            correct_missing=set(proposal["missing_fields"])
            == set(case["expected"]["missing_fields"]),
            correct_abstain=proposal["abstain"] == case["expected"]["abstain"],
        )
    except Exception as exc:
        row.update(
            valid_contract=False,
            error=type(exc).__name__,
            correct_category=False,
            correct_missing=False,
            correct_abstain=False,
        )
    row["latency_ms"] = round((time.monotonic() - start) * 1000)
    results.append(row)
summary = {
    key: sum(r[key] for r in results) / len(results)
    for key in ("valid_contract", "correct_category", "correct_missing", "correct_abstain")
}
report = {
    "dataset": "synthetic-sandbox-v1",
    "count": len(results),
    "engine": args.engine,
    "model": args.model if args.engine == "ollama" else None,
    "policy_hash": policies.hash,
    "limitations": [
        "Synthetic cases do not establish real-world accuracy.",
        "Citation substring checks establish provenance, not logical entailment.",
        "No human time savings or routing improvement measured by this script.",
    ],
    "summary": summary,
    "results": results,
}
path = Path(args.out)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(
    json.dumps(
        {"count": len(results), "engine": args.engine, "summary": summary, "output": str(path)},
        indent=2,
    )
)
