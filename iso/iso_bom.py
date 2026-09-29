# SPDX-License-Identifier: LGPL-3.0-or-later
"""Bill of material and balloons for an isometric (pure Python)."""

import math
import re
from dataclasses import dataclass, field

from pcf import pcf_fittings
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
    key: str = ""  # stable identity across regenerations (item code or type + sizes)


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
    """BOM rows: components grouped by item code; elbows also by angle and
    radius (from their PCF points), so a 90 LR and a 45 LR, or an LR elbow and
    a 6D bend, never share a part number even when their item codes do."""
    groups = {}
    designations = {}
    for ci, c in enumerate(pcf_file.components):
        key = c.item_code or "%s %s %s" % (c.keyword, c.skey, "x".join("%g" % b for b in _bores(c)))
        des = elbow_designation(c, pcf_file.units_bore)
        if des is not None:
            designations[ci] = des
            key += " | " + des.code
        groups.setdefault(key, []).append(ci)
    order = [name for name, _k in CATEGORIES] + ["OTHER"]
    rows = []
    for key, members in groups.items():
        c = pcf_file.components[members[0]]
        size = " x ".join(iso_format.size_text(b, pcf_file.units_bore, size_system) for b in _bores(c))
        description = pcf_file.materials.get(c.item_code, "") or _describe(c)
        if members[0] in designations:
            description = _elbow_description(designations[members[0]], description, units)
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
        rows.append(BomItem(0, category, size, description, quantity, members, total, key))
    rows.sort(key=lambda r: (order.index(r.category), r.comps[0]))
    for i, r in enumerate(rows, 1):
        r.number = i
    return rows


def elbow_designation(comp, units_bore):
    """Angle and radius class of an ELBOW/BEND from its PCF points, or None."""
    if comp.keyword not in ("ELBOW", "BEND") or comp.centre_point is None or len(comp.end_points) < 2:
        return None
    c = comp.centre_point.xyz()
    v1 = g.sub(comp.end_points[0].xyz(), c)
    v2 = g.sub(comp.end_points[1].xyz(), c)
    if g.length(v1) < 1e-6 or g.length(v2) < 1e-6:
        return None
    between = math.degrees(math.acos(max(-1.0, min(1.0, g.dot(g.unit(v1), g.unit(v2))))))
    angle = 180.0 - between
    tangent = (g.length(v1) + g.length(v2)) / 2.0
    radius = tangent / math.tan(math.radians(angle) / 2.0) if angle > 1e-6 else 0.0
    bore = comp.end_points[0].bore
    inch = units_bore.upper().startswith("IN")
    if inch:
        dn = min(pcf_fittings.DN_INCH, key=lambda d: abs(pcf_fittings.DN_INCH[d] - bore))
    else:
        dn = pcf_fittings.nearest_dn(bore)
    des = pcf_fittings.elbow_designation(angle, radius, dn)
    if comp.skey[2:4] in ("SW", "SC"):
        des.radius_class = "SOCKET"  # socket/screwed elbows: angle only
    return des


_LEADING = (r"^(ELBOW|BEND)\b[\s,]*", r"^\d+(\.\d+)?\s*(DEG|°)[\s,]*",
            r"^(LR|SR|\d+(\.\d+)?D|R\d+)\b[\s,]*")


def _elbow_description(des, description, units):
    """'ELBOW 90° LR, <rest>' or 'BEND 45° 6D, R 2'-3", <rest>'."""
    rest = description.upper()
    for pattern in _LEADING:
        rest = re.sub(pattern, "", rest)
    rest = re.sub(r"^\(\w+\)$", "", rest)  # a bare SKEY left from a fallback description
    if des.radius_class == "SOCKET":
        head = "ELBOW %s°" % des.angle_text
    elif des.is_bend:
        head = "BEND %s° %sR %s" % (des.angle_text, des.radius_class + ", " if des.radius_class else "",
                                         iso_format.length_text(des.radius, units))
    else:
        head = "ELBOW %s° %s" % (des.angle_text, des.radius_class)
    return head + (", " + rest if rest else "")


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
