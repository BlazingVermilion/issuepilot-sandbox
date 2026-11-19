"""Verify input/ranking/decision parity against a supplied ai-bench directory, without inference."""

import argparse
import hashlib
import json
from pathlib import Path

from issuepilot.ai.classifier import classify
from issuepilot.ai.generation import answer
from issuepilot.ai.index import PolicyIndex
from issuepilot.ai.profile import MODEL_DIGEST


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line]


def check(ok, message):
    if not ok:
        raise ValueError(message)


def recorded(folder, key):
    manifest = json.loads((folder / "manifest.json").read_bytes())
    predictions = folder / "predictions.jsonl"
    check(
        manifest["status"] == "COMPLETED" and manifest["identity"]["model_digest"] == MODEL_DIGEST,
        f"Wrong run or model: {folder}",
    )
    check(
        hashlib.sha256(predictions.read_bytes()).hexdigest() == manifest["predictions_hash"],
        f"Predictions hash mismatch: {folder}",
    )
    rows = lines(predictions)
    check([r["id"] for r in rows] == manifest["identity"][key], f"ID order mismatch: {folder}")
    return rows


class Replay:
    def __init__(self, row):
        self.row = row
        self.count = 0

    def chat(self, stage, system, user, grammar):
        self.count += 1
        if stage == "classification":
            check(user == self.row["prompt"], f"Classifier input mismatch: {self.row['id']}")
            return self.row["raw_output"]
        return self.row["raw_payload"]


def run(bench, policy_dir):
    runs = bench / "runs"
    index = PolicyIndex(policy_dir)
    cases = {row["id"]: row for row in lines(bench / "data/scenarios.jsonl")}
    counts = {}
    for split in ("validation", "test"):
        saved = json.loads((runs / f"retrieval-policy-{split}" / "policy.json").read_bytes())
        check(
            index.content_sha256 == saved["policy_hash"], "The runtime policy differs from this run"
        )
        for row in saved["rows"]:
            docs, rank = index.retrieve(cases[row["id"]]["query"])
            check(
                (list(docs), rank) == (row["context_ids"], row["rank"]),
                f"Ranking mismatch: {row['id']}",
            )
        counts["retrieval_" + split] = len(saved["rows"])
    rows = recorded(
        runs / "rag-policy-pilot-final-validation/policy--pilot-refined", "scenario_ids"
    )
    for row in rows:
        c = cases[row["id"]]
        replay = Replay(row)
        value, docs, trace = answer(replay, index, c["query"], c["task"])
        out = row["output"]
        decision = out["action"] or ("EXPLANATION" if out["policy_answerable"] else "NO_POLICY")
        check(
            (value["decision"], value["claims"], docs, trace["path"], replay.count)
            == (decision, out["claims"], row["context"], row["generation_path"], len(row["calls"])),
            f"Policy output mismatch: {row['id']}",
        )
    counts["rag_outputs"] = len(rows)
    inputs = {
        str(i["issue_number"]): i["input"]
        for split in ("train", "validation", "test")
        for i in lines(bench / "data" / (split + ".jsonl"))
    }
    rows = recorded(runs / "classification-qwen3/none", "ids")
    for row in rows:
        title, sep, body = inputs[row["id"]].partition("\n\nDescription:")
        check(title.startswith("Title: ") and sep, f"Unexpected source format: {row['id']}")
        result = classify(Replay(row), {"title": title[7:], "body": body.removeprefix("\n")})
        check(result == row["prediction"], f"Classification mismatch: {row['id']}")
    counts["classification_outputs"] = len(rows)
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench", type=Path, required=True)
    parser.add_argument("--policy-dir", type=Path, default=Path("policies"))
    args = parser.parse_args()
    print(json.dumps(run(args.bench, args.policy_dir), indent=2))
