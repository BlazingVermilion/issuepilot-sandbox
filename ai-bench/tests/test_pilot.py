import unittest

import pilot


def candidate(arm="bm25--pilot"):
    return {
        "arm": arm,
        "split": "validation",
        "split_complete": True,
        "expected_n": 100,
        "processed_n": 100,
        "answerable_n": 80,
        "retrieval_n": 80,
        "retrieval": {"4": {"recall": 0.90, "mrr": 0.85, "ndcg": 0.85}},
        "context_reference_recall": 0.95,
        "complete_reference_context_rate": 0.90,
        "critical_closure_context": {"denominator": 20, "missing_ids": []},
        "first_attempt_protocol_validity_rate": 0.99,
        "error_rate_processed": 0.01,
        "action_n": 60,
        "action_accuracy": 0.90,
        "action_per_class": {
            label: {"recall": 0.85, "support": 20} for label in ("ALLOW", "BLOCK", "NEED_FACTS")
        },
        "unsafe_allow_non_allow": {"count": 0, "denominator": 40},
        "citation_id_validity": {"rate": 1, "denominator": 121},
        "false_abstention": {"rate": 0.10, "denominator": 80},
        "false_answer": {"rate": 0, "denominator": 20, "errors": 1, "failure_or_wrong_rate": 0.05},
        "timing": {
            "latency_seconds": {"p95": 15, "observed_n": 100},
            "retrieval_seconds": {"p95": 1, "observed_n": 100},
        },
        "judge_evaluator_current": True,
        "judge": {
            "eligible_n": 99,
            "scored_n": 99,
            "failed_n": 0,
            "coverage_valid": 1,
            "estimates": dict(pilot.SEMANTIC_MINIMUMS),
            "estimate_n": {metric: 90 for metric in pilot.SEMANTIC_MINIMUMS},
        },
        "semantic_task_success_all": 0.85,
    }


class PilotTests(unittest.TestCase):
    def test_inclusive_thresholds_and_visible_unsupported_error(self):
        result = pilot.assess([candidate()])
        self.assertEqual(result["passing_arms"], ["bm25--pilot"])
        arm = result["arms"][0]
        self.assertEqual(arm["unsupported_errors"], 1)
        self.assertEqual(arm["unsupported_failure_or_wrong_rate"], 0.05)
        self.assertEqual(arm["judge_coverage_all"], 0.99)

    def test_legacy_estimates_do_not_establish_current_judge_or_full_split(self):
        summary = candidate()
        del summary["judge_evaluator_current"], summary["split_complete"]
        arm = pilot.evaluate_arm(summary)
        self.assertEqual(arm["status"], "INCOMPLETE")
        self.assertEqual(arm["gates"]["judge_evaluator_current"]["status"], "UNMEASURED")
        self.assertEqual(arm["gates"]["full_split_verified"]["status"], "UNMEASURED")

    def test_zero_support_and_partial_ranking_cannot_pass(self):
        summary = candidate()
        summary["action_per_class"]["NEED_FACTS"] = {"recall": 0, "support": 0}
        summary["retrieval_n"] = 79
        arm = pilot.evaluate_arm(summary)
        self.assertEqual(arm["status"], "FAIL")
        self.assertEqual(arm["gates"]["action_recall_NEED_FACTS"]["status"], "UNMEASURED")
        self.assertEqual(arm["gates"]["answerable_queries_ranked"]["status"], "FAIL")

    def test_diagnostics_remain_controls_with_good_scores(self):
        arm = pilot.evaluate_arm(candidate("oracle--pilot"))
        self.assertEqual(arm["status"], "CONTROL")
        self.assertEqual(arm["gates"]["recall_at_4"]["status"], "NOT_APPLICABLE")

    def test_refusal_completeness_and_task_success_use_the_planned_denominator(self):
        from evaluation import summarize

        refused = {
            "id": "refused",
            "gold": {"task": "explanation", "answerable": False, "family": "unsupported"},
            "output": {"policy_answerable": False, "claims": []},
            "latency_seconds": 1,
        }
        failure = {**refused, "id": "failed", "output": None, "error": {"kind": "OUTPUT_TRUNCATED"}}
        verdict = {
            "claim_checks": [],
            "reference_checks": [{"covered": False}],
            "citation_checks": [],
            "refusal_correct": True,
        }
        summary = summarize([refused, failure], 3, {"refused": {"output": verdict}})
        self.assertEqual(summary["judge"]["estimates"]["completeness"], 1)
        self.assertEqual(summary["judge"]["estimate_n"]["groundedness"], 0)
        self.assertEqual(summary["judge"]["coverage_all"], 1 / 3)
        self.assertEqual(summary["semantic_task_success_all"], 1 / 3)
        self.assertEqual(summary["false_answer"]["errors"], 1)

    def test_model_raw_action_support_excludes_deterministic_answers(self):
        from evaluation import summarize

        deterministic = {
            "id": "deadline",
            "gold": {"task": "action", "answerable": True, "expected_action": "BLOCK",
                     "family": "deadline", "relevant": ["IP-CLOSE-002"]},
            "output": {"policy_answerable": True, "action": "BLOCK",
                       "claims": [{"citations": ["IP-CLOSE-002"]}]},
            "context_ids": ["IP-CLOSE-002"],
            "latency_seconds": 1,
            "generation_path": "deadline_check",
            "first_attempt_valid": True,
        }
        model = {**deterministic, "id": "model", "generation_path": "model",
                 "first_attempt_valid": False, "normalizations": ["duplicate_citations"],
                 "raw_decision": {"decision": "BLOCK"}}
        summary = summarize([deterministic, model], 2)
        self.assertEqual(summary["generation_paths"], {"deadline_check": 1, "model": 1})
        self.assertEqual(summary["protocol_validity_rate"], 1)
        self.assertEqual(summary["first_attempt_protocol_validity_rate"], 0.5)
        self.assertEqual(summary["decision_accuracy_before_payload_validation"], 0.5)
        self.assertEqual(summary["model_only_raw_action"], {"denominator": 1, "accuracy": 1})
        self.assertEqual(summary["citation_id_validity"]["denominator"], 2)
        self.assertEqual(summary["critical_closure_context"], {"denominator": 2, "missing_ids": []})


if __name__ == "__main__":
    unittest.main()
