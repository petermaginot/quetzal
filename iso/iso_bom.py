# SPDX-License-Identifier: LGPL-3.0-or-later
"""Bill of material and balloons for an isometric (pure Python)."""

import math
from dataclasses import dataclass, field

from pcf import pcf_geom as g

from . import iso_format
from .iso_draw import (TEXT, W_SYMBOL, W_THIN, Circle, Line, Text, add, circle_box, mul,
                       norm, sub, unit)

CATEGORIES = (
    ("PIPE", ("PIPE",)),
    ("FITTINGS", ("ELBOW", "BEND", "TEE", "REDUCER-CONCENTRIC", "REDUCER-ECCENTRIC", "CAP",
                  "OLET", "COUPLING", "UNION")),
    ("FLANGES", ("FLANGE", "FLANGE-BLIND")),
    ("GASKETS", ("GASKET",)),
    ("BOLTS", ("BOLT",)),
    ("VALVES", ("VALVE",)),
    ("SUPPORTS", ("SUPPORT",)),
)
BALLOON_R = 3.0
BALLOON_GAP = 1.0  # minimum clear space between two balloons
DISTANCES = (7.0, 10.0, 13.0, 17.0, 22.0, 28.0)


@dataclass
class BomItem:
    number: int
    category: str
    size: str
    description: str
    quantity: str
    comps: list = field(default_factory=list)  # component indices
    length: float = 0.0  # pipe: total length, mm


def _category(keyword):
    for name, keywords in CATEGORIES:
        if keyword in keywords:
            return name
    return "OTHER"


def _bores(comp):
    pts = list(comp.end_points) + list(comp.branch_points)
    if comp.co_ords is not None:
        pts.append(comp.co_ords)
    out = []
    for p in pts:
        if p.bore > 0 and p.bore not in out:
            out.append(p.bore)
    return sorted(out, reverse=True)


def bom_items(pcf_file, units, size_system):
    groups = {}
    for ci, c in enumerate(pcf_file.components):
        key = c.item_code or (c.keyword, c.skey, tuple(_bores(c)))
        groups.setdefault(key, []).append(ci)
    order = [name for name, _k in CATEGORIES] + ["OTHER"]
    rows = []
    for key, members in groups.items():
        c = pcf_file.components[members[0]]
        size = " x ".join(iso_format.size_text(b, pcf_file.units_bore, size_system) for b in _bores(c))
        description = pcf_file.materials.get(c.item_code, "") or _describe(c)
        category = _category(c.keyword)
        if c.keyword == "PIPE":
            total = sum(g.dist(pcf_file.components[i].end_points[0].xyz(),
                               pcf_file.components[i].end_points[1].xyz())
                        for i in members if len(pcf_file.components[i].end_points) == 2)
            quantity = iso_format.length_text(total, units)
        else:
            total = 0.0
            quantity = str(len(members))
            if c.keyword == "BOLT" and c.attr("BOLT-QUANTITY"):
                description += " (%s PER SET)" % c.attr("BOLT-QUANTITY")
        rows.append(BomItem(0, category, size, description, quantity, members, total))
    rows.sort(key=lambda r: (order.index(r.category), r.comps[0]))
    for i, r in enumerate(rows, 1):
        r.number = i
    return rows


def _describe(comp):
    words = comp.keyword.replace("-", " ")
    if comp.skey:
        words += " (%s)" % comp.skey
    return words


# --------------------------------------------------------------------------
# Balloons
# --------------------------------------------------------------------------


def place_balloons(items, anchors, layout, obstacles):
    """One balloon per drawn component (per straight run, for pipe);
    returns (drawing items, [(number, centre, anchor)])."""
    z = max(layout.zoom, 0.75)
    r = BALLOON_R * z
    graph = layout.graph
    run_of = {eid: i for i, run in enumerate(graph.runs) for eid in run["edges"]}
    placed = []
    pipe_runs = set()
    out = []
    for item in items:
        for ci in item.comps:
            anchor = anchors.get(ci)
            if anchor is None:
                continue
            if item.category == "PIPE":
                runs = {run_of.get(eid) for eid in graph.comp_edges.get(ci, [])}
                if runs and runs <= {k for n, k in pipe_runs if n == item.number}:
                    continue  # this pipe already has a balloon on the same straight
                pipe_runs |= {(item.number, k) for k in runs}
            centre = _best_spot(anchor, layout, obstacles, placed, r)
            placed.append((item.number, centre, anchor))
            edge = sub(anchor, centre)
            start = add(centre, mul(unit(edge), r))
            leader = Line(start, anchor, W_THIN)
            bubble = Circle(centre, r, W_SYMBOL)
            label = Text((centre[0], centre[1] - 0.35 * TEXT * z), str(item.number), TEXT * z)
            dot = Circle(anchor, 0.35 * z, W_THIN, fill=True)
            out.extend([leader, dot, bubble, label])
            obstacles.segments.append((start, anchor))
            obstacles.boxes.append(circle_box(centre, r))
    return out, placed


def _best_spot(anchor, layout, obstacles, placed, r):
    directions = [(math.cos(math.radians(a)), math.sin(math.radians(a)))
                  for a in (90, 270, 30, 150, 210, 330, 0, 180, 60, 120, 240, 300)]
    zoom = max(layout.zoom, 0.75)
    fallback = None
    for d in DISTANCES:
        for u in directions:
            c = add(anchor, mul(u, d * zoom))
            box = circle_box(c, r)
            crowd = any(norm(sub(c, p[1])) < 2 * r + BALLOON_GAP for p in placed)
            start = add(c, mul(unit(sub(anchor, c)), r))
            # The leader may cross its own symbol, which surrounds the anchor.
            tail = add(anchor, mul(unit(sub(c, anchor)), min(4.0 * zoom, 0.5 * d * zoom)))
            bad = 10 * (crowd + obstacles.hits_box(box)) + obstacles.hits_segment(start, tail)
            if bad == 0:
                return c
            if fallback is None or bad < fallback[0]:
                fallback = (bad, c)
    return fallback[1]
