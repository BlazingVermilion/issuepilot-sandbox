import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jsonschema
import protocol_probe
import storage
from client import CallError


class ExactClient:
    def __init__(self, args):
        self.meta = {"digest": "probe-mock"}

    def chat(self, system, user, schema):
        return json.loads(user), {"raw_response": user, "eval_count": 7}


class ProbeTests(unittest.TestCase):
    def invoke(self, output, extra=()):
        with contextlib.redirect_stdout(io.StringIO()):
            protocol_probe.main(["--out", str(output), *extra])

    def test_default_positive_checks_pass_and_are_checkpointed(self):
        snapshots = []
        original_dump = protocol_probe.retrieval.dump

        def capture(path, result):
            snapshots.append(copy.deepcopy(result))
            original_dump(path, result)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(protocol_probe, "Ollama", ExactClient), \
                patch.object(protocol_probe.retrieval, "dump", capture):
            output = Path(directory) / "probe.json"
            self.invoke(output)
            result = storage.read(output)
            self.assertTrue(result["passed"])
            self.assertEqual(len(result["checks"]), 11)
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(snapshots[0]["status"], "INITIALIZING")
            for index, check in enumerate(result["checks"]):
                self.assertEqual(check["response"], check["target"])
                self.assertTrue(any(len(snapshot["checks"]) == index + 1
                                    and snapshot["checks"][-1]["status"] == "RUNNING"
                                    for snapshot in snapshots))
                self.assertTrue(any(len(snapshot["checks"]) == index + 1
                                    and snapshot["checks"][-1]["passed"]
                                    for snapshot in snapshots))
            self.assertFalse(output.with_suffix(".json.tmp").exists())

    def test_startup_failure_is_durable(self):
        error = CallError("MODEL_STARTUP_HTTP", "Cannot load model", {"raw_response": "startup"})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(protocol_probe, "Ollama", side_effect=error):
            output = Path(directory) / "probe.json"
            with self.assertRaises(CallError) as raised:
                self.invoke(output, ["--num-ctx", "24576"])
            self.assertIs(raised.exception, error)
            result = storage.read(output)
            self.assertFalse(result["passed"])
            self.assertIsNone(result["model"])
            self.assertEqual(result["checks"], [])
            self.assertEqual(result["requested"]["num_ctx"], 24576)
            self.assertEqual(result["error"]["stage"], "startup")
            self.assertEqual(result["error"]["kind"], "MODEL_STARTUP_HTTP")
            self.assertEqual(result["error"]["meta"], error.meta)

    def test_failed_chat_preserves_target_and_raw_response(self):
        metadata = {"raw_response": '{"category":', "eval_count": 512, "done_reason": "length"}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(protocol_probe, "Ollama", ExactClient), \
                patch.object(ExactClient, "chat", side_effect=CallError("OUTPUT_TRUNCATED", meta=metadata)):
            output = Path(directory) / "probe.json"
            with self.assertRaises(CallError):
                self.invoke(output)
            result = storage.read(output)
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(result["error"]["stage"], "chat")
            self.assertEqual(result["checks"][0]["target"], {"category": "bug"})
            self.assertEqual(result["checks"][0]["meta"], metadata)
            self.assertEqual(result["checks"][0]["error"]["kind"], "OUTPUT_TRUNCATED")

    def test_valid_but_mismatching_response_fails(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(protocol_probe, "Ollama", ExactClient), \
                patch.object(ExactClient, "chat", return_value=({"category": "feature-request"}, {})):
            output = Path(directory) / "probe.json"
            with self.assertRaisesRegex(ValueError, "mismatch"):
                self.invoke(output)
            result = storage.read(output)
            self.assertFalse(result["passed"])
            self.assertEqual(result["error"]["stage"], "validation")
            self.assertEqual(result["checks"][0]["response"], {"category": "feature-request"})

    def test_interrupt_keeps_prior_success_and_pending_check(self):
        call_count = 0

        def interrupt(client, system, user, schema):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise KeyboardInterrupt()
            return json.loads(user), {}

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(protocol_probe, "Ollama", ExactClient), \
                patch.object(ExactClient, "chat", interrupt):
            output = Path(directory) / "probe.json"
            with self.assertRaises(KeyboardInterrupt):
                self.invoke(output)
            result = storage.read(output)
            self.assertEqual(result["status"], "INTERRUPTED")
            self.assertEqual(result["error"]["type"], "KeyboardInterrupt")
            self.assertTrue(result["checks"][0]["passed"])
            self.assertFalse(result["checks"][1]["passed"])
            self.assertEqual(result["checks"][1]["status"], "INTERRUPTED")

    def test_duplicate_challenge_fails_when_duplicates_are_returned(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(protocol_probe, "Ollama", ExactClient):
            output = Path(directory) / "probe.json"
            with self.assertRaises(jsonschema.ValidationError):
                self.invoke(output, ["--constraint-challenges"])
            result = storage.read(output)
            self.assertFalse(result["passed"])
            self.assertEqual(result["error"]["kind"], "SCHEMA_INVALID")
            self.assertTrue(all(check["passed"] for check in result["checks"][:-1]))
            check = result["checks"][-1]
            citation_schema = check["schema"]["properties"]["claims"]["items"]["properties"]["citations"]
            self.assertEqual(citation_schema["items"]["enum"], ["TEST-1", "TEST-2"])
            self.assertEqual(check["response"]["claims"][0]["citations"], ["TEST-1", "TEST-1"])

    def test_duplicate_challenge_accepts_only_same_claim_and_unique_sources(self):
        name, schema, target, expectation = protocol_probe.checks(True)[-1]
        valid = {"claims": [{"text": "A test claim.", "citations": ["TEST-1"]}]}
        protocol_probe.validate_response(valid, schema, target, expectation, name)
        for change in [
            {"text": "Another claim.", "citations": ["TEST-1"]},
            {"text": "A test claim.", "citations": ["TEST-1", "TEST-2"]},
        ]:
            with self.assertRaisesRegex(ValueError, "mismatch"):
                protocol_probe.validate_response({"claims": [change]}, schema, target, expectation, name)

    def test_invalid_arguments_fail_before_initializing_or_overwriting(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(protocol_probe, "Ollama") as client:
            output = Path(directory) / "probe.json"
            output.write_text("previous result", encoding="utf-8")
            for flag, value in [("--num-ctx", "4095"), ("--max-output", "0"), ("--timeout", "-1")]:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.invoke(output, [flag, value])
                self.assertEqual(output.read_text(encoding="utf-8"), "previous result")
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
