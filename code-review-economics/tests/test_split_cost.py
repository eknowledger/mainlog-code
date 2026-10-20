"""The fit and the split rule, checked on data whose answer is known, and the filter on hand-made PRs."""

import json
import math
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from split_cost import fit, load, slowest_of  # noqa: E402


def pr(author="alice", typename="User", additions=10, deletions=0, reviews=(), ready=None):
    return {
        "createdAt": "2026-10-01T00:00:00Z", "mergedAt": "2026-10-02T00:00:00Z",
        "additions": additions, "deletions": deletions,
        "author": {"login": author, "__typename": typename},
        "timelineItems": {"nodes": [{"createdAt": ready}] if ready else []},
        "reviewThreads": {"totalCount": 0},
        "reviews": {"nodes": [{"submittedAt": t, "author": {"login": who, "__typename": kind}} for who, kind, t in reviews]},
    }


class Filter(unittest.TestCase):
    def load(self, prs):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(p) for p in prs))
        try:
            return load(Path(f.name))
        finally:
            os.unlink(f.name)

    def test_bots_and_self_reviews_are_left_out(self):
        rows = self.load([
            pr(author="renovate[bot]", typename="Bot", reviews=[("bob", "User", "2026-10-01T01:00:00Z")]),
            pr(reviews=[("alice", "User", "2026-10-01T01:00:00Z")]),  # only the author reviewed
            pr(reviews=[("ci-bot", "User", "2026-10-01T01:00:00Z")]),  # a bot account that GitHub calls a user
            pr(reviews=[("zaniebot", "User", "2026-10-01T01:00:00Z")]),
            pr(reviews=[]),
        ])
        self.assertEqual(rows, [])

    def test_a_person_whose_name_contains_bot_is_kept(self):
        rows = self.load([pr(author="subotac", reviews=[("bob", "User", "2026-10-01T01:00:00Z")])])
        self.assertEqual(len(rows), 1)

    def test_wait_runs_from_ready_for_review_to_the_first_human_review(self):
        rows = self.load([pr(ready="2026-10-01T10:00:00Z", reviews=[
            ("carol", "User", "2026-10-01T16:00:00Z"), ("bob", "User", "2026-10-01T13:00:00Z")])])
        self.assertEqual(rows[0]["wait"], 3.0)

    def test_a_review_before_ready_is_floored_not_negative(self):
        rows = self.load([pr(ready="2026-10-01T10:00:00Z", reviews=[("bob", "User", "2026-10-01T09:00:00Z")])])
        self.assertEqual(rows[0]["wait"], 0.05)


class Fit(unittest.TestCase):
    def test_recovers_a_known_elasticity_through_noise(self):
        rng = random.Random(5)
        for e in (0.2, 0.5):
            pairs = []
            for _ in range(5000):
                size = math.exp(rng.uniform(0, 8))
                pairs.append((size, 2 * size ** e * math.exp(rng.gauss(0, 1.5))))
            got, a, r = fit(pairs)
            self.assertAlmostEqual(got, e, delta=0.03)

    def test_serial_rule_matches_summing_the_pieces(self):
        # Under the fit, k pieces of size S/k each expect c(S/k)^e, so k of them expect k^(1-e) times one PR of S.
        e, c, size = 0.3, 2.0, 800
        for k in (2, 4, 8):
            self.assertAlmostEqual(k * c * (size / k) ** e / (c * size ** e), k ** (1 - e), places=12)

    def test_slowest_of_one_is_one_and_grows_with_k(self):
        rng = random.Random(3)
        residuals = [math.exp(rng.gauss(0, 1)) for _ in range(2000)]
        self.assertAlmostEqual(slowest_of(1, residuals), 1.0, places=12)
        self.assertLess(slowest_of(2, residuals), slowest_of(8, residuals))

    def test_slowest_of_is_the_exact_expected_maximum(self):
        # Two values, 1 and 3, mean 2. The larger of two draws is 3 unless both are 1: E = 3 - 2 x 1/4 = 2.5.
        self.assertAlmostEqual(slowest_of(2, [1.0, 3.0]), 2.5 / 2, places=12)
        # And against a direct simulation.
        rng = random.Random(4)
        values = [rng.expovariate(1) for _ in range(500)]
        sim = sum(max(rng.choice(values) for _ in range(5)) for _ in range(100000)) / 100000 / (sum(values) / 500)
        self.assertAlmostEqual(slowest_of(5, values), sim, delta=0.02)


if __name__ == "__main__":
    unittest.main()
