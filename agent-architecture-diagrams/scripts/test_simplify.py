"""Behavioral regressions for straight-route preferences and aligned ports."""
import unittest
from build import score
from ports import packed_positions, straight_plan


class SimplificationTests(unittest.TestCase):
    def report(self, **changes):
        report = dict(violations=[], crossingCount=0, straightEdges=2, totalBends=8, maxBends=4)
        report.update(changes)
        return report

    def test_rewards_fewer_total_bends_even_if_maximum_is_unchanged(self):
        data = dict(edges=[], H=500)
        self.assertLess(score(self.report(totalBends=6), data), score(self.report(), data))

    def test_prefers_more_straight_edges_before_bend_tiebreak(self):
        data = dict(edges=[], H=500)
        self.assertLess(score(self.report(straightEdges=3), data), score(self.report(totalBends=6), data))

    def test_never_trades_a_crossing_for_more_straight_edges(self):
        data = dict(edges=[], H=500)
        self.assertLess(score(self.report(), data, 0),
                        score(self.report(crossingCount=1, straightEdges=8, totalBends=0), data, 0))

    def test_facing_ports_align_unequal_boxes_without_diagonals(self):
        a = dict(x=0, y=0, w=100, h=40)
        b = dict(x=30, y=100, w=100, h=60)
        sides, points = straight_plan(a, b, "vertical")
        self.assertEqual(("S", "N"), sides)
        self.assertEqual([(65, 40), (65, 100)], points)
        sides, points = straight_plan(b, a, "vertical")
        self.assertEqual(("N", "S"), sides)
        self.assertEqual([(65, 100), (65, 40)], points)

    def test_diagonally_separated_boxes_have_no_straight_connection(self):
        a = dict(x=0, y=0, w=100, h=40)
        b = dict(x=130, y=100, w=100, h=60)
        self.assertIsNone(straight_plan(a, b, "vertical"))
        self.assertIsNone(straight_plan(a, b, "horizontal"))

    def test_aligned_anchor_keeps_neighbor_on_its_ordered_side(self):
        positions = packed_positions(172.5, [None, 41], [True, True])
        self.assertEqual(41, positions[1])
        self.assertLessEqual(positions[0], 17)

    def test_optional_alternative_does_not_displace_an_aligned_port(self):
        positions = packed_positions(150, [138, None], [True, False])
        self.assertEqual({0: 138}, positions)

    def test_required_ports_are_not_silently_dropped(self):
        with self.assertRaises(ValueError):
            packed_positions(150, [138, None], [True, True])


if __name__ == "__main__":
    unittest.main()
