"""Reproduce the published held-out lexical retrieval result without Ollama."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


class PublishedRetrieval(unittest.TestCase):
    def test_builtin_policy_against_held_out_scenarios(self):
        root = Path(__file__).resolve().parents[1]
        evidence = json.loads((root / "evidence/selection.json").read_text(encoding="utf-8"))
        expected = evidence["policy"]["test"]
        # A custom CLI dataset must not leak into this pinned regression check.
        env = {k: v for k, v in os.environ.items() if k != "ISSUEPILOT_BENCH_CONFIG"}
        with TemporaryDirectory() as folder:
            out = Path(folder) / "retrieval"
            subprocess.run(
                [sys.executable, "bench.py", "retrieval", "--modes", "policy", "--split",
                 "test", "--device", "cpu", "--out", str(out)],
                cwd=root, check=True, env=env, capture_output=True, text=True,
            )
            report = json.loads((out / "policy.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["rows"]), expected["scenarios"])
        answerable = [row for row in report["rows"] if row["gold"]]
        self.assertEqual(len(answerable), expected["answerable"])
        covered = sum(set(row["gold"]) <= set(row["context_ids"]) for row in answerable)
        self.assertEqual(covered, expected["complete_context"])
        for metric in ("recall", "precision", "mrr", "ndcg"):
            self.assertAlmostEqual(report["metrics"]["4"][metric], expected[metric + "_at_4"], places=4)
