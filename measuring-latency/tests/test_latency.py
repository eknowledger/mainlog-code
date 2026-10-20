"""The claims the post makes about the statistics, checked on the seeded model rather than read off its output."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from latency_questions import THRESHOLD_MS, Model, draw, mean, mulberry32, percentile  # noqa: E402


class Generator(unittest.TestCase):
    def test_matches_the_javascript_reference(self):
        # The first three values of mulberry32(7), from the reference implementation run in Node. The site's lab
        # draws from the same generator, so if this drifts the lab and the post disagree.
        r = mulberry32(7)
        self.assertEqual([r(), r(), r()], [0.011704753153026104, 0.06195825757458806, 0.97690763277933])


class Percentile(unittest.TestCase):
    def test_nearest_rank_returns_a_measurement(self):
        values = [5.0, 1.0, 4.0, 2.0, 3.0]
        self.assertEqual(percentile(values, 50), 3.0)
        self.assertEqual(percentile(values, 99), 5.0)
        self.assertEqual(percentile(values, 1), 1.0)
        self.assertIn(percentile([0.3, 7.1, 2.2], 90), [0.3, 7.1, 2.2])


class RollUps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.equal = draw(Model(busy_share=0.25))
        cls.busy = draw(Model(busy_share=0.70))

    def test_shares_add_up_exactly(self):
        for hosts in (self.equal, self.busy):
            pooled = [x for h in hosts for x in h]
            counts = sum(sum(x < THRESHOLD_MS for x in h) for h in hosts)
            self.assertEqual(counts, sum(x < THRESHOLD_MS for x in pooled))

    def test_means_add_up_only_when_weighted(self):
        pooled = [x for h in self.busy for x in h]
        weighted = sum(mean(h) * len(h) for h in self.busy) / len(pooled)
        self.assertAlmostEqual(weighted, mean(pooled), places=9)
        self.assertLess(mean([mean(h) for h in self.busy]), mean(pooled) - 3)

    def test_averaged_p99_is_wrong_in_both_directions(self):
        def ratio(hosts):
            pooled = [x for h in hosts for x in h]
            return mean([percentile(h, 99) for h in hosts]) / percentile(pooled, 99)

        self.assertGreater(ratio(self.equal), 2.5)  # equal traffic: three times too high
        self.assertLess(ratio(self.busy), 0.5)  # most traffic on the stalling host: well under half


if __name__ == "__main__":
    unittest.main()
