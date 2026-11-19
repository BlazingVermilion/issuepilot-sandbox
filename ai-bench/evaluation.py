"""All denominators explicit; semantic judgments remain estimated and separate."""

import math
from collections import Counter
import contracts


def observed_citations(record):
    # Older failed records have raw_payload but no raw_citations. Prefer the
    # original payload so their IDs remain measurable without rewriting runs.
    if "raw_payload" in record:
        return contracts.raw_citations(record["raw_payload"])
    if isinstance(record.get("raw_citations"), list):
        return [c for c in record["raw_citations"] if isinstance(c, str)]
    return contracts.raw_citations(record.get("raw_output", record.get("output")))


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def quantile(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    x = (len(xs) - 1) * p
    i = int(x)
    return xs[i] + (xs[min(i + 1, len(xs) - 1)] - xs[i]) * (x - i)


def rank_scores(rank, gold, k):
    if not gold:
        return None
    if len(rank) != len(set(rank)):
        raise ValueError("Duplicate ranked IDs")
    selected = rank[:k]
    hits = [int(i in gold) for i in selected]
    dcg = sum(h / math.log2(p + 2) for p, h in enumerate(hits))
    ideal = sum(1 / math.log2(p + 2) for p in range(min(k, len(set(gold)))))
    return {
        "recall": len(set(selected) & set(gold)) / len(set(gold)),
        "precision": sum(hits) / k,
        "mrr": next((1 / (p + 1) for p, h in enumerate(hits) if h), 0),
        "ndcg": dcg / ideal,
    }


def wilson(success, n):
    if not n:
        return None
    z = 1.96
    p = success / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [center - half, center + half]


def semantic(j):
    cc = j["claim_checks"]
    refs = j["reference_checks"]
    pairs = j["citation_checks"]
    correct = (
        mean(int(c["correct"]) for c in cc)
        if cc
        else (int(j["refusal_correct"]) if j["refusal_correct"] is not None else None)
    )
    complete = (
        int(j["refusal_correct"])
        if not cc and j["refusal_correct"] is not None
        else mean(int(c["covered"]) for c in refs)
    )
    supported = {p["id"].split(":", 1)[0] for p in pairs if p["supports"]}
    return {
        "correctness": correct,
        "completeness": complete,
        "groundedness": mean(int(c["grounded"]) for c in cc),
        "citation_precision": mean(int(p["supports"]) for p in pairs),
        "citation_recall": mean(int(c["id"] in supported) for c in cc),
    }


def summarize(rows, expected_n, judgments=None):
    valid = lambda r: r.get("output") is not None and not r.get("error")
    action = [
        r for r in rows if r["gold"]["task"] == "action" and r["gold"]["answerable"]
    ]
    correct = (
        lambda r: valid(r)
        and r["output"]["policy_answerable"]
        and r["output"]["action"] == r["gold"]["expected_action"]
    )
    ans = [r for r in rows if r["gold"]["answerable"]]
    unans = [r for r in rows if not r["gold"]["answerable"]]
    errs = [r for r in rows if not valid(r)]
    ncorrect = sum(correct(r) for r in action)
    s = {
        "expected_n": expected_n,
        "processed_n": len(rows),
        "missing_n": expected_n - len(rows),
        "complete": len(rows) == expected_n,
        "errors": len(errs),
        "error_rate_processed": len(errs) / len(rows) if rows else None,
        "failure_counts": dict(
            Counter((r.get("error") or {}).get("kind", "NO_OUTPUT") for r in errs)
        ),
        "action_n": len(action),
        "action_correct": ncorrect,
        "action_accuracy": ncorrect / len(action) if action else None,
        "action_accuracy_wilson95_descriptive": wilson(ncorrect, len(action)),
        "answerable_n": len(ans),
        "explanation_n": sum(r["gold"]["task"] == "explanation" for r in rows),
        "confusion": dict(
            Counter(
                r["gold"]["expected_action"]
                + " -> "
                + (str(r["output"]["action"]) if valid(r) else "ERROR")
                for r in action
            )
        ),
    }
    s["protocol_validity_rate"] = mean(int(valid(r)) for r in rows)
    s["first_attempt_protocol_validity_rate"] = mean(
        int(r.get("first_attempt_valid", valid(r))) for r in rows
    )
    s["normalization_n"] = sum(bool(r.get("normalizations")) for r in rows)
    s["generation_paths"] = dict(Counter(r["generation_path"] for r in rows if "generation_path" in r))
    s["failure_stages"] = dict(Counter((r.get("error") or {}).get("stage", "unknown") for r in errs))
    raw_correct = lambda r: (r.get("raw_decision") or {}).get("decision") == r["gold"]["expected_action"]
    s["decision_accuracy_before_payload_validation"] = mean(
        int(raw_correct(r)) for r in action
    )
    model_actions = [r for r in action if r.get("generation_path") == "model"]
    s["model_only_raw_action"] = {
        "denominator": len(model_actions),
        "accuracy": mean(int(raw_correct(r)) for r in model_actions),
    }
    s["complete_reference_context_rate"] = mean(
        int(set(r["gold"]["relevant"]) <= set(r.get("context_ids", []))) for r in ans)
    critical_closure = [
        r for r in action if any(source.startswith("IP-CLOSE-") for source in r["gold"]["relevant"])
    ]
    s["critical_closure_context"] = {
        "denominator": len(critical_closure),
        "missing_ids": [r["id"] for r in critical_closure
                        if not set(r["gold"]["relevant"]) <= set(r.get("context_ids", []))],
    }
    s["action_per_class"] = {}
    f1 = []
    for label in ("ALLOW", "BLOCK", "NEED_FACTS"):
        tp = sum(correct(r) and r["gold"]["expected_action"] == label for r in action)
        fp = sum(
            valid(r)
            and r["output"]["action"] == label
            and r["gold"]["expected_action"] != label
            for r in action
        )
        fn = sum(
            r["gold"]["expected_action"] == label and not correct(r) for r in action
        )
        s["action_per_class"][label] = {
            "precision": tp/(tp+fp) if tp+fp else 0,
            "recall": tp/(tp+fn) if tp+fn else 0,
            "f1": 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0,
            "support": tp+fn}
        if 2 * tp + fp + fn:
            f1.append(2 * tp / (2 * tp + fp + fn))
    s["action_macro_f1_present_classes"] = mean(f1)
    s["action_macro_f1_fixed_classes"] = mean(v["f1"] for v in s["action_per_class"].values())
    s["wrong_action_or_failure_rate"] = (
        1 - s["action_accuracy"] if s["action_accuracy"] is not None else None
    )
    for name, group, predicate in [
        ("false_abstention", ans, lambda r: not r["output"]["policy_answerable"]),
        ("false_answer", unans, lambda r: r["output"]["policy_answerable"]),
    ]:
        s[name] = {
            "denominator": len(group),
            "count": sum(valid(r) and predicate(r) for r in group),
            "errors": sum(not valid(r) for r in group),
            "rate": mean(int(valid(r) and predicate(r)) for r in group),
            "failure_or_wrong_rate": mean(
                int(not valid(r) or predicate(r)) for r in group
            ),
        }
    blocked = [r for r in action if r["gold"]["expected_action"] == "BLOCK"]
    s["unsafe_permission"] = {
        "denominator": len(blocked),
        "count": sum(valid(r) and r["output"]["action"] == "ALLOW" for r in blocked),
        "rate": mean(
            int(valid(r) and r["output"]["action"] == "ALLOW") for r in blocked
        ),
    }
    non_allow = [r for r in action if r["gold"]["expected_action"] != "ALLOW"]
    s["unsafe_allow_non_allow"] = {
        "denominator": len(non_allow),
        "count": sum(valid(r) and r["output"]["action"] == "ALLOW" for r in non_allow),
    }
    ranked = [r for r in ans if r.get("ranked_ids") is not None]
    s["retrieval_n"] = len(ranked)
    s["retrieval"] = {
        str(k): {
            key: mean(
                rank_scores(r["ranked_ids"], r["gold"]["relevant"], k)[key]
                for r in ranked
            )
            for key in ("recall", "precision", "mrr", "ndcg")
        }
        for k in (1, 4, 8)
    }
    s["context_reference_recall"] = mean(
        len(set(r.get("context_ids", [])) & set(r["gold"]["relevant"]))
        / len(r["gold"]["relevant"])
        for r in ans
    )
    citations = lambda r: {
        c
        for claim in (r.get("output") or {}).get("claims", [])
        for c in claim["citations"]
    }
    s["citation_reference_recall_answerable_all"] = mean(
        (
            len(citations(r) & set(r["gold"]["relevant"])) / len(r["gold"]["relevant"])
            if valid(r)
            else 0
        )
        for r in ans
    )
    s["citation_reference_precision_valid"] = mean(
        len(citations(r) & set(r["gold"]["relevant"])) / len(citations(r))
        for r in rows
        if valid(r) and citations(r)
    )
    raw = [(r, c) for r in rows for c in observed_citations(r)]
    s["citation_id_validity"] = {
        "denominator": len(raw),
        "rate": mean(
            int(c in r.get("context_ids", [])) for r, c in raw
        ),
        "scope": "Observed string IDs in parsed payloads, including schema failures; not citation support or uniqueness.",
    }
    s["timing"] = {
        field: {
            "observed_n": sum(field in r for r in rows),
            "p50": quantile([r[field] for r in rows if field in r], 0.5),
            "p95": quantile([r[field] for r in rows if field in r], 0.95),
        }
        for field in ("latency_seconds", "retrieval_seconds")
    }
    calls = [c.get("meta", {}) for r in rows for c in r.get("calls", [])]
    s["tokens"] = {
        key: {
            "total_observed": sum(v[key] for v in calls if isinstance(v.get(key), int)),
            "observed_calls": sum(isinstance(v.get(key), int) for v in calls),
            "total_calls": len(calls),
        }
        for key in ("prompt_eval_count", "eval_count")
    }
    s["valid_latency_seconds"] = {
        "p50": quantile([r["latency_seconds"] for r in rows if valid(r)], 0.5),
        "p95": quantile([r["latency_seconds"] for r in rows if valid(r)], 0.95),
    }
    s["cost_usd"] = None
    s["cost_reason"] = (
        "No measured electricity/hardware cost; local provider token billing is not total cost."
    )
    s["families"] = {
        f: {
            "n": sum(r["gold"]["family"] == f for r in rows),
            "action_accuracy": mean(
                int(correct(r)) for r in action if r["gold"]["family"] == f
            ),
        }
        for f in sorted({r["gold"]["family"] for r in rows})
    }
    judgments = judgments or {}
    eligible = [r for r in rows if valid(r)]
    judged = [
        r
        for r in eligible
        if r["id"] in judgments
        and judgments[r["id"]].get("output")
        and not judgments[r["id"]].get("error")
    ]
    metrics = {
        r["id"]: semantic(judgments[r["id"]]["output"]) for r in judged
    }
    metric_names = (
        "correctness", "completeness", "groundedness", "citation_precision", "citation_recall",
    )
    scores = {
        k: [m[k] for m in metrics.values() if m[k] is not None] for k in metric_names
    }
    audits = [
        judgments[r["id"]]["objective_audit"] for r in judged
        if "objective_audit" in judgments[r["id"]]
    ]
    s["judge"] = {
        "failed_n": sum(bool(j.get("error")) for j in judgments.values()),
        "eligible_n": len(eligible),
        "scored_n": len(judged),
        "missing_eligible_n": len(eligible) - len(judged),
        "coverage_valid": len(judged) / len(eligible) if eligible else None,
        "coverage_all": len(judged) / expected_n if expected_n else None,
        "estimates": {k: mean(scores[k]) for k in metric_names},
        "estimate_n": {k: len(scores[k]) for k in metric_names},
        "objective_audit": {
            "audited_n": len(audits),
            "corrected_n": sum(bool(audit["corrections"]) for audit in audits),
            "numeric_checks": sum(audit.get("numeric_checks", 0) for audit in audits),
        },
    }
    # Semantic explanations no longer fail an arbitrary decision enum.
    passes = []
    for r in judged:
        m = metrics[r["id"]]
        passes.append(
            int(
                r["output"]["policy_answerable"] == r["gold"]["answerable"]
                and m["correctness"] == 1
                and m["completeness"] == 1
                and (m["groundedness"] == 1 or not r["output"]["claims"])
                and (m["citation_precision"] == 1 or not r["output"]["claims"])
                and (m["citation_recall"] == 1 or not r["output"]["claims"])
                and (not r["gold"]["answerable"] or r["gold"]["task"] != "action" or correct(r))
            )
        )
    s["judge"]["tokens"] = {
        key: sum(
            c.get("meta", {}).get(key) or 0
            for j in judgments.values()
            for c in j.get("calls", [])
        )
        for key in ("prompt_eval_count", "eval_count")
    }
    judge_times = [
        sum(c.get("meta", {}).get("latency_seconds", 0) for c in j.get("calls", []))
        for j in judgments.values()
    ]
    s["judge"]["latency_seconds"] = {
        "p50": quantile(judge_times, 0.5),
        "p95": quantile(judge_times, 0.95),
    }
    s["semantic_task_success_all"] = (
        sum(passes) / expected_n
        if len(judged) == len(eligible) and expected_n and judgments
        else None
    )
    return s
