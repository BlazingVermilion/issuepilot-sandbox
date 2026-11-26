"""Apply current deterministic judge checks to saved model judgments offline."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dataset
from evaluation import semantic, summarize
import judging
import retrieval
import storage


def verified_predictions(folder, manifest):
    predictions = folder / "predictions.jsonl"
    if (
        manifest["status"] != "COMPLETED"
        or retrieval.sha(predictions.read_bytes()) != manifest["predictions_hash"]
    ):
        raise ValueError(f"Incomplete run or predictions checksum mismatch: {folder}")
    return retrieval.jsonl(predictions)


def audit_arm(source, judge, corpus, policy_hash):
    source_manifest = storage.read(source / "manifest.json")
    judge_manifest = storage.read(judge / "manifest.json")
    source_id, judge_id = source_manifest["identity"], judge_manifest["identity"]
    if (
        source_id["policy_hash"] != policy_hash
        or judge_id["settings"]["policy_hash"] != policy_hash
        or judge_id["generator_fingerprint"] != source_manifest["fingerprint"]
        or judge_id["predictions_hash"] != source_manifest["predictions_hash"]
    ):
        raise ValueError(f"Mismatched generator, judge, or policy: {source.name}")
    generated = verified_predictions(source, source_manifest)
    judged = verified_predictions(judge, judge_manifest)
    eligible = [row for row in generated if row.get("output") and not row.get("error")]
    if (
        [row["id"] for row in generated] != source_id["scenario_ids"]
        or [row["id"] for row in judged] != [row["id"] for row in eligible]
        or [row["id"] for row in judged] != judge_id["eligible_ids"]
    ):
        raise ValueError(f"Prediction IDs do not align: {source.name}")

    changed = []
    original, revised = [], []
    for candidate, judgment in zip(eligible, judged):
        if judgment.get("error"):
            original.append(judgment)
            revised.append(judgment)
            continue
        raw = judgment.get("raw_output", judgment["output"])
        judging.validate(raw, candidate["output"], candidate["gold"])
        output, audit = judging.adjudicate(raw, candidate, corpus)
        original.append({**judgment, "output": raw})
        revised.append({**judgment, "output": output, "objective_audit": audit})
        if audit["corrections"]:
            changed.append({
                "id": candidate["id"],
                "before": semantic(raw),
                "after": semantic(output),
                **audit,
            })
    before, after = [
        summarize(generated, len(generated), {row["id"]: row for row in grades})
        for grades in (original, revised)
    ]
    return {
        "arm": source.name,
        "generator_predictions_hash": source_manifest["predictions_hash"],
        "historical_judge_predictions_hash": judge_manifest["predictions_hash"],
        "historical_judge_settings": judge_id["settings"],
        "generated_n": len(generated),
        "judged_n": after["judge"]["scored_n"],
        "changed_n": len(changed),
        "changes": changed,
        "before_semantic_estimates": before["judge"]["estimates"],
        "after_semantic_estimates": after["judge"]["estimates"],
        "before_semantic_task_success_all": before["semantic_task_success_all"],
        "after_semantic_task_success_all": after["semantic_task_success_all"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path, help="Generator experiment directory")
    parser.add_argument("--judges", required=True, type=Path, help="Historical judge experiment directory")
    parser.add_argument("--out", required=True, type=Path, help="Path to the judgment audit report JSON")
    args = parser.parse_args(argv)
    policy_hash = retrieval.sha(dataset.POLICY.read_bytes())
    corpus = {row["id"]: row["text"] for row in retrieval.sections(dataset.POLICY)[1]}
    arms = storage.read(args.run / "experiment.json")["planned_arms"]
    results = [audit_arm(args.run / arm, args.judges / arm, corpus, policy_hash) for arm in arms]
    report = {
        "method": "Deterministic audit of saved LLM judgments; no new LLM calls or judge calibration.",
        "semantic_aggregation": "Both snapshots use the current reporting formulas; before uses saved raw judge labels.",
        "policy_hash": policy_hash,
        "generated_n": sum(result["generated_n"] for result in results),
        "judged_n": sum(result["judged_n"] for result in results),
        "changed_n": sum(result["changed_n"] for result in results),
        "arms": results,
    }
    with storage.exclusive(args.out.parent):
        if args.out.exists():
            raise FileExistsError(args.out)
        retrieval.dump(args.out, report)
    print(
        f"Audited {report['judged_n']}/{report['generated_n']} records; "
        f"corrected {report['changed_n']}. Report: {args.out}"
    )


if __name__ == "__main__":
    main()
