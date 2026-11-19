"""Regressions for retaining evidence from rejected RAG responses."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import contracts
import evaluation
import lab
import storage
from test_judge import args
from test_lab import FakeClient, row


class FailureDiagnostics(unittest.TestCase):
    def run_case(self, decision, payload, mode="full"):
        class ResponseClient(FakeClient):
            def chat(self, system, user, schema, maximum=None):
                value = (
                    {"decision": decision}
                    if "decision" in schema.get("properties", {})
                    else copy.deepcopy(payload)
                )
                return value, {
                    "raw_response": json.dumps(value),
                    "prompt_eval_count": 10,
                    "eval_count": 10,
                }

        with tempfile.TemporaryDirectory() as directory:
            settings = args(directory)
            settings.modes = [mode]
            with patch.object(lab, "Ollama", ResponseClient):
                lab.run(settings)
            folder = Path(directory) / (mode + "--direct")
            record = storage.read(folder / "records" / "RAG-001.json")
            return record, storage.read(folder / "summary.json")

    def test_rejected_payload_keeps_citations_without_repair(self):
        for citations, rate in [
            (["IP-INFO-002", "IP-INFO-002"], 1.0),
            (["IP-INFO-002", "NOT-IN-CONTEXT"], 0.5),
        ]:
            with self.subTest(citations=citations):
                payload = {"claims": [{"text": "Evidence.", "citations": citations}]}
                original = copy.deepcopy(payload)
                record, summary = self.run_case("BLOCK", payload)
                self.assertEqual(record["error"]["kind"], "SCHEMA_INVALID")
                self.assertEqual(record["error"]["stage"], "payload")
                self.assertIsNone(record["output"])
                self.assertEqual(record["raw_payload"], original)
                self.assertEqual(record["raw_citations"], citations)
                self.assertEqual(payload, original)
                self.assertEqual(len(record["calls"]), 2)
                self.assertEqual(summary["errors"], 1)
                self.assertEqual(summary["action_accuracy"], 0)
                self.assertEqual(summary["citation_id_validity"]["denominator"], 2)
                self.assertEqual(summary["citation_id_validity"]["rate"], rate)

    def test_malformed_payload_shapes_do_not_break_diagnostics(self):
        malformed = [
            None,
            [],
            "not an object",
            {},
            {"claims": None},
            {"claims": "not a list"},
            {"claims": {"citations": ["p"]}},
            {"claims": [None, 7, "claim", {}, {"citations": "p"}]},
        ]
        for payload in malformed:
            with self.subTest(payload=payload):
                original = copy.deepcopy(payload)
                self.assertEqual(contracts.raw_citations(payload), [])
                self.assertEqual(payload, original)
        payload = {
            "claims": [
                None,
                {"citations": None},
                {"citations": ["p", None, 7, {}, "p", "unknown", ""]},
            ]
        }
        original = copy.deepcopy(payload)
        self.assertEqual(contracts.raw_citations(payload), ["p", "p", "unknown", ""])
        self.assertEqual(payload, original)

    def test_malformed_runner_payload_stays_schema_failure(self):
        payload = {"claims": [None, {"citations": ["IP-INFO-002", 9, "unknown"]}]}
        record, summary = self.run_case("BLOCK", payload)
        self.assertEqual(record["error"]["kind"], "SCHEMA_INVALID")
        self.assertEqual(record["raw_payload"], payload)
        self.assertEqual(record["raw_citations"], ["IP-INFO-002", "unknown"])
        self.assertIsNone(record["output"])
        self.assertEqual(summary["citation_id_validity"]["denominator"], 2)
        self.assertEqual(summary["citation_id_validity"]["rate"], 0.5)

    def test_old_rejected_payload_contributes_all_citation_occurrences(self):
        record = row()
        record.update(
            output=None,
            error={"kind": "SCHEMA_INVALID", "stage": "payload"},
            raw_payload={"claims": [{"text": "Evidence.", "citations": ["p", "p", "bad"]}]},
        )
        original = copy.deepcopy(record)
        summary = evaluation.summarize([record], 1)
        self.assertEqual(summary["citation_id_validity"]["denominator"], 3)
        self.assertAlmostEqual(summary["citation_id_validity"]["rate"], 2 / 3)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(record, original)

    def test_saved_payload_precedes_stale_citation_cache(self):
        record = row()
        record["raw_payload"] = {"claims": [{"citations": ["bad"]}]}
        record["raw_citations"] = ["p"]
        summary = evaluation.summarize([record], 1)
        self.assertEqual(summary["citation_id_validity"]["denominator"], 1)
        self.assertEqual(summary["citation_id_validity"]["rate"], 0.0)

    def test_empty_raw_payload_does_not_borrow_normalized_citations(self):
        for payload in [None, {}, {"claims": []}]:
            with self.subTest(payload=payload):
                record = row()
                record["raw_payload"] = payload
                record["raw_citations"] = ["p"]
                summary = evaluation.summarize([record], 1)
                self.assertEqual(summary["citation_id_validity"]["denominator"], 0)
                self.assertIsNone(summary["citation_id_validity"]["rate"])

    def test_legacy_citation_sources_remain_supported(self):
        for source in ["raw_citations", "raw_output", "output"]:
            with self.subTest(source=source):
                record = row()
                if source == "raw_citations":
                    record[source] = ["p", "missing"]
                    expected = {"denominator": 2, "rate": 0.5}
                elif source == "raw_output":
                    record[source] = {"claims": [{"citations": ["p", "missing"]}]}
                    expected = {"denominator": 2, "rate": 0.5}
                else:
                    expected = {"denominator": 1, "rate": 1.0}
                original = copy.deepcopy(record)
                observed = evaluation.summarize([record], 1)["citation_id_validity"]
                self.assertEqual(observed["denominator"], expected["denominator"])
                self.assertEqual(observed["rate"], expected["rate"])
                self.assertEqual(record, original)

    def test_answer_without_context_remains_visible_failure(self):
        record, summary = self.run_case("BLOCK", {"claims": []}, mode="none")
        self.assertEqual(record["raw_decision"], {"decision": "BLOCK"})
        self.assertEqual(record["error"]["kind"], "ANSWER_WITHOUT_CONTEXT")
        self.assertEqual(record["error"]["stage"], "payload")
        self.assertEqual(record["context_ids"], [])
        self.assertIsNone(record["output"])
        self.assertNotIn("raw_payload", record)
        self.assertEqual(len(record["calls"]), 1)
        self.assertEqual(summary["errors"], 1)

    def test_valid_no_policy_uses_refusal_payload(self):
        payload = {"reason": "No policy was supplied."}
        record, summary = self.run_case("NO_POLICY", payload, mode="none")
        self.assertIsNone(record["error"])
        self.assertEqual(record["raw_decision"], {"decision": "NO_POLICY"})
        self.assertEqual(record["raw_payload"], payload)
        self.assertEqual(record["raw_citations"], [])
        self.assertEqual(len(record["calls"]), 2)
        self.assertEqual(record["output"], {
            "policy_answerable": False,
            "action": None,
            "claims": [],
            "refusal_reason": payload["reason"],
        })
        self.assertEqual(summary["errors"], 0)


if __name__ == "__main__":
    unittest.main()
