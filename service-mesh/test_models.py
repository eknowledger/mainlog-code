"""Checks on the two arithmetic scripts: the post's numbers, and the rules they rest on.

    python3 -m unittest test_models.py
"""

from __future__ import annotations

import math
import unittest

from retry_fanout import expected, worst
from timeout_budget import FIXED, SHOP, fits, parse, top_fit, worst_cases


class Fanout(unittest.TestCase):
    def test_post_numbers(self):
        self.assertEqual([worst(3, m, a, 0) for m, a in ((0, 0), (1, 0), (2, 0), (2, 2))], [1, 8, 27, 729])
        self.assertEqual(worst(3, 2, 0, 2), 81)
        self.assertAlmostEqual(worst(3, 2, 0, 0, 0.2), 1.728)

    def test_layers_multiply_not_add(self):
        self.assertEqual(worst(1, 2, 2, 0), 9)

    def test_average_is_one_with_no_failure_and_worst_with_total_failure(self):
        self.assertEqual(expected(3, 2, 1, 1, 0.0)[0], 1)
        self.assertEqual(expected(3, 2, 1, 0, 1.0)[0], worst(3, 2, 1, 0))


class Timeouts(unittest.TestCase):
    def test_shop_chain(self):
        calls = parse(SHOP)
        w = worst_cases(calls, 0.025)
        self.assertEqual([round(x, 3) for x in w], [9.2, 3.05, 1.55])
        self.assertEqual(fits(calls, w, 0.025), [3, 1, None])
        # The app gives the whole chain 2 s; one try of the top call can take 3.05 s, so none of its tries finishes.
        self.assertEqual(top_fit(calls, w, 0.025, 2.0), 0)

    def test_fixed_chain_fits_two_seconds(self):
        calls = parse(FIXED)
        w = worst_cases(calls, 0.025)
        self.assertLessEqual(w[0], 2.0)
        self.assertEqual(fits(calls, w, 0.025), [2, 3, None])
        self.assertEqual(top_fit(calls, w, 0.025, 2.0), 1)
        # Envoy jitters its backoff: up to 24 ms before the first retry and 74 ms before the second. Still fits.
        self.assertLess(3 * 0.225 + 0.024 + 0.074, 0.8)

    def test_no_timeout_anywhere_is_unbounded(self):
        self.assertTrue(math.isinf(worst_cases(parse("a -> b none 1\nb -> c none 1"), 0.025)[0]))


if __name__ == "__main__":
    unittest.main()
