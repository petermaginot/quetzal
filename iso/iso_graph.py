# SPDX-License-Identifier: LGPL-3.0-or-later
"""Topology of one pipeline for isometric drawing (pure Python).

A PcfFile becomes a graph: nodes are distinct points (end, centre and branch
points, merged within MERGE_TOL), edges are straight legs of components.
Pipes and inline items give one edge; elbows give two legs through their
centre point; tees give three; olets give one leg from the header centre
line to the branch end.  Caps, bolts and supports become markers.
"""

import math
from dataclasses import dataclass, field

from pcf import pcf_geom as g

MERGE_TOL = 1.0  # mm
AXIS_TOL_DEG = 1.0

INLINE = {"FLANGE", "FLANGE-BLIND", "VALVE", "REDUCER-CONCENTRIC", "REDUCER-ECCENTRIC",
          "GASKET", "COUPLING", "UNION"}
FACE_NEIGHBOURS = {"GASKET", "FLANGE", "FLANGE-BLIND", "VALVE"}
AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}

# End kinds of a component end: BW / SW / SC weld or screwed ends, FACE for a
# bolted face, CLOSED for the back of a blind flange, PLAIN for pipe ends
# (they take whatever the fitting they meet has).


@dataclass
class Node:
    id: int
    pos: tuple
    ends: list = field(default_factory=list)  # (component index, end kind)
    weld: str = ""  # "BW", "SW", "SC" or ""
    work_point: bool = False
    centre: bool = False  # centre point of an elbow, tee or olet


@dataclass
class Edge:
    id: int
    a: int
    b: int
    comp: int
    role: str  # pipe | inline | leg | branch | olet
    length: float
    direction: tuple  # 3D unit vector a -> b
    axis: str = ""  # "X", "Y", "Z" when aligned, else "" (skewed)
    flat: tuple = None  # eccentric reducer: world direction of the flat side


@dataclass
class Marker:
    kind: str  # cap | bolt | support
    comp: int
    node: int = -1
    edge: int = -1
    t: float = 0.0
    pos: tuple = (0.0, 0.0, 0.0)


@dataclass
class IsoGraph:
    pcf: object
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    markers: list = field(default_factory=list)
    comp_edges: dict = field(default_factory=dict)  # component index -> [edge ids]
    # Collinear chains: {"nodes": ordered node ids, "edges", "direction", "axis"}
    runs: list = field(default_factory=list)
    skipped: list = field(default_factory=list)  # (component index, reason)

    def edges_at(self, node_id):
        return [e for e in self.edges if node_id in (e.a, e.b)]

    def other(self, edge, node_id):
        return edge.b if edge.a == node_id else edge.a


def axis_of(direction):
    """'X'/'Y'/'Z' if direction is within AXIS_TOL_DEG of a world axis, else ''."""
    cos_tol = math.cos(math.radians(AXIS_TOL_DEG))
    for name, ax in AXES.items():
        if abs(g.dot(direction, ax)) >= cos_tol:
            return name
    return ""


def end_code(skey, default="BW"):
    code = skey[2:4].upper() if len(skey) >= 4 else ""
    if code == "FL":
        return "FACE"
    return code if code in ("BW", "SW", "SC") else default


def build(pcf_file):
    graph = IsoGraph(pcf=pcf_file)

    def node_at(p):
        for n in graph.nodes:
            if g.dist(n.pos, p) <= MERGE_TOL:
                return n
        n = Node(len(graph.nodes), tuple(p))
        graph.nodes.append(n)
        return n

    def add_edge(ci, na, nb, role):
        vec = g.sub(nb.pos, na.pos)
        length = g.length(vec)
        if length < 1e-6:
            return None
        d = g.unit(vec)
        e = Edge(len(graph.edges), na.id, nb.id, ci, role, length, d, axis_of(d))
        graph.edges.append(e)
        graph.comp_edges.setdefault(ci, []).append(e.id)
        return e

    comps = pcf_file.components
    # First pass: nodes for every end point, so neighbours can be looked up.
    for ci, c in enumerate(comps):
        for p in c.end_points:
            node_at(p.xyz())

    def neighbours(ci, p):
        n = node_at(p.xyz())
        out = []
        for cj, c in enumerate(comps):
            if cj != ci and any(g.dist(q.xyz(), n.pos) <= MERGE_TOL for q in c.end_points):
                out.append(c)
        return out

    for ci, c in enumerate(comps):
        kw = c.keyword
        eps = c.end_points
        if kw in ("BOLT", "SUPPORT"):
            if c.co_ords is not None:
                graph.markers.append(Marker(kw.lower(), ci, pos=c.co_ords.xyz()))
            else:
                graph.skipped.append((ci, "no CO-ORDS"))
            continue
        if kw == "OLET":
            if c.centre_point is None or not c.branch_points:
                graph.skipped.append((ci, "olet without CENTRE-POINT/BRANCH1-POINT"))
                continue
            cp = node_at(c.centre_point.xyz())
            bp = node_at(c.branch_points[0].xyz())
            cp.centre = True
            cp.ends.append((ci, "OLET"))
            bp.ends.append((ci, end_code(c.skey)))
            add_edge(ci, cp, bp, "olet")
            continue
        if kw == "CAP":
            if eps:
                n = node_at(eps[0].xyz())
                n.ends.append((ci, end_code(c.skey)))
                graph.markers.append(Marker("cap", ci, node=n.id, pos=n.pos))
            continue
        if len(eps) < 2:
            graph.skipped.append((ci, "fewer than two END-POINTs"))
            continue
        n1, n2 = node_at(eps[0].xyz()), node_at(eps[1].xyz())

        if kw in ("ELBOW", "BEND", "TEE") and c.centre_point is not None:
            cp = node_at(c.centre_point.xyz())
            cp.centre = True
            kind = end_code(c.skey)
            n1.ends.append((ci, kind))
            n2.ends.append((ci, kind))
            add_edge(ci, n1, cp, "leg")
            add_edge(ci, cp, n2, "leg")
            if kw == "TEE" and c.branch_points:
                bp = node_at(c.branch_points[0].xyz())
                bp.ends.append((ci, kind))
                add_edge(ci, cp, bp, "branch")
            continue

        if kw == "PIPE":
            k1 = k2 = "PLAIN"
        elif kw in ("FLANGE", "FLANGE-BLIND"):
            k1, k2 = _flange_ends(c, neighbours(ci, eps[0]), neighbours(ci, eps[1]))
        elif kw == "GASKET":
            k1 = k2 = "FACE"
        elif kw == "VALVE":
            k1 = k2 = end_code(c.skey, "FACE")
        else:
            k1 = k2 = end_code(c.skey)
        n1.ends.append((ci, k1))
        n2.ends.append((ci, k2))
        e = add_edge(ci, n1, n2, "pipe" if kw == "PIPE" else "inline")
        if e is not None and kw == "REDUCER-ECCENTRIC":
            _eccentric(e, c, n1, n2)

    _split_at_interior_nodes(graph)
    _snap_markers(graph)
    _classify_nodes(graph)
    graph.runs = _runs(graph)
    return graph


FLAT_DIRECTIONS = {"UP": (0.0, 0.0, 1.0), "DOWN": (0.0, 0.0, -1.0), "NORTH": (0.0, 1.0, 0.0),
                   "SOUTH": (0.0, -1.0, 0.0), "EAST": (1.0, 0.0, 0.0), "WEST": (-1.0, 0.0, 0.0)}


def _eccentric(edge, comp, n1, n2):
    """Snap an eccentric reducer onto its run axis and find its flat side.

    The centres of an eccentric reducer are offset by (OD - OD2) / 2, so the
    raw end-to-end direction is a few degrees off the pipe axis.  Its flat
    side is the side the small end's centre is offset towards (bottoms line
    up on a flat-on-bottom reducer)."""
    d = edge.direction
    i = max(range(3), key=lambda k: abs(d[k]))
    axis = [0.0, 0.0, 0.0]
    axis[i] = 1.0 if d[i] > 0 else -1.0
    axis = tuple(axis)
    vec = g.sub(n2.pos, n1.pos)
    edge.direction, edge.axis = axis, "XYZ"[i]
    edge.length = abs(g.dot(vec, axis))
    big, small = (n1, n2) if comp.end_points[0].bore >= comp.end_points[1].bore else (n2, n1)
    off = g.sub(small.pos, big.pos)
    off = g.sub(off, g.scale(axis, g.dot(off, axis)))
    if g.length(off) > 0.5:
        edge.flat = g.unit(off)
    else:
        given = (comp.attr("FLAT-DIRECTION") or "").upper()
        edge.flat = FLAT_DIRECTIONS.get(given, (1.0, 0.0, 0.0) if i == 2 else (0.0, 0.0, -1.0))


def _flange_ends(comp, nb1, nb2):
    """(kind of end 1, kind of end 2): which end is the bolted face."""
    if comp.keyword == "FLANGE-BLIND":
        return "FACE", "CLOSED"

    def score(p, nbs):
        s = 2 if p.extra and p.extra[0].upper() in ("FL", "RF", "FF") else 0
        for c in nbs:
            s += 1 if c.keyword in FACE_NEIGHBOURS else -1
        return s

    weld = "SW" if comp.skey == "FLSW" else "BW"
    if score(comp.end_points[1], nb2) > score(comp.end_points[0], nb1):
        return weld, "FACE"
    return "FACE", weld  # Quetzal writes the face first


def _split_at_interior_nodes(graph):
    """Split edges at nodes lying inside them (an olet on a pipe, a tee
    centre on a continuous header) so the graph is connected there."""
    changed = True
    while changed:
        changed = False
        for e in list(graph.edges):
            a, b = graph.nodes[e.a].pos, graph.nodes[e.b].pos
            for n in graph.nodes:
                if n.id in (e.a, e.b):
                    continue
                t = g.segment_param(n.pos, a, b, MERGE_TOL)
                if t is None or t * e.length <= MERGE_TOL or (1 - t) * e.length <= MERGE_TOL:
                    continue
                first = g.dist(a, n.pos)
                tail = Edge(len(graph.edges), n.id, e.b, e.comp, e.role,
                            e.length - first, e.direction, e.axis)
                e.b, e.length = n.id, first
                graph.edges.append(tail)
                graph.comp_edges[e.comp].append(tail.id)
                changed = True
                break
            if changed:
                break


def _snap_markers(graph):
    """Attach bolts and supports to the edge they sit on.  Supports are often
    given at the pipe bottom, so allow up to the nominal bore off the axis."""
    inch = graph.pcf.units_bore.upper().startswith("IN")
    for m in graph.markers:
        if m.kind == "cap":
            continue
        bore = graph.pcf.components[m.comp].co_ords.bore * (25.4 if inch else 1.0)
        tol = max(MERGE_TOL, bore)
        best = None
        for e in graph.edges:
            a, b = graph.nodes[e.a].pos, graph.nodes[e.b].pos
            t = g.segment_param(m.pos, a, b, tol)
            if t is not None:
                off = g.dist(g.add(a, g.scale(g.sub(b, a), t)), m.pos)
                if best is None or off < best[0]:
                    best = (off, e.id, t)
        if best is not None:
            m.edge, m.t = best[1], best[2]


def _classify_nodes(graph):
    edge_count = {}
    for e in graph.edges:
        edge_count[e.a] = edge_count.get(e.a, 0) + 1
        edge_count[e.b] = edge_count.get(e.b, 0) + 1
    for n in graph.nodes:
        kinds = [k for _c, k in n.ends]
        comps = {c for c, _k in n.ends}
        if "OLET" in kinds:
            n.weld = "BW"  # olet welded to the header
        elif len(comps) >= 2 and not ({"FACE", "CLOSED"} & set(kinds)):
            n.weld = "SW" if "SW" in kinds else "SC" if "SC" in kinds else "BW"
        face = "FACE" in kinds
        open_end = edge_count.get(n.id, 0) == 1 and "CLOSED" not in kinds
        n.work_point = n.centre or face or open_end or _turns(graph, n.id)
    # A gasket's two faces are one joint: dimension to the first only.
    for e in graph.edges:
        if graph.pcf.components[e.comp].keyword == "GASKET":
            graph.nodes[e.b].work_point = False


def _turns(graph, node_id):
    """True where the line changes direction (e.g. pipe welded to pipe at an angle)."""
    dirs = [e.direction for e in graph.edges_at(node_id)]
    return any(abs(g.dot(a, b)) < 0.9998 for i, a in enumerate(dirs) for b in dirs[i + 1:])


def _runs(graph):
    """Collinear chains of edges, each as an ordered list of node ids."""
    seen = set()
    runs = []
    for e in graph.edges:
        if e.id in seen:
            continue
        chain = {e.id}
        stack = [e.a, e.b]
        while stack:
            nid = stack.pop()
            for f in graph.edges_at(nid):
                if f.id not in chain and abs(g.dot(f.direction, e.direction)) > 0.9998:
                    chain.add(f.id)
                    stack.append(graph.other(f, nid))
        seen |= chain
        node_ids = {n for fid in chain for n in (graph.edges[fid].a, graph.edges[fid].b)}
        origin = graph.nodes[e.a].pos
        ordered = sorted(node_ids, key=lambda nid: g.dot(g.sub(graph.nodes[nid].pos, origin), e.direction))
        runs.append({"nodes": ordered, "edges": sorted(chain), "direction": e.direction, "axis": e.axis})
    return runs
