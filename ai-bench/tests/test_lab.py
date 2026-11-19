import copy, json, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import contracts, dataset, evaluation as ev, judging, lab, retrieval, storage
import vector_store
from client import Ollama, CallError


class FakeClient:
    def __init__(self, a):
        self.a = a
        self.meta = {
            "digest": "fake-generator",
            "details": {"family": "qwen2"},
            "ollama_version": {"version": "mock"},
        }

    def budget(self, *args):
        return 100

    def chat(self, system, user, schema, maximum=None):
        if system == contracts.EXTRACT_SYSTEM:
            return {"evidence": []}, {"prompt_eval_count": 10, "eval_count": 10}
        assert "reference_claims" not in user and "expected_action" not in user
        if system == contracts.DECISION_SYSTEM:
            return {"decision": "BLOCK"}, {"prompt_eval_count": 10, "eval_count": 10}
        return {"claims": [{"text": "A partial reply blocks closure.",
                            "citations": ["IP-INFO-002"]}]}, {"prompt_eval_count": 20, "eval_count": 20}



def row(task="action", expected="BLOCK"):
    return {
        "id": "x",
        "gold": {
            "task": task,
            "expected_action": expected,
            "answerable": True,
            "relevant": ["p"],
            "family": "f",
        },
        "output": {
            "policy_answerable": True,
            "action": expected,
            "claims": [{"text": "claim", "citations": ["p"]}],
            "refusal_reason": "",
        },
        "error": None,
        "context_ids": ["p"],
        "ranked_ids": ["p"],
        "latency_seconds": 1,
        "calls": [],
    }


class Tests(unittest.TestCase):
    def test_dataset(self):
        self.assertEqual(dataset.validate()["n"], 52)

    def test_explanation_null(self):
        r = row("explanation", None)
        contracts.validate_output(r["output"], ["p"], "explanation")

    def test_explanation_wrong_action(self):
        with self.assertRaises(Exception):
            contracts.validate_output(row()["output"], ["p"], "explanation")

    def test_unknown_facts_answerable(self):
        contracts.validate_output(row(expected="NEED_FACTS")["output"], ["p"], "action")

    def test_refusal_contract(self):
        v = {
            "policy_answerable": False,
            "action": None,
            "claims": [],
            "refusal_reason": "No authority",
        }
        contracts.validate_output(v, [], "action")
        v["action"] = "BLOCK"
        with self.assertRaises(Exception):
            contracts.validate_output(v, [], "action")

    def test_invalid_citation(self):
        with self.assertRaises(Exception):
            contracts.validate_output(row()["output"], ["other"], "action")

    def test_no_citations_rejected(self):
        r = row()["output"]
        r["claims"][0]["citations"] = []
        with self.assertRaises(Exception):
            contracts.validate_output(r, ["p"], "action")

    def test_quotes_verified(self):
        contracts.validate_extraction(
            {"evidence": [{"section_id": "p", "quote": "actual"}]},
            {"p": "The actual rule"},
        )
        with self.assertRaises(ValueError):
            contracts.validate_extraction(
                {"evidence": [{"section_id": "p", "quote": "invented"}]},
                {"p": "The actual rule"},
            )

    def test_retrieval_perfect(self):
        self.assertEqual(
            ev.rank_scores(["a"], ["a"], 1), dict(recall=1, precision=1, mrr=1, ndcg=1)
        )

    def test_duplicate_rank_rejected(self):
        with self.assertRaises(ValueError):
            ev.rank_scores(["a", "a"], ["a"], 4)

    def test_rank_penalty(self):
        self.assertEqual(ev.rank_scores(["x", "a"], ["a"], 2)["mrr"], 0.5)

    def test_no_gold_na(self):
        self.assertIsNone(ev.rank_scores(["x"], [], 4))

    def test_explanation_excluded(self):
        s = ev.summarize([row(), row("explanation", None)], 2)
        self.assertEqual(s["action_n"], 1)
        self.assertEqual(s["explanation_n"], 1)

    def test_errors_count_wrong(self):
        r = row()
        r.update(output=None, error={"kind": "HTTP_ERROR"})
        s = ev.summarize([row(), r], 2)
        self.assertEqual(s["action_accuracy"], 0.5)

    def test_incomplete_visible(self):
        self.assertFalse(ev.summarize([row()], 2)["complete"])

    def test_no_judge_na(self):
        self.assertIsNone(ev.summarize([row()], 1)["judge"]["estimates"]["correctness"])

    def test_unsafe(self):
        r = row()
        r["output"]["action"] = "ALLOW"
        self.assertEqual(ev.summarize([r], 1)["unsafe_permission"]["count"], 1)

    def test_tokens_both_stages(self):
        r = row()
        r["calls"] = [{"meta": {"eval_count": 10}}, {"meta": {"eval_count": 20}}]
        self.assertEqual(
            ev.summarize([r], 1)["tokens"]["eval_count"]["total_observed"], 30
        )

    def test_dependencies_oracle(self):
        ret = retrieval.Retriever(dataset.POLICY, "bm25")
        docs, rank = dataset.assemble(ret, {"relevant": ["IP-CLOSE-003"]}, "oracle", 4)
        self.assertIn("IP-VERIFY-002", docs)
        self.assertIsNone(rank)

    def test_bm25_runs(self):
        self.assertEqual(
            len(
                retrieval.Retriever(dataset.POLICY, "bm25").search("close duplicate", 8)
            ),
            8,
        )

    def test_selfjudge_alias_blocked(self):
        with self.assertRaises(ValueError):
            judging.independent({"digest": "x"}, {"digest": "x"})

    def test_same_family_different_checkpoint_allowed(self):
        self.assertFalse(
            judging.independent(
                {"digest": "x", "details": {"family": "qwen2"}},
                {"digest": "y", "details": {"family": "qwen2"}},
            )["different_architecture_family"]
        )

    def test_independent(self):
        judging.independent(
            {"digest": "x", "details": {"family": "qwen2"}},
            {"digest": "y", "details": {"family": "gemma3"}},
        )

    def test_judge_alignment(self):
        c = row()["output"]
        g = {"reference_claims": ["a"]}
        v = {
            "claim_checks": [
                {"id": "c1", "correct": True, "grounded": True, "reason": "ok"}
            ],
            "reference_checks": [{"id": "r1", "covered": True, "reason": "ok"}],
            "citation_checks": [{"id": "c1:p", "supports": True, "reason": "ok"}],
            "refusal_correct": None,
        }
        judging.validate(v, c, g)
        self.assertEqual(ev.semantic(v)["correctness"], 1)
        v["claim_checks"][0]["id"] = "wrong"
        with self.assertRaises(Exception):
            judging.validate(v, c, g)

    def test_semantic_precision_support_not_id(self):
        j = {
            "claim_checks": [{"id": "c1", "correct": True, "grounded": True}],
            "reference_checks": [{"covered": True}],
            "citation_checks": [{"id": "c1:p", "supports": False}],
            "refusal_correct": None,
        }
        self.assertEqual(ev.semantic(j)["citation_precision"], 0)
        self.assertEqual(ev.semantic(j)["citation_recall"], 0)

    def test_atomic_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            storage.initialize(d, {"a": 1}, False)
            retrieval.dump(Path(d) / "records/x.json", {"id": "x"})
            (Path(d) / "records/x.json.tmp").write_text("{")
            self.assertEqual(len(storage.records(d, ["x"])), 1)

    def test_resume_changed_identity(self):
        with tempfile.TemporaryDirectory() as d:
            storage.initialize(d, {"a": 1}, False)
            with self.assertRaises(ValueError):
                storage.initialize(d, {"a": 2}, True)

    def test_lock(self):
        with tempfile.TemporaryDirectory() as d:
            with storage.exclusive(d):
                with self.assertRaises(RuntimeError):
                    with storage.exclusive(d):
                        pass

    def test_run_and_resume_and_report(self):
        with tempfile.TemporaryDirectory() as d, patch.object(
            lab, "Ollama", FakeClient
        ):
            a = SimpleNamespace(
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
                out=d,
                resume=False,
            )
            lab.run(a)
            folder = Path(d) / "bm25--direct"
            self.assertEqual(storage.read(folder / "summary.json")["errors"], 0)
            a.resume = True
            lab.run(a)
            self.assertEqual(len(retrieval.jsonl(folder / "predictions.jsonl")), 1)
            lab.report(SimpleNamespace(out=d))
            m = storage.read(folder / "manifest.json")
            m["status"] = "INTERRUPTED"
            retrieval.dump(folder / "manifest.json", m)
            with self.assertRaises(ValueError):
                lab.report(SimpleNamespace(out=d))

    def test_evidence_first(self):
        with tempfile.TemporaryDirectory() as d, patch.object(
            lab, "Ollama", FakeClient
        ):
            a = SimpleNamespace(
                model="mock",
                seed=42,
                num_ctx=32768,
                max_output=1024,
                split="validation",
                limit=1,
                k=4,
                device="cpu",
                strategy="evidence-first",
                extraction_output=2048,
                modes=["bm25"],
                out=d,
                resume=False,
            )
            lab.run(a)
            r = retrieval.jsonl(Path(d) / "bm25--evidence-first/predictions.jsonl")[0]
            self.assertEqual(len(r["calls"]), 3)

    def test_context_budget(self):
        c = object.__new__(Ollama)
        c.a = SimpleNamespace(num_ctx=4096)
        with self.assertRaises(CallError):
            c.budget("x" * 5000, "", 100)

    def test_truncated_call(self):
        c = object.__new__(Ollama)
        c.a = SimpleNamespace(
            num_ctx=8192, max_output=100, url="mock", model="m", seed=42, timeout=1
        )
        with patch.object(
            retrieval,
            "http",
            return_value={
                "done": True,
                "done_reason": "length",
                "message": {"content": "{}"},
            },
        ):
            with self.assertRaises(CallError) as x:
                c.chat("s", "u", {})
            self.assertEqual(x.exception.kind, "OUTPUT_TRUNCATED")

    def test_contradictory_completion_coverage(self):
        r = row()
        r["id"] = "a"
        s = ev.summarize([r], 1, {})
        self.assertIsNone(s["semantic_task_success_all"])

    def test_refusal_grounding_not_vacuous(self):
        j = {
            "claim_checks": [],
            "reference_checks": [{"covered": True}],
            "citation_checks": [],
            "refusal_correct": True,
        }
        self.assertIsNone(ev.semantic(j)["groundedness"])


class ExtraTests(unittest.TestCase):
    def test_downloader_filter_and_lock_scope(self):
        import types, numpy as np

        hf = types.ModuleType("huggingface_hub")
        sf = types.ModuleType("sentence_transformers")
        calls = []
        hf.HfApi = lambda: SimpleNamespace(
            model_info=lambda name: SimpleNamespace(sha="a" * 40)
        )

        def download(name, **kw):
            calls.append(kw)
            return str(kw["local_dir"])

        hf.snapshot_download = download

        class Tok:
            def encode(self, text, **kw):
                return list(range(min(len(text), 20)))

            def decode(self, ids):
                return "passage"

        class Enc:
            def __init__(self, *a, **kw):
                self.tokenizer = Tok()

            def encode(self, texts, **kw):
                return np.ones((len(texts), 3)) / np.sqrt(3)

        sf.SentenceTransformer = Enc
        with tempfile.TemporaryDirectory() as d, patch.object(
            retrieval, "ROOT", Path(d)
        ), patch.dict(
            sys.modules, {"huggingface_hub": hf, "sentence_transformers": sf}
        ):
            with patch.object(
                vector_store, "QdrantIndex", return_value=SimpleNamespace(meta={})
            ):
                one = retrieval.Retriever(dataset.POLICY, "dense")
            lock = storage.read(Path(d) / "models.lock.json")
            lock[retrieval.RERANK] = "b" * 40
            retrieval.dump(Path(d) / "models.lock.json", lock)
            with patch.object(
                vector_store, "QdrantIndex", return_value=SimpleNamespace(meta={})
            ):
                two = retrieval.Retriever(dataset.POLICY, "dense")
            self.assertEqual(one.meta["model_revisions"], two.meta["model_revisions"])
            self.assertIn("local_dir", calls[0])
            self.assertIn("onnx/*", calls[0]["ignore_patterns"])
            self.assertNotIn("*.bin", calls[0]["allow_patterns"])

    def test_explanation_semantic_score(self):
        r = row("explanation", None)
        j = {
            "claim_checks": [{"id": "c1", "correct": True, "grounded": True}],
            "reference_checks": [{"covered": True}],
            "citation_checks": [{"id": "c1:p", "supports": True}],
            "refusal_correct": None,
        }
        result = ev.summarize([r], 1, {"x": {"output": j}})
        self.assertEqual(result["semantic_task_success_all"], 1)

    def test_wrong_action_fails_semantic_task(self):
        r = row()
        r["output"]["action"] = "ALLOW"
        j = {
            "claim_checks": [{"id": "c1", "correct": True, "grounded": True}],
            "reference_checks": [{"covered": True}],
            "citation_checks": [{"id": "c1:p", "supports": True}],
            "refusal_correct": None,
        }
        self.assertEqual(
            ev.summarize([r], 1, {"x": {"output": j}})["semantic_task_success_all"], 0
        )

    def test_judge_calibration_and_resume(self):
        cases = retrieval.jsonl(lab.ROOT / "data/judge_challenges.jsonl")
        truths = {
            json.dumps({key: c["output"][key] for key in ("claims", "refusal_reason")}, sort_keys=True): c
            for c in cases
        }

        class J(FakeClient):
            def __init__(self, a):
                self.a = a
                self.meta = {
                    "digest": "fake-judge",
                    "details": {"family": "gemma3"},
                    "ollama_version": {"version": "mock"},
                }

            def chat(self, system, user, schema, maximum=None):
                d = json.loads(user)
                candidate = d["candidate"]
                case = truths[json.dumps(candidate, sort_keys=True)]
                ok = case.get("expected_correct", True)
                result = {
                    "claim_checks": [
                        {"id": i, "correct": ok, "grounded": ok, "reason": "mock"}
                        for i in d["candidate_claim_ids"]
                    ],
                    "reference_checks": [
                        {"id": v["id"], "covered": ok, "reason": "mock"}
                        for v in d["reference_claims"]
                    ],
                    "citation_checks": [
                        {"id": v["id"], "supports": ok, "reason": "mock"}
                        for v in d["citation_pairs"]
                    ],
                    "refusal_correct": ok if not candidate["claims"] else None,
                }
                for field, expectation in case.get("expected_checks", {}).items():
                    if field == "refusal_correct":
                        result[field] = expectation
                    else:
                        for check in result[field]:
                            check.update(expectation.get(check["id"], {}))
                return result, {"eval_count": 100, "prompt_eval_count": 100}

        with tempfile.TemporaryDirectory() as d, patch.object(lab, "Ollama", J):
            a = SimpleNamespace(
                out=d,
                model="gemma3:27b",
                num_ctx=65536,
                max_output=4096,
                seed=42,
                resume=False,
            )
            lab.calibrate(a)
            self.assertTrue(storage.read(Path(d) / "calibration.json")["passed"])
            a.resume = True
            lab.calibrate(a)
            self.assertEqual(len(retrieval.jsonl(Path(d) / "predictions.jsonl")), len(cases))

    def test_bad_judge_rejected(self):
        class J(FakeClient):
            def __init__(self, a):
                self.a = a
                self.meta = {
                    "digest": "fake-judge",
                    "details": {"family": "gemma3"},
                    "ollama_version": {"version": "mock"},
                }

            def chat(self, system, user, schema, maximum=None):
                d = json.loads(user)
                return {
                    "claim_checks": [
                        {
                            "id": i,
                            "correct": True,
                            "grounded": True,
                            "reason": "always yes",
                        }
                        for i in d["candidate_claim_ids"]
                    ],
                    "reference_checks": [
                        {"id": v["id"], "covered": True, "reason": "always yes"}
                        for v in d["reference_claims"]
                    ],
                    "citation_checks": [
                        {"id": v["id"], "supports": True, "reason": "always yes"}
                        for v in d["citation_pairs"]
                    ],
                    "refusal_correct": True if not d["candidate"]["claims"] else None,
                }, {}

        with tempfile.TemporaryDirectory() as d, patch.object(lab, "Ollama", J):
            with self.assertRaises(ValueError):
                lab.calibrate(
                    SimpleNamespace(
                        out=d,
                        model="gemma",
                        num_ctx=65536,
                        max_output=4096,
                        seed=42,
                        resume=False,
                    )
                )
            self.assertFalse(storage.read(Path(d) / "calibration.json")["passed"])

    def test_runtime_context_preflight(self):
        a = SimpleNamespace(
            url="mock", model="qwen2.5:7b", num_ctx=65536, timeout=10, allow_cpu=True
        )

        def http(url, end, payload=None, *args):
            if end == "/api/tags":
                return {"models": [{"name": a.model, "digest": "d"}]}
            if end == "/api/show":
                return {"model_info": {"qwen2.context_length": 32768}}
            raise AssertionError("Should fail before warmup")

        with patch.object(retrieval, "http", http):
            with self.assertRaises(ValueError):
                Ollama(a)


if __name__ == "__main__":
    unittest.main()
