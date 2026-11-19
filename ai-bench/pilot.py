"""Evaluate the documented RAG pilot thresholds on a controlled comparison."""

import argparse
import json
import math
from pathlib import Path


CONTROL_MODES = {"full", "oracle", "none"}
SEMANTIC_MINIMUMS = {
    "correctness": 0.90,
    "completeness": 0.85,
    "groundedness": 0.95,
    "citation_precision": 0.95,
    "citation_recall": 0.90,
}


def evaluate_arm(summary):
    arm = summary["arm"]
    ranked_arm = summary.get("mode", arm.split("--", 1)[0]) not in CONTROL_MODES
    expected_n = summary.get("expected_n")
    answerable_n = summary.get("answerable_n", summary.get("false_abstention", {}).get("denominator"))
    action_n = summary.get("action_n")
    judge = summary.get("judge", {})
    critical = summary.get("critical_closure_context", {})
    unsupported = summary.get("false_answer", {})
    ranked = summary.get("retrieval", {}).get("4", {})
    ranked_n = summary.get("retrieval_n")
    gates = {}

    def gate(metric, value, threshold, support=None, *, maximum=False, applicable=True):
        if not applicable:
            status = "NOT_APPLICABLE"
        elif threshold is None or support == 0 or type(value) not in (float, int) or not math.isfinite(value):
            status = "UNMEASURED"
        else:
            passed = value <= threshold if maximum else value >= threshold
            status = "PASS" if passed else "FAIL"
        gates[metric] = {
            "value": value,
            "operator": "<=" if maximum else ">=",
            "threshold": threshold,
            "support": support,
            "status": status,
        }

    split_complete = summary.get("split_complete")
    eligible_n = judge.get("eligible_n")
    scored_n = judge.get("scored_n")
    judge_current = summary.get("judge_evaluator_current")
    processed_n = summary.get("processed_n")
    gate("planned_cases_missing", abs(expected_n - processed_n) if expected_n is not None and processed_n is not None else None,
         0, expected_n, maximum=True)
    gate("full_split_verified", int(split_complete) if split_complete is not None else None, 1)
    gate("answerable_queries_ranked", ranked_n, answerable_n, answerable_n, applicable=ranked_arm)
    for name, threshold in (("recall", 0.90), ("mrr", 0.85), ("ndcg", 0.85)):
        gate(f"{name}_at_4", ranked.get(name), threshold, ranked_n, applicable=ranked_arm)
    gate("context_reference_recall", summary.get("context_reference_recall"), 0.95, answerable_n)
    gate("complete_reference_context_rate", summary.get("complete_reference_context_rate"), 0.90, answerable_n)
    missing_ids = critical.get("missing_ids")
    gate("critical_closure_missing_evidence", len(missing_ids) if missing_ids is not None else None,
         0, critical.get("denominator"), maximum=True)
    gate("first_attempt_protocol_validity", summary.get("first_attempt_protocol_validity_rate",
         summary.get("protocol_validity_rate")), 0.99, expected_n)
    gate("final_error_rate", summary.get("error_rate_processed"), 0.01, expected_n, maximum=True)
    gate("action_accuracy", summary.get("action_accuracy"), 0.90, action_n)
    for label in ("ALLOW", "BLOCK", "NEED_FACTS"):
        stats = summary.get("action_per_class", {}).get(label, {})
        gate(f"action_recall_{label}", stats.get("recall"), 0.85, stats.get("support"))
    unsafe = summary.get("unsafe_allow_non_allow", {})
    gate("unsafe_allow", unsafe.get("count"), 0, unsafe.get("denominator"), maximum=True)
    citations = summary.get("citation_id_validity", {})
    gate("citation_id_validity", citations.get("rate"), 1, citations.get("denominator"))
    abstention = summary.get("false_abstention", {})
    gate("false_abstention_rate", abstention.get("rate"), 0.10, answerable_n, maximum=True)
    gate("false_answer_rate", unsupported.get("rate"), 0.05, unsupported.get("denominator"), maximum=True)
    timing = summary.get("timing", {})
    for key, threshold, applicable in (
        ("latency_seconds", 15, True), ("retrieval_seconds", 1, ranked_arm),
    ):
        stats = timing.get(key, {})
        gate(f"{key}_p95", stats.get("p95"), threshold, stats.get("observed_n"),
             maximum=True, applicable=applicable)
        gate(f"{key}_observations", stats.get("observed_n"), expected_n, expected_n,
             applicable=applicable)
    gate("judge_evaluator_current", int(judge_current) if judge_current is not None else None, 1)
    gate("judge_coverage_valid", judge.get("coverage_valid") if judge_current is not None else None,
         1, eligible_n)
    for metric, threshold in SEMANTIC_MINIMUMS.items():
        gate(f"semantic_{metric}", judge.get("estimates", {}).get(metric),
             threshold, judge.get("estimate_n", {}).get(metric))
    gate("semantic_task_success_all", summary.get("semantic_task_success_all"), 0.85, expected_n)

    statuses = {check["status"] for check in gates.values()}
    if not ranked_arm:
        status = "CONTROL"
    elif "FAIL" in statuses:
        status = "FAIL"
    elif "UNMEASURED" in statuses:
        status = "INCOMPLETE"
    else:
        status = "PASS"
    return {
        "arm": arm,
        "split": summary.get("split"),
        "status": status,
        "expected_n": expected_n,
        "gates": gates,
        "critical_closure_missing_ids": missing_ids,
        "first_attempt_normalized_n": summary.get("normalization_n"),
        "generation_paths": summary.get("generation_paths", {}),
        "model_only_raw_action": summary.get("model_only_raw_action", {}),
        "unsupported_errors": unsupported.get("errors"),
        "unsupported_failure_or_wrong_rate": unsupported.get("failure_or_wrong_rate"),
        "judge_scored_n": scored_n,
        "judge_eligible_n": eligible_n,
        "judge_failed_n": judge.get("failed_n"),
        "judge_coverage_all": scored_n / expected_n if expected_n and scored_n is not None else None,
        "judge_objective_audit": judge.get("objective_audit"),
    }


def assess(results):
    if not isinstance(results, list) or not results:
        raise ValueError("Expected a nonempty RAG comparison list")
    arms = [evaluate_arm(summary) for summary in results]
    if len({arm["arm"] for arm in arms}) != len(arms):
        raise ValueError("Duplicate arms in comparison")
    return {
        "rubric": "rag-pilot-v1",
        "passing_arms": [arm["arm"] for arm in arms if arm["status"] == "PASS"],
        "arms": arms,
    }


def render(report):
    def fmt(value):
        return "N/A" if value is None else f"{value:.4g}"

    lines = [
        "# RAG pilot assessment",
        "",
        "Thresholds apply to this benchmark split. Semantic values are judge estimates. "
        "Executor authorization, recovery, idempotency, and deployment load need separate evidence.",
        "",
        "| Arm | Split | Cases | Status | Judge scored / eligible | Judge coverage, all cases | Judge failures | Unsupported errors | Unsupported wrong or error rate |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for arm in report["arms"]:
        lines.append(
            f"| {arm['arm']} | {arm['split'] or 'Unknown'} | {fmt(arm['expected_n'])} | {arm['status']} "
            f"| {fmt(arm['judge_scored_n'])} / {fmt(arm['judge_eligible_n'])} | {fmt(arm['judge_coverage_all'])} "
            f"| {fmt(arm['judge_failed_n'])} | {fmt(arm['unsupported_errors'])} "
            f"| {fmt(arm['unsupported_failure_or_wrong_rate'])} |"
        )
    lines += ["", "A ranked arm needs every applicable gate to pass. Control arms provide diagnostics."]
    for arm in report["arms"]:
        lines += ["", f"## {arm['arm']}", "",
                  "| Gate | Observed | Target | Support | Status |", "|---|---:|---:|---:|---|"]
        for metric, check in arm["gates"].items():
            lines.append(
                f"| {metric} | {fmt(check['value'])} | {check['operator']} {fmt(check['threshold'])} "
                f"| {fmt(check['support'])} | {check['status']} |"
            )
        if arm["critical_closure_missing_ids"]:
            lines += ["", "Closure cases missing reference evidence — "
                      + ", ".join(arm["critical_closure_missing_ids"]) + "."]
        if arm["generation_paths"]:
            paths = ", ".join(f"{key} ({count})" for key, count in sorted(arm["generation_paths"].items()))
            lines += ["", f"Generation paths by case count — {paths}."]
        model = arm["model_only_raw_action"]
        if model.get("denominator"):
            lines += ["", f"Raw model action accuracy is {fmt(model['accuracy'])} over "
                      f"{model['denominator']} model-path action cases."]
        audit = arm["judge_objective_audit"]
        if audit and audit["audited_n"]:
            lines += ["", f"Objective checks adjusted {audit['corrected_n']} of {audit['audited_n']} "
                      f"audited judge records. Numeric comparisons checked — {audit['numeric_checks']}."]
    return "\n".join(lines) + "\n"


def write(root, results):
    """Write pilot assessments next to the comparison without changing run artifacts."""
    root = Path(root)
    report = assess(results)
    (root / "pilot.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (root / "pilot.md").write_text(render(report), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", required=True, type=Path,
                        help="comparison.json produced by the RAG report command")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args(argv)
    report = assess(json.loads(args.comparison.read_text(encoding="utf-8-sig")))
    print(render(report) if args.format == "markdown" else json.dumps(report, indent=2), end="\n")


if __name__ == "__main__":
    main()
