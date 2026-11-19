import json, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dataset, judging, lab, retrieval, storage
from test_lab import FakeClient


def args(out):
    return SimpleNamespace(
        model="mock",
        seed=42,
        num_ctx=32768,
        max_output=1024,
        split="validation",
        limit=1,
        k=4,
        device="cpu",
        strategy="direct",
        extraction_output=2048,
        modes=["bm25"],
        out=out,
        resume=False,
    )


class JudgeIntegration(unittest.TestCase):
    def test_qwen_judge_resume_and_joined_report(self):
        import judging

        class Judge(FakeClient):
            def __init__(self, a):
                self.a = a
                self.meta = {
                    "digest": "qwen3-32b-test",
                    "details": {"family": "qwen3"},
                    "ollama_version": {"version": "mock"},
                }

            def chat(self, system, user, schema, maximum=None):
                d = json.loads(user)
                return {
                    "claim_checks": [
                        {"id": i, "correct": True, "grounded": True, "reason": "test"}
                        for i in d["candidate_claim_ids"]
                    ],
                    "reference_checks": [
                        {"id": i["id"], "covered": True, "reason": "test"}
                        for i in d["reference_claims"]
                    ],
                    "citation_checks": [
                        {"id": i["id"], "supports": True, "reason": "test"}
                        for i in d["citation_pairs"]
                    ],
                    "refusal_correct": True if not d["candidate"]["claims"] else None,
                }, {"prompt_eval_count": 10, "eval_count": 10}

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a = args(str(root / "runs"))
            a.modes = ["bm25"]
            with patch.object(lab, "Ollama", FakeClient):
                lab.run(a)
            ja = SimpleNamespace(
                model="qwen3:32b",
                num_ctx=32768,
                max_output=4096,
                seed=42,
                resume=False,
                run=str(root / "runs/bm25--direct"),
                out=str(root / "judges/bm25--direct"),
                calibration=str(root / "cal"),
            )
            # Synthetic matching calibration fixture; no claim of real model calibration.
            retrieval.dump(
                root / "cal/calibration.json",
                {
                    "passed": True,
                    "settings": lab.judge_settings(ja, Judge(ja)),
                    "challenge_hash": retrieval.sha(
                        (lab.ROOT / "data/judge_challenges.jsonl").read_bytes()
                    ),
                },
            )
            with patch.object(lab, "Ollama", Judge):
                lab.judge(ja)
                ja.resume = True
                lab.judge(ja)
            lab.report(
                SimpleNamespace(out=str(root / "runs"), judges=str(root / "judges"))
            )
            result = storage.read(root / "runs/comparison.json")[0]
            self.assertEqual(result["judge"]["scored_n"], 1)
            self.assertEqual(result["judge"]["tokens"]["eval_count"], 10)
            manifest = storage.read(root / "judges/bm25--direct/manifest.json")
            self.assertTrue(manifest["judge_relationship"]["different_checkpoint"])
            with open(root / "judges/bm25--direct/predictions.jsonl", "a") as f:
                f.write("\n")
            with self.assertRaises(ValueError):
                lab.report(
                    SimpleNamespace(out=str(root / "runs"), judges=str(root / "judges"))
                )


class ObjectiveJudging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.challenges = {
            row["id"]: row for row in retrieval.jsonl(lab.ROOT / "data/judge_challenges.jsonl")
        }
        cls.corpus = {
            section["id"]: section["text"] for section in retrieval.sections(dataset.POLICY)[1]
        }

    def accepting_judge(self, candidate):
        claims, refs, pairs = judging.indexes(candidate["output"], candidate["gold"])
        return {
            "claim_checks": [
                {"id": i, "correct": True, "grounded": True, "reason": "optimistic"}
                for i in claims
            ],
            "reference_checks": [{"id": i, "covered": True, "reason": "optimistic"} for i in refs],
            "citation_checks": [{"id": i, "supports": True, "reason": "optimistic"} for i in pairs],
            "refusal_correct": None if claims else True,
        }

    def test_wrong_late_reminder_arithmetic_and_permission_fail(self):
        case = self.challenges["CAL-013"]
        raw = self.accepting_judge(case)
        result, audit = judging.adjudicate(raw, case, self.corpus)
        self.assertFalse(audit["decision_correct"])
        self.assertFalse(result["claim_checks"][0]["correct"])
        self.assertFalse(result["claim_checks"][0]["grounded"])
        self.assertFalse(result["citation_checks"][0]["supports"])
        self.assertTrue(all(not ref["covered"] for ref in result["reference_checks"]))
        self.assertTrue(raw["claim_checks"][0]["correct"])
        payload = json.loads(judging.payload(case, self.corpus))
        self.assertEqual(payload["computed_timing"]["deadline_hour"], 192)
        self.assertNotIn("action", payload["candidate"])

    def test_wrong_policy_constant_fails_even_for_correct_decision(self):
        case = self.challenges["CAL-014"]
        raw = self.accepting_judge(case)
        result, audit = judging.adjudicate(raw, case, self.corpus)
        self.assertTrue(audit["decision_correct"])
        self.assertFalse(result["claim_checks"][0]["correct"])
        self.assertFalse(result["claim_checks"][0]["grounded"])
        self.assertFalse(result["citation_checks"][0]["supports"])
        self.assertIn("48 hours", result["claim_checks"][0]["reason"])
        summary = judging.calibration_summary(
            [case], [{"id": case["id"], "output": result, "raw_output": raw, "error": None}]
        )
        self.assertFalse(summary["passed"])
        self.assertEqual(summary["false_accept"], 1)

    def test_equivalent_numeric_result_stays_valid(self):
        case = self.challenges["CAL-017"]
        gold = next(row for row in dataset.load("validation") if row["id"] == "RAG-004")
        claim = {
            "text": "The effective deadline is max(request time + 168 hours, reminder time + 48 hours) = 192 hours.",
            "citations": ["IP-INFO-003"],
        }
        case = {**case, "gold": gold, "output": {**case["output"], "action": "ALLOW", "claims": [claim]}}
        raw = self.accepting_judge(case)
        result, audit = judging.adjudicate(raw, case, self.corpus)
        self.assertEqual(result, raw)
        self.assertTrue(audit["decision_correct"])
        self.assertEqual(audit["numeric_checks"], 3)

    def test_correct_policy_refusal_covers_unsupported_reference(self):
        case = self.challenges["CAL-018"]
        for reason in (case["output"]["refusal_reason"], "NO_POLICY", "No policy sections supplied."):
            with self.subTest(reason=reason):
                refused = {**case, "output": {**case["output"], "refusal_reason": reason}}
                raw = self.accepting_judge(refused)
                raw["refusal_correct"] = raw["reference_checks"][0]["covered"] = False
                result, _ = judging.adjudicate(raw, refused, self.corpus)
                self.assertTrue(result["refusal_correct"])
                self.assertTrue(result["reference_checks"][0]["covered"])
