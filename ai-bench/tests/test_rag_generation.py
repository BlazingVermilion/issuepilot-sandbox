"""Regressions for the pilot generation path and retained raw responses."""

import unittest
from unittest.mock import Mock

import jsonschema

import dataset
import lab
import rag_generation
import rag_refined
import retrieval


class PilotGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus = retrieval.sections(dataset.POLICY)[1]
        cls.docs = {doc["id"]: doc["text"] for doc in corpus}

    def test_timing_veto_uses_policy_and_no_model_call(self):
        case = {
            "query": "An information request was published at hour 10 and the reminder "
                     "was successfully delivered at hour 155. At hour 180, may it close?",
            "task": "action",
        }
        client = Mock()
        record = {"calls": []}
        lab.generate_pilot(client, case, self.docs, record)

        client.chat.assert_not_called()
        self.assertEqual(record["computed_timing"]["deadline_hour"], 203)
        self.assertEqual(record["output"]["action"], "BLOCK")
        self.assertIn("hour 203", record["output"]["claims"][0]["text"])
        self.assertTrue(record["first_attempt_valid"])
        self.assertNotIn("raw_decision", record)

    def test_passing_timer_does_not_override_other_gate(self):
        query = (
            "An information request was published at hour 10 and the reminder was "
            "successfully delivered at hour 155. At hour 203, may it close while paused?"
        )
        wire = {"decision": "BLOCK", "claims": [
            {"text": "A pause blocks closure.", "citations": ["IP-CLOSE-001"]},
        ]}
        client = Mock()
        client.chat.return_value = (wire, {})
        record = {"calls": []}
        lab.generate_pilot(client, {"query": query, "task": "action"}, self.docs, record)

        client.chat.assert_called_once()
        self.assertIn("Hour 203 is at or after", client.chat.call_args.args[1])
        self.assertEqual(record["output"]["action"], "BLOCK")
        self.assertTrue(record["first_attempt_valid"])

    def test_unknown_reminder_and_missing_policy_do_not_trigger_timer(self):
        query = ("An information request was sent at hour 10. At hour 180, "
                 "may we close if reminder delivery is unknown?")
        self.assertIsNone(rag_generation.timing_check(query, self.docs))
        self.assertIsNone(rag_generation.timing_check(query, {}))

    def test_duplicate_citations_are_removed_and_first_attempt_is_visible(self):
        wire = {"decision": "NEED_FACTS", "claims": [
            {"text": "Check reminder delivery.", "citations": ["IP-INFO-003", "IP-INFO-003"]},
        ]}
        client = Mock()
        client.chat.return_value = (wire, {})
        record = {"calls": []}
        lab.generate_pilot(client, {"query": "Is delivery established?", "task": "action"},
                           self.docs, record)

        self.assertEqual(record["output"]["action"], "NEED_FACTS")
        self.assertEqual(record["output"]["claims"][0]["citations"], ["IP-INFO-003"])
        self.assertEqual(record["raw_citations"], ["IP-INFO-003", "IP-INFO-003"])
        self.assertEqual(record["normalizations"], ["duplicate_citations"])
        self.assertFalse(record["first_attempt_valid"])

    def test_invalid_citations_and_inconsistent_refusals_stay_errors(self):
        for decision, citation in [("BLOCK", "unknown"), ("NO_POLICY", "IP-INFO-003")]:
            with self.subTest(decision=decision):
                wire = {"decision": decision, "claims": [{"text": "Claim", "citations": [citation]}]}
                with self.assertRaises(jsonschema.ValidationError):
                    rag_generation.normalize_wire(wire, self.docs, "action")

    def test_no_context_refuses_without_model(self):
        client = Mock()
        record = {"calls": []}
        lab.generate_pilot(client, {"query": "May it close?", "task": "action"}, {}, record)
        client.chat.assert_not_called()
        self.assertFalse(record["output"]["policy_answerable"])
        self.assertIsNone(record["output"]["action"])
        self.assertTrue(record["first_attempt_valid"])

    def test_refined_unknown_note_and_citation_limit(self):
        docs = {section: self.docs[section] for section in ("IP-VERIFY-001", "IP-VERIFY-002")}
        query = "The record does not establish whether the package opens."
        self.assertIn("DECISION CHECK", rag_refined.prompt(query, "action", docs, None))
        self.assertNotIn("DECISION CHECK", rag_refined.prompt(query, "explanation", docs, None))
        self.assertNotIn("DECISION CHECK", rag_refined.prompt("The package failed to open.", "action", docs, None))
        wire = {"decision": "NEED_FACTS", "claims": [
            {"text": "Verify the package is accessible.", "citations": ["IP-VERIFY-001"] * 2},
        ]}
        self.assertFalse(jsonschema.Draft202012Validator(rag_refined.wire_schema(docs, "action")).is_valid(wire))

    def test_refined_passing_timing_is_included_in_the_answer(self):
        query = (
            "An information request was published at hour 10 and the reminder was "
            "successfully delivered at hour 155. At hour 203, may it close while paused?"
        )
        client = Mock()
        client.chat.return_value = ({"decision": "BLOCK", "claims": [
            {"text": "A pause blocks closure.", "citations": ["IP-CLOSE-001"]},
        ]}, {})
        record = {"calls": []}
        lab.generate_pilot(client, {"query": query, "task": "action"},
                           self.docs, record, "pilot-refined")
        self.assertEqual(record["output"]["action"], "BLOCK")
        self.assertIn("effective information deadline is hour 203",
                      record["output"]["claims"][0]["text"])
        self.assertEqual(len(record["output"]["claims"]), 2)
