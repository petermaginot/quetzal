# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for the isometric generator (no FreeCAD needed).

Run from the repository root with:
    python -m unittest discover -s iso/tests -t .
"""

import math
import os
import time
import unittest
import xml.etree.ElementTree as ET

from pcf import pcf_geom, pcf_model
from iso import iso_build, iso_format, iso_graph, iso_layout, iso_sheet, iso_symbols

DATA = os.path.join(os.path.dirname(__file__), "..", "..", "pcf", "tests", "data")


def load(name, index=0):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return pcf_model.to_mm(pcf_model.parse(f.read())[index])


def zigzag_pcf(legs=20):
    """A long line turning through all three axes, with a valve on every leg."""
    text = ["UNITS-BORE MM", "UNITS-CO-ORDS MM", "PIPELINE-REFERENCE ZIGZAG"]
    axes = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (0, -1, 0), (1, 0, 0), (0, 0, -1)]
    p = (0.0, 0.0, 0.0)
    for i in range(legs):
        d = axes[i % len(axes)]
        length = 800 + 150 * (i % 5)
        q = tuple(p[k] + d[k] * length for k in range(3))
        v1 = tuple(p[k] + d[k] * 300 for k in range(3))
        v2 = tuple(p[k] + d[k] * 500 for k in range(3))
        for kw, a, b, skey in (("PIPE", p, v1, ""), ("VALVE", v1, v2, "VBFL"), ("PIPE", v2, q, "")):
            text.append(kw)
            text.append("    END-POINT %.1f %.1f %.1f 100" % a)
            text.append("    END-POINT %.1f %.1f %.1f 100" % b)
            if skey:
                text.append("    SKEY " + skey)
        p = q
    return pcf_model.parse("\n".join(text) + "\n")[0]


class FormatTests(unittest.TestCase):
    def test_ftin(self):
        self.assertEqual(iso_format.ftin(4036.65), "13'-2 15/16\"")
        self.assertEqual(iso_format.ftin(326.8), "1'-0 7/8\"")
        self.assertEqual(iso_format.ftin(300.45), "11 13/16\"")
        self.assertEqual(iso_format.ftin(1219.2), "4'-0\"")
        self.assertEqual(iso_format.ftin(6.35), "1/4\"")

    def test_sizes(self):
        self.assertEqual(iso_format.size_text(50, "MM", "NPS"), '2"')
        self.assertEqual(iso_format.size_text(32, "MM", "NPS"), '1-1/4"')
        self.assertEqual(iso_format.size_text(1.5, "INCH", "DN"), "DN40")
        self.assertEqual(iso_format.size_text(100, "MM", "DN"), "DN100")


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.pf = load("foreign.pcf")
        self.g = iso_graph.build(self.pf)

    def test_topology(self):
        self.assertEqual(len(self.g.nodes), 20)
        self.assertEqual(len(self.g.edges), 18)  # the header pipe is split at the olet
        self.assertEqual(self.g.skipped, [])
        self.assertTrue(all(e.axis in ("X", "Y", "Z") for e in self.g.edges))

    def test_welds_and_work_points(self):
        by_pos = {tuple(round(c, 1) for c in n.pos): n for n in self.g.nodes}
        self.assertEqual(by_pos[(1000.0, 0.0, 0.0)].weld, "BW")  # pipe to elbow
        self.assertEqual(by_pos[(1152.4, 0.0, 1668.1)].weld, "")  # flange face
        self.assertTrue(by_pos[(1152.4, 0.0, 1668.1)].work_point)
        self.assertFalse(by_pos[(1152.4, 0.0, 1671.1)].work_point)  # other gasket face
        self.assertEqual(by_pos[(500.0, 0.0, 0.0)].weld, "BW")  # olet on header

    def test_markers_snap(self):
        kinds = {m.kind: m for m in self.g.markers}
        self.assertGreaterEqual(kinds["bolt"].edge, 0)
        self.assertGreaterEqual(kinds["support"].edge, 0)  # given at the pipe bottom

    def test_connected(self):
        lay = iso_layout.place(iso_layout.Layout(self.g))
        # One piece for the line and one for the stand-alone instrument.
        roots = [n for n, (parent, _e) in lay.parent.items() if parent is None]
        self.assertEqual(len(roots), 2)


ECCENTRIC = """UNITS-BORE MM
UNITS-CO-ORDS MM
PIPELINE-REFERENCE ECC
PIPE
    END-POINT 0 0 25.4 200
    END-POINT 0 1000 25.4 200
REDUCER-ECCENTRIC
    END-POINT 0 1000 25.4 200
    END-POINT 0 1152 0 150
    SKEY REBW
PIPE
    END-POINT 0 1152 0 150
    END-POINT 0 2000 0 150
"""


class EccentricTests(unittest.TestCase):
    def setUp(self):
        self.pf = pcf_model.parse(ECCENTRIC)[0]
        self.g = iso_graph.build(self.pf)

    def test_snapped_and_flat_on_bottom(self):
        red = [e for e in self.g.edges if e.role == "inline"][0]
        self.assertEqual(red.axis, "Y")
        self.assertAlmostEqual(red.length, 152.0)
        # Small end dropped to keep the bottoms level: flat on bottom.
        for got, want in zip(red.flat, (0.0, 0.0, -1.0)):
            self.assertAlmostEqual(got, want)
        self.assertEqual(len(self.g.runs), 1)  # one straight run through the reducer

    def test_dimension_along_the_run(self):
        sheet = iso_build.build_sheet(self.pf)
        self.assertEqual([round(d.value, 1) for d in sheet.dimensions], [2000.0])


class CameraTests(unittest.TestCase):
    def test_freecad_isometric_is_rotation_0(self):
        self.assertEqual(iso_layout.rotation_for_camera((-0.577, 0.577, -0.577), (-0.408, 0.408, 0.816)), 0)

    def test_each_rotation_finds_itself(self):
        for r in range(4):
            turns = (4 - r) % 4
            look = iso_layout.rotate_xy(iso_layout.VIEW_DIR, turns)
            up = iso_layout.rotate_xy(iso_layout.VIEW_UP, turns)
            self.assertEqual(iso_layout.rotation_for_camera(look, up), r)
            # The camera's view direction collapses to a point in that rotation.
            x, y = iso_layout.project(look, r)
            self.assertAlmostEqual(x, 0.0)
            self.assertAlmostEqual(y, 0.0)

    def test_from_below_uses_the_same_heading(self):
        # Looking up from below, heading like rotation 3 from above.
        self.assertEqual(iso_layout.rotation_for_camera((-0.577, -0.577, 0.577), (0.408, 0.408, 0.816)), 3)


class LayoutTests(unittest.TestCase):
    def check(self, pf):
        g = iso_graph.build(pf)
        lay = iso_layout.layout_graph(g, 230, 225)
        self.assertEqual(lay.clashes, [])
        for e in g.edges:  # every edge keeps its iso direction
            p, q = lay.segment(e)
            d = iso_layout.unit2((q[0] - p[0], q[1] - p[1]))
            want = lay.screen_dir(e)
            self.assertAlmostEqual(d[0] * want[0] + d[1] * want[1], 1.0, places=6)
        return lay

    def test_foreign(self):
        self.check(load("foreign.pcf"))

    def test_sample_inches(self):
        self.check(load("sample.pcf"))

    def test_zigzag(self):
        start = time.time()
        self.check(zigzag_pcf(20))
        self.assertLess(time.time() - start, 30.0)


class SheetTests(unittest.TestCase):
    def setUp(self):
        self.pf = load("foreign.pcf")
        self.sheet = iso_build.build_sheet(self.pf, iso_build.Options(sheet=iso_sheet.ISO_A3))

    def test_svg_is_valid(self):
        root = ET.fromstring(self.sheet.svg)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertEqual(root.get("width"), "420mm")

    def test_dimensions_are_true_lengths(self):
        g = self.sheet.layout.graph
        for d in self.sheet.dimensions:
            true = pcf_geom.dist(g.nodes[d.a].pos, g.nodes[d.b].pos)
            self.assertAlmostEqual(d.value, true, places=6)
            self.assertEqual(d.text, "%d" % round(true))
        values = sorted(round(d.value, 1) for d in self.sheet.dimensions)
        # Only spans with pipe: not the valve (219), the olet + elbow (120, 25)
        # or the stand-alone instrument (200).
        self.assertEqual(values, [447.6, 500.0, 563.3, 652.4, 1104.8])

    def test_bom(self):
        rows = {r.description: r for r in self.sheet.bom}
        self.assertEqual(rows["PIPE SMLS SCH 40"].quantity, "1848")
        self.assertEqual(rows["PIPE SMLS SCH 40"].size, "DN100")
        self.assertEqual([r.number for r in self.sheet.bom], list(range(1, len(self.sheet.bom) + 1)))
        categories = [r.category for r in self.sheet.bom]
        self.assertEqual(categories[0], "PIPE")
        self.assertLess(categories.index("FLANGES"), categories.index("VALVES"))

    def test_every_component_has_a_symbol_anchor(self):
        _items, anchors = iso_symbols.draw(self.sheet.layout)
        drawn = set(anchors)
        for ci, c in enumerate(self.pf.components):
            self.assertIn(ci, drawn, c.keyword)

    def test_balloons_on_sheet_and_apart(self):
        x0, y0, x1, y1 = iso_sheet.ISO_A3.iso_area
        pts = [c for _n, c, _a in self.sheet.balloons]
        self.assertEqual(len(pts), len(self.pf.components))
        for x, y in pts:
            self.assertTrue(x0 <= x <= x1 and y0 <= y <= y1, (x, y))
        r = 3.0 * self.sheet.scale
        closest = min(math.dist(a, b) for i, a in enumerate(pts) for b in pts[i + 1:])
        self.assertGreaterEqual(closest, 2 * r)

    def test_feet_inches(self):
        sheet = iso_build.build_sheet(self.pf, iso_build.Options(units="ftin", size_system="NPS"))
        texts = {d.text for d in sheet.dimensions}
        self.assertIn("1'-7 11/16\"", texts)  # 500 mm
        self.assertIn('4"', {r.size for r in sheet.bom})

    def test_rotation_override(self):
        for rot in range(4):
            sheet = iso_build.build_sheet(self.pf, iso_build.Options(rotation=rot))
            self.assertEqual(sheet.layout.rotation, rot)


if __name__ == "__main__":
    unittest.main()
