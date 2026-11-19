"""Regression checks for policy retrieval and context assembly."""

import unittest

import dataset
from retrieval import Retriever


class PolicyRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.retriever = Retriever(dataset.POLICY, "policy")

    def test_query_alone_selects_rule_and_expands_dependencies(self):
        query = "May an issue close if the day-5 reminder was never delivered?"
        context, ranked = dataset.assemble(self.retriever, {"query": query}, "policy", 2)

        self.assertEqual(set(ranked[:2]), {"IP-INFO-003", "IP-CLOSE-002"})
        self.assertEqual(
            set(context), {"IP-CLOSE-002", "IP-CLOSE-001", "IP-INFO-003", "IP-INFO-004"}
        )

    def test_explicit_policy_section_search(self):
        results = self.retriever.search("Locate IP-VERIFY-003.", 4)
        self.assertEqual(results[0]["id"], "IP-VERIFY-003")

    def test_weak_matches_stay_visible_in_rank_but_are_not_context(self):
        query = "Where can I look up the holiday shipping rate?"
        context, ranked = dataset.assemble(self.retriever, {"query": query}, "policy", 4)

        self.assertEqual(context, {})
        self.assertEqual(len(ranked), 8)
        self.assertEqual(len(set(ranked)), 8)
