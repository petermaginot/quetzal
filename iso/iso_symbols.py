# SPDX-License-Identifier: LGPL-3.0-or-later
"""Isometric symbols for a laid-out IsoGraph (pure Python).

draw(layout) returns (items, anchors): drawing primitives in paper mm (Y up)
and, per component index, the paper point a balloon should point at.
"""

import math

from pcf import pcf_geom as g

from . import iso_layout
from .iso_draw import (W_PIPE, W_SYMBOL, W_THIN, Circle, Line, Poly, Text, add, lerp,
                       mul, sub, unit)

H = 3.0  # half-height of flanges, valves and reducers
WELD_R = 0.8

VALVE_MARKS = {"VB": "ball", "VG": "globe", "VC": "check", "VP": "plug", "VY": "butterfly",
               "VT": "gate"}


def draw(layout):
    graph = layout.graph
    comps = graph.pcf.components
    z = layout.zoom
    items = []
    anchors = {}

    def pos(nid):
        return layout.pos[nid]

    for e in graph.edges:
        comp = comps[e.comp]
        P, Q = pos(e.a), pos(e.b)
        n = layout.across(e)
        mid = lerp(P, Q, 0.5)
        kw = comp.keyword
        if e.role in ("pipe", "leg", "branch"):
            items.append(Line(P, Q, W_PIPE))
            if e.role == "pipe":
                anchors.setdefault(e.comp, mid)
            else:
                anchors.setdefault(e.comp, P if e.role == "branch" else _centre(graph, layout, e))
        elif e.role == "olet":
            items.append(Poly([add(P, mul(n, 2.2 * z)), add(Q, mul(n, 1.4 * z)),
                               add(Q, mul(n, -1.4 * z)), add(P, mul(n, -2.2 * z))], W_SYMBOL))
            anchors[e.comp] = mid
        else:
            items.extend(_inline(layout, e, comp, P, Q, n, z))
            anchors[e.comp] = mid
        if not e.axis:
            items.extend(_skew_triangle(layout, e, P, Q, z))

    for m in graph.markers:
        comp = comps[m.comp]
        if m.kind == "cap" and m.node >= 0:
            items.extend(_cap(layout, m.node, z))
            anchors[m.comp] = pos(m.node)
        elif m.edge >= 0:
            e = graph.edges[m.edge]
            at = lerp(pos(e.a), pos(e.b), m.t)
            if m.kind == "support":
                items.extend(_support(layout, e, at, comp, z))
            anchors[m.comp] = at

    for node in graph.nodes:
        if node.id not in layout.pos:
            continue
        if node.weld in ("BW", "SW"):
            items.append(Circle(pos(node.id), WELD_R * z, W_THIN, fill=True))
        elif node.weld == "SC":
            e = graph.edges_at(node.id)[0]
            n = layout.across(e)
            d = layout.screen_dir(e)
            for s in (-0.6, 0.6):
                c = add(pos(node.id), mul(d, s * z))
                items.append(Line(add(c, mul(n, 1.3 * z)), add(c, mul(n, -1.3 * z)), W_THIN))
    return items, anchors


def _centre(graph, layout, edge):
    """Paper position of the centre node of an elbow/tee leg."""
    for nid in (edge.a, edge.b):
        if graph.nodes[nid].centre:
            return layout.pos[nid]
    return lerp(layout.pos[edge.a], layout.pos[edge.b], 0.5)


def _face_node(graph, edge):
    """(face node, other node) of a flange edge."""
    for nid in (edge.a, edge.b):
        if (edge.comp, "FACE") in graph.nodes[nid].ends:
            return nid, (edge.b if nid == edge.a else edge.a)
    return edge.a, edge.b


def _across_line(c, n, h, width=W_SYMBOL):
    return Line(add(c, mul(n, h)), add(c, mul(n, -h)), width)


def _inline(layout, e, comp, P, Q, n, z):
    graph = layout.graph
    kw = comp.keyword
    h = H * z
    out = []
    if kw in ("FLANGE", "FLANGE-BLIND"):
        fnode, wnode = _face_node(graph, e)
        F, Wd = layout.pos[fnode], layout.pos[wnode]
        out.append(Line(F, Wd, W_PIPE))
        out.append(_across_line(F, n, h, W_PIPE))
        if kw == "FLANGE-BLIND" or comp.skey == "FLBL":
            out.append(Poly([add(F, mul(n, h)), add(Wd, mul(n, h)), add(Wd, mul(n, -h)),
                             add(F, mul(n, -h))], W_SYMBOL, fill=True))
        elif comp.skey in ("FLWN", ""):
            out.append(Poly([add(F, mul(n, 0.55 * h)), Wd, add(F, mul(n, -0.55 * h))],
                            W_SYMBOL, closed=False))
        else:  # slip-on, socket-weld, lap joint: a second, shorter line
            out.append(_across_line(lerp(F, Wd, 0.6), n, 0.6 * h))
    elif kw == "GASKET":
        out.append(Line(P, Q, W_PIPE))
        out.append(_across_line(lerp(P, Q, 0.5), n, 0.8 * h, W_THIN))
    elif kw == "VALVE":
        M = lerp(P, Q, 0.5)
        out.append(Poly([add(P, mul(n, h)), add(P, mul(n, -h)), M], W_SYMBOL))
        out.append(Poly([add(Q, mul(n, h)), add(Q, mul(n, -h)), M], W_SYMBOL,
                        fill=VALVE_MARKS.get(comp.skey[:2]) == "check"))
        mark = VALVE_MARKS.get(comp.skey[:2])
        if mark == "ball":
            out.append(Circle(M, 1.2 * z, W_SYMBOL))
        elif mark == "globe":
            out.append(Circle(M, 1.2 * z, W_SYMBOL, fill=True))
        elif mark == "plug":
            s = 1.0 * z
            out.append(Poly([(M[0] - s, M[1] - s), (M[0] + s, M[1] - s), (M[0] + s, M[1] + s),
                             (M[0] - s, M[1] + s)], W_SYMBOL))
        elif mark == "butterfly":
            out.append(_across_line(M, n, h, W_SYMBOL))
        if mark != "check":
            stem = _stem_dir(layout, e)
            top = add(M, mul(stem, 5.0 * z))
            d = layout.screen_dir(e)
            out.append(Line(M, top, W_SYMBOL))
            out.append(Line(add(top, mul(d, 2.5 * z)), add(top, mul(d, -2.5 * z)), W_SYMBOL))
        if comp.skey[2:4] in ("FL", ""):
            out.append(_across_line(P, n, 1.15 * h, W_PIPE))
            out.append(_across_line(Q, n, 1.15 * h, W_PIPE))
    elif kw.startswith("REDUCER"):
        big, small = (P, Q) if _bore_at(comp, graph.nodes[e.a].pos) >= _bore_at(
            comp, graph.nodes[e.b].pos) else (Q, P)
        h2 = 0.55 * h
        if kw == "REDUCER-ECCENTRIC":
            # Flat side drawn along the reducer's real flat direction (e.g. down for FOB).
            f = unit(iso_layout.project(e.flat, layout.rotation)) if e.flat else mul(n, -1.0)
            out.append(Poly([add(big, mul(f, h)), add(small, mul(f, h)),
                             add(small, mul(f, h - 2 * h2)), add(big, mul(f, -h))], W_SYMBOL))
        else:
            out.append(Poly([add(big, mul(n, h)), add(small, mul(n, h2)),
                             add(small, mul(n, -h2)), add(big, mul(n, -h))], W_SYMBOL))
    elif kw in ("COUPLING", "UNION"):
        hh = 0.75 * h
        out.append(Poly([add(P, mul(n, hh)), add(Q, mul(n, hh)), add(Q, mul(n, -hh)),
                         add(P, mul(n, -hh))], W_SYMBOL))
        if kw == "UNION":
            out.append(_across_line(lerp(P, Q, 0.5), n, hh))
    else:  # anything else: a box with its SKEY
        hh = 0.8 * h
        out.append(Poly([add(P, mul(n, hh)), add(Q, mul(n, hh)), add(Q, mul(n, -hh)),
                         add(P, mul(n, -hh))], W_SYMBOL))
        label = comp.skey or kw[:4]
        out.append(Text(add(lerp(P, Q, 0.5), mul(n, hh + 1.2 * z)), label, 1.8 * z))
    return out


def _bore_at(comp, point):
    for p in comp.end_points:
        if g.dist(p.xyz(), point) <= 1.0:
            return p.bore
    return 0.0


def _stem_dir(layout, e):
    """Valve stem: up for horizontal lines, north for vertical ones."""
    if abs(e.direction[2]) > 0.99:
        return unit(iso_layout.project((0.0, 1.0, 0.0), layout.rotation))
    return unit(iso_layout.project((0.0, 0.0, 1.0), layout.rotation))


def _cap(layout, node_id, z):
    graph = layout.graph
    edges = graph.edges_at(node_id)
    if not edges:
        return []
    e = edges[0]
    C = layout.pos[node_id]
    other = layout.pos[graph.other(e, node_id)]
    out_dir = unit(sub(C, other))
    n = layout.across(e)
    r = 2.2 * z
    arc = [add(add(C, mul(n, r * math.cos(t))), mul(out_dir, r * math.sin(t)))
           for t in [i * math.pi / 12 for i in range(13)]]
    return [Poly(arc, W_SYMBOL, closed=True)]


def _support(layout, e, at, comp, z):
    down = unit(iso_layout.project((0.0, 0.0, -1.0), layout.rotation))
    if abs(e.direction[2]) > 0.99:
        down = unit(iso_layout.project((-1.0, 0.0, 0.0), layout.rotation))
    d = layout.screen_dir(e)
    base = add(at, mul(down, 3.0 * z))
    out = [Poly([at, add(base, mul(d, 1.8 * z)), add(base, mul(d, -1.8 * z))], W_SYMBOL, fill=True)]
    name = comp.attr("NAME") or comp.skey
    if name:
        out.append(Text(add(base, mul(down, 3.0 * z)), name, 1.8 * z))
    return out


def _skew_triangle(layout, e, P, Q, z):
    """Hatched triangle whose legs follow the two main iso axes of a skewed line."""
    comps = sorted(range(3), key=lambda i: -abs(e.direction[i]))[:2]
    axes = []
    for i in comps:
        v = [0.0, 0.0, 0.0]
        v[i] = 1.0 if e.direction[i] > 0 else -1.0
        axes.append(iso_layout.project(tuple(v), layout.rotation))
    s = sub(Q, P)
    (a1, b1), (a2, b2) = axes
    det = a1 * b2 - a2 * b1
    if abs(det) < 1e-9:
        return []
    k1 = (s[0] * b2 - s[1] * a2) / det
    corner = add(P, mul(axes[0], k1))
    out = [Line(P, corner, W_THIN), Line(corner, Q, W_THIN)]
    for t in (0.25, 0.5, 0.75):
        out.append(Line(lerp(P, corner, t), lerp(P, Q, t), W_THIN))
    return out
