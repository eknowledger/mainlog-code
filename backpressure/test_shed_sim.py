"""Checks on shed_sim.py: that it reproduces the post's numbers, and that the model has the right shape.

    python3 -m unittest test_shed_sim.py
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from shed_sim import Config, Mulberry32, simulate, summarise


class Generator(unittest.TestCase):
    def test_mulberry32_matches_the_javascript_reference(self):
        # The first three draws of mulberry32(1), as JavaScript's (state + 0x6d2b79f5) >>> 0 version prints them.
        r = Mulberry32(1)
        self.assertEqual([round(r.random(), 10) for _ in range(3)], [0.6270739406, 0.0027357212, 0.52744704])


class PostNumbers(unittest.TestCase):
    """The numbers the post quotes, so a change to the model that moves one fails here first."""

    def s(self, **kw):
        return summarise(simulate(replace(Config(), **kw)))

    def test_unbounded_queue(self):
        s = self.s(policy="fifo")
        self.assertEqual((round(s.answered, 1), round(s.timed_out, 1), s.wasted, s.recovered), (8.4, 91.6, 14309, 90.0))

    def test_bounded_queue(self):
        s = self.s(policy="bounded")
        self.assertEqual((round(s.answered, 1), round(s.refused, 1), round(s.timed_out, 1)), (67.1, 32.9, 0.0))

    def test_checkout_first(self):
        plain, first = self.s(policy="bounded"), self.s(policy="priority")
        self.assertEqual(round(first.answered, 1), round(plain.answered, 1))
        self.assertEqual(round(first.checkout_answered, 1), 100.0)

    def test_retries_keep_the_unbounded_queue_down(self):
        for retries in (1, 3):
            r = simulate(replace(Config(), policy="fifo", retries=retries))
            self.assertEqual(r.windows[12:], [0.0] * 18, f"{retries} retries")
            self.assertIsNone(summarise(r).recovered)

    def test_bounded_policies_recover_at_once_with_retries(self):
        for policy in ("bounded", "codel", "lifo", "priority"):
            self.assertEqual(self.s(policy=policy, retries=3).recovered, 0.0, policy)


class Shape(unittest.TestCase):
    def test_every_person_has_exactly_one_outcome(self):
        for policy in ("fifo", "fifo_skip", "bounded", "priority", "codel", "lifo"):
            r = simulate(replace(Config(), policy=policy, retries=3))
            self.assertTrue(all(u.outcome in ("answered", "late", "refused", "timed_out") for u in r.users), policy)

    def test_a_short_bound_never_times_anyone_out(self):
        # 50 at 100/s is 0.5 s of work, under the 2 s timeout.
        for spike in (120.0, 150.0, 300.0):
            self.assertEqual(self.s_(policy="bounded", spike=spike).timed_out, 0.0)

    def test_a_bound_longer_than_the_timeout_does_not_shed(self):
        self.assertGreater(self.s_(policy="bounded", queue_bound=1024).timed_out, 10)

    def test_refusing_costs_answers_when_it_costs_work(self):
        free, cheap, dear = (self.s_(policy="bounded", reject_cost_ms=c).answered for c in (0, 20, 80))
        self.assertGreater(free, cheap)
        self.assertGreater(cheap, dear)

    def s_(self, **kw):
        return summarise(simulate(replace(Config(), **kw)))


if __name__ == "__main__":
    unittest.main()
