"""Size-variation regression: stage sizing must include anchored spine nodes."""
import unittest
from layout import layout


class LayoutTests(unittest.TestCase):
    def test_tall_spine_endpoint_fits_when_final_stage_node_is_short(self):
        spec = dict(stages=[dict(id="start", label="Start"), dict(id="end", label="End")],
                    spines=[dict(id="control", label="Control", fromStage="start", toStage="end")],
                    nodes=[dict(id="a", title="Input", lines=[], parent="start"),
                           dict(id="b", title="Output", lines=[], parent="end"),
                           dict(id="c", title="Release", lines=["Record final state"], parent="control", atStage="end")],
                    edges=[], orders=[])
        data = layout(spec)
        control = data["spines"][0]
        endpoint = next(n for n in data["nodes"] if n["id"] == "c")
        self.assertLessEqual(endpoint["y"] + endpoint["h"] + 16, control["y"] + control["h"])


if __name__ == "__main__":
    unittest.main()
