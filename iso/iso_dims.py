# SPDX-License-Identifier: LGPL-3.0-or-later
"""Isometric dimensions between work points (pure Python).

Along each straight run the work points (elbow/tee/olet centres, flange and
valve faces, open ends) are chained.  Only spans that contain pipe are
dimensioned: fittings, flanges and valves have catalog dimensions, so a span
made only of them needs none.  Each dimension states the true length along
the run; its line is offset along the run's iso "across" axis, on the side
and lane where it collides with the least.
"""

import math
from dataclasses import dataclass

from pcf import pcf_geom as g

from . import iso_format
from .iso_draw import (TEXT, W_THIN, Line, Poly, Text, add, lerp, mul, perp,
                       readable_angle, sub, text_box, unit)

FIRST_LANE = 8.0  # mm from the line work to the first dimension line
LANE_STEP = 6.0
LANES = 4
EXT_GAP = 1.5  # gap between the line work and an extension line
EXT_OVER = 1.5  # extension line beyond the dimension line
ARROW_L, ARROW_W = 2.2, 0.55
MIN_VALUE = 0.5  # mm; shorter distances are not dimensioned


@dataclass
class Dimension:
    a: int  # node ids
    b: int
    value: float  # true distance, mm
    text: str


def place_dimensions(layout, units, obstacles):
    """Add dimension items for every run; returns (items, [Dimension])."""
    graph = layout.graph
    z = layout.zoom
    size = max(2.0, TEXT * z)
    all_items, dims = [], []
    for run in graph.runs:
        pairs = _spans(graph, layout, run)
        if not pairs:
            continue
        edge = graph.edges[run["edges"][0]]
        n = layout.across(edge)
        best = None
        for lane in range(LANES):
            for side in (-1.0, 1.0):
                off = side * (FIRST_LANE + lane * LANE_STEP) * z
                items, texts, lines = _chain(layout, pairs, n, off, size, units)
                hits = sum(obstacles.hits_segment(p, q) for p, q in lines)
                hits += sum(obstacles.hits_box(text_box(t)) for t in texts)
                if best is None or hits < best[0]:
                    best = (hits, items)
                if hits == 0:
                    break
            if best[0] == 0:
                break
        obstacles.add_drawing([it for it in best[1] if not (isinstance(it, Line) and it.dash)])
        all_items.extend(best[1])
        dims.extend(Dimension(a, b, value, iso_format.length_text(value, units))
                    for a, b, value in pairs)
    return all_items, dims


def _spans(graph, layout, run):
    """(a, b, length) for consecutive work points on run with pipe between them."""
    order = {nid: i for i, nid in enumerate(run["nodes"])}
    pipes = [sorted((order[e.a], order[e.b])) for e in (graph.edges[i] for i in run["edges"])
             if e.role == "pipe"]
    wps = [nid for nid in run["nodes"] if graph.nodes[nid].work_point and nid in layout.pos]
    spans = []
    for a, b in zip(wps, wps[1:]):
        lo, hi = order[a], order[b]
        if not any(lo <= p0 and p1 <= hi for p0, p1 in pipes):
            continue  # fittings only: their own dimensions fix this span
        # Measured along the run, so an eccentric reducer's offset does not count.
        value = abs(g.dot(g.sub(graph.nodes[b].pos, graph.nodes[a].pos), run["direction"]))
        if value >= MIN_VALUE:
            spans.append((a, b, value))
    return spans


def _chain(layout, pairs, n, off, size, units):
    z = layout.zoom
    away = unit(mul(n, off))
    items, texts, lines = [], [], []
    extended = set()
    for a, b, value in pairs:
        A, B = layout.pos[a], layout.pos[b]
        A2, B2 = add(A, mul(n, off)), add(B, mul(n, off))
        for P, P2, nid in ((A, A2, a), (B, B2, b)):
            if nid not in extended:
                extended.add(nid)
                items.append(Line(add(P, mul(away, EXT_GAP * z)), add(P2, mul(away, EXT_OVER * z)),
                                  W_THIN, dash=True))
        items.append(Line(A2, B2, W_THIN))
        lines.append((A2, B2))
        u = unit(sub(B2, A2))
        for tip, direction in ((A2, u), (B2, mul(u, -1.0))):
            base = add(tip, mul(direction, ARROW_L * z))
            w = mul(perp(direction), ARROW_W * z)
            items.append(Poly([tip, add(base, w), sub(base, w)], W_THIN, fill=True))
        angle = readable_angle(u)
        text = Text(lerp(A2, B2, 0.5), iso_format.length_text(value, units), size, angle)
        # Put the text on the far side of the dimension line from the pipe.
        up = _text_up(angle)
        if up[0] * away[0] + up[1] * away[1] > 0:
            text.pos = add(text.pos, mul(away, 0.8 * z))
        else:
            text.pos = add(text.pos, mul(away, size + 0.8 * z))
        items.append(text)
        texts.append(text)
    return items, texts, lines


def _text_up(angle):
    r = math.radians(angle)
    return (-math.sin(r), math.cos(r))
