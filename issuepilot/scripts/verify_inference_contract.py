"""Verify input/ranking/decision parity against a supplied ai-bench directory, without inference."""

import argparse
import hashlib
import json
from pathlib import Path

from issuepilot.ai.classifier import classify
from issuepilot.ai.generation import answer
from issuepilot.ai.index import PolicyIndex
from issuepilot.ai.profile import MODEL_DIGEST


def read_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line]


def check(ok, message):
    if not ok:
        raise ValueError(message)


def verified_predictions(folder, key):
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
    rows = read_records(predictions)
    check([r["id"] for r in rows] == manifest["identity"][key], f"ID order mismatch: {folder}")
    return rows


class RecordedModel:
    def __init__(self, row):
        self.row = row
        self.count = 0

    def chat(self, stage, system, user, grammar):
        self.count += 1
        if stage == "classification":
            check(user == self.row["prompt"], f"Classifier input mismatch: {self.row['id']}")
            return self.row["raw_output"]
        return self.row["raw_payload"]


def verify(data, policy_dir, classification, decisions, retrieval_validation, retrieval_test):
    index = PolicyIndex(policy_dir)
    cases = {row["id"]: row for row in read_records(data / "scenarios.jsonl")}
    counts = {}
    for split, path in (("validation", retrieval_validation), ("test", retrieval_test)):
        saved = json.loads(path.read_bytes())
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
    rows = verified_predictions(decisions, "scenario_ids")
    for row in rows:
        c = cases[row["id"]]
        model = RecordedModel(row)
        value, docs, trace = answer(model, index, c["query"], c["task"])
        out = row["output"]
        decision = out["action"] or ("EXPLANATION" if out["policy_answerable"] else "NO_POLICY")
        check(
            (value["decision"], value["claims"], docs, trace["path"], model.count)
            == (decision, out["claims"], row["context"], row["generation_path"], len(row["calls"])),
            f"Policy output mismatch: {row['id']}",
        )
    counts["rag_outputs"] = len(rows)
    inputs = {
        str(i["issue_number"]): i["input"]
        for split in ("train", "validation", "test")
        for i in read_records(data / (split + ".jsonl"))
    }
    rows = verified_predictions(classification, "ids")
    for row in rows:
        title, sep, body = inputs[row["id"]].partition("\n\nDescription:")
        check(title.startswith("Title: ") and sep, f"Unexpected source format: {row['id']}")
        result = classify(RecordedModel(row), {"title": title[7:], "body": body.removeprefix("\n")})
        check(result == row["prediction"], f"Classification mismatch: {row['id']}")
    counts["classification_outputs"] = len(rows)
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data", required=True, type=Path, help="Source splits and scenarios directory"
    )
    parser.add_argument("--policy-dir", type=Path, default=Path("policies"))
    parser.add_argument(
        "--classification", type=Path, required=True, help="Completed classification arm directory"
    )
    parser.add_argument(
        "--decisions", type=Path, required=True, help="Completed decision arm directory"
    )
    parser.add_argument(
        "--retrieval-validation", type=Path, required=True, help="Validation retrieval JSON"
    )
    parser.add_argument("--retrieval-test", type=Path, required=True, help="Test retrieval JSON")
    print(json.dumps(verify(**vars(parser.parse_args())), indent=2))
