# SPDX-License-Identifier: LGPL-3.0-or-later
"""Not-to-scale isometric placement of an IsoGraph (pure Python).

Paper coordinates are millimetres with Y up.  Every edge keeps its iso
direction; only its paper length changes:
  - inline items and fitting legs get a fixed symbol length;
  - pipes get max(PIPE_MIN, k * sqrt(L)), so longer pipe stays longer.
Clashes between segments are removed by stretching a pipe on the tree path
between them; k is then fitted so the drawing fills the iso area.
"""

import math
from dataclasses import dataclass, field

from pcf import pcf_geom as g

C30, S30 = math.cos(math.radians(30)), math.sin(math.radians(30))
# Screen directions of world +X (east), +Y (north), +Z (up) for rotation 0.
BASE_AXES = ((C30, -S30), (C30, S30), (0.0, 1.0))

SYMBOL_LEN = {
    "FLANGE": 6.0, "FLANGE-BLIND": 4.0, "VALVE": 16.0, "REDUCER-CONCENTRIC": 9.0,
    "REDUCER-ECCENTRIC": 9.0, "GASKET": 2.5, "COUPLING": 6.0, "UNION": 7.0,
}
GENERIC_INLINE_LEN = 10.0
LEG_LEN = 5.0  # elbow/tee legs: the fitting is drawn tight around its centre
OLET_LEN = 6.0
PIPE_MIN = 8.0
CLEARANCE = 3.0  # mm between segments that do not touch
FOLD_DEG = 10.0  # edges leaving one node closer than this overlap
K_MIN, K_MAX, K_START = 0.15, 3.5, 1.0
ZOOM_MAX = 1.6  # largest symbol enlargement when a small line leaves room
MAX_ROUNDS = 80
MARGIN = 25.0  # room left around the line work for dimensions and balloons


def rotate_xy(v, quarter_turns):
    x, y, z = v
    for _ in range(quarter_turns % 4):
        x, y = -y, x
    return (x, y, z)


def project(v, rotation=0):
    """Screen (x, y) of a world vector for the given rotation (quarter turns)."""
    x, y, z = rotate_xy(v, rotation)
    ex, ey, ez = BASE_AXES
    return (x * ex[0] + y * ey[0] + z * ez[0], x * ex[1] + y * ey[1] + z * ez[1])


# Rotation 0 is FreeCAD's standard isometric view: looking along VIEW_DIR with
# screen-up along VIEW_UP (both world vectors).
VIEW_DIR = (-1.0 / math.sqrt(3), 1.0 / math.sqrt(3), -1.0 / math.sqrt(3))
VIEW_UP = (-S30 / math.hypot(2 * S30 * S30, 1), S30 / math.hypot(2 * S30 * S30, 1),
           1.0 / math.hypot(2 * S30 * S30, 1))


def rotation_for_camera(direction, up):
    """Quarter turns (0-3) whose isometric best matches a 3D camera.

    direction: where the camera looks; up: the camera's up vector (world).
    Isometrics are always drawn from above, so a camera looking up or
    straight down gets the nearest view from above facing the same way."""
    def score(r):
        turns = (4 - r) % 4
        return g.dot(direction, rotate_xy(VIEW_DIR, turns)) + g.dot(up, rotate_xy(VIEW_UP, turns))
    return max(range(4), key=score)


def unit2(v):
    n = math.hypot(v[0], v[1])
    return (0.0, 0.0) if n < 1e-12 else (v[0] / n, v[1] / n)


def across3d(direction):
    """World direction symbols and dimensions extend along, for a run."""
    if abs(direction[2]) > 0.99:  # vertical run: use east
        return (1.0, 0.0, 0.0)
    if abs(direction[2]) < 1e-6:  # horizontal run: use up
        return (0.0, 0.0, 1.0)
    return None  # skewed out of plan: use the screen perpendicular


# --------------------------------------------------------------------------
# 2D geometry
# --------------------------------------------------------------------------


def _seg_dist(p1, p2, q1, q2):
    """Minimum distance between segments p1-p2 and q1-q2 (0 when crossing)."""
    if _segments_cross(p1, p2, q1, q2):
        return 0.0
    return min(_pt_seg(p1, q1, q2), _pt_seg(p2, q1, q2), _pt_seg(q1, p1, p2), _pt_seg(q2, p1, p2))


def _pt_seg(p, a, b):
    ab = (b[0] - a[0], b[1] - a[1])
    den = ab[0] ** 2 + ab[1] ** 2
    t = 0.0 if den < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / den))
    return math.hypot(p[0] - a[0] - ab[0] * t, p[1] - a[1] - ab[1] * t)


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_cross(p1, p2, q1, q2):
    d1, d2 = _cross(q1, q2, p1), _cross(q1, q2, p2)
    d3, d4 = _cross(p1, p2, q1), _cross(p1, p2, q2)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and 0 not in (d1, d2, d3, d4)


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------


@dataclass
class Layout:
    graph: object
    rotation: int = 0
    k: float = K_START
    zoom: float = 1.0  # symbol size factor, < 1 only when nothing else fits
    stretch: dict = field(default_factory=dict)  # edge id -> extra length factor
    pos: dict = field(default_factory=dict)  # node id -> (x, y) paper mm, Y up
    parent: dict = field(default_factory=dict)  # node id -> (parent node, edge id)
    clashes: list = field(default_factory=list)  # (edge id, edge id)
    loops: list = field(default_factory=list)  # edge ids not in the spanning tree

    def screen_dir(self, edge):
        return unit2(project(edge.direction, self.rotation))

    def across(self, edge):
        """Screen unit vector along which a symbol on edge is drawn across the pipe."""
        a3 = across3d(edge.direction)
        if a3 is None:
            d = self.screen_dir(edge)
            return (-d[1], d[0])
        return unit2(project(a3, self.rotation))

    def bbox(self):
        xs = [p[0] for p in self.pos.values()]
        ys = [p[1] for p in self.pos.values()]
        return min(xs), min(ys), max(xs), max(ys)

    def segment(self, edge):
        return self.pos[edge.a], self.pos[edge.b]


def paper_length(layout, edge):
    comp = layout.graph.pcf.components[edge.comp]
    z = layout.zoom
    if edge.role == "pipe":
        base = max(PIPE_MIN * z, layout.k * math.sqrt(edge.length))
    elif edge.role in ("leg", "branch"):
        base = LEG_LEN * z
    elif edge.role == "olet":
        base = OLET_LEN * z
    else:
        base = SYMBOL_LEN.get(comp.keyword, GENERIC_INLINE_LEN) * z
    return base * layout.stretch.get(edge.id, 1.0)


def place(layout):
    """Positions from the spanning tree; disconnected pieces side by side."""
    graph = layout.graph
    layout.pos, layout.parent, layout.loops = {}, {}, []
    adjacency = {n.id: [] for n in graph.nodes}
    for e in graph.edges:
        adjacency[e.a].append(e)
        adjacency[e.b].append(e)
    used = set()
    offset_x = 0.0
    for root in _roots(graph, adjacency):
        if root in layout.pos:
            continue
        piece = {root: (0.0, 0.0)}
        layout.parent[root] = (None, None)
        queue = [root]
        while queue:
            u = queue.pop(0)
            for e in adjacency[u]:
                if e.id in used:
                    continue
                v = graph.other(e, u)
                if v in piece:
                    used.add(e.id)
                    layout.loops.append(e.id)
                    continue
                used.add(e.id)
                d = layout.screen_dir(e)
                sign = 1.0 if e.a == u else -1.0
                length = paper_length(layout, e)
                piece[v] = (piece[u][0] + sign * d[0] * length, piece[u][1] + sign * d[1] * length)
                layout.parent[v] = (u, e.id)
                queue.append(v)
        xs = [p[0] for p in piece.values()]
        shift = offset_x - min(xs)
        for nid, p in piece.items():
            layout.pos[nid] = (p[0] + shift, p[1])
        offset_x = max(xs) + shift + 30.0 * layout.zoom
    return layout


def _roots(graph, adjacency):
    """An open end of each connected piece first, then everything else."""
    ends = [n.id for n in graph.nodes if len(adjacency[n.id]) == 1]
    others = [n.id for n in graph.nodes if adjacency[n.id]]
    return ends + others


def find_clashes(layout):
    graph = layout.graph
    clashes = []
    edges = graph.edges
    run_of = {eid: i for i, run in enumerate(graph.runs) for eid in run["edges"]}
    clear = CLEARANCE * layout.zoom
    boxes = []
    for e in edges:
        (ax, ay), (bx, by) = layout.segment(e)
        boxes.append((min(ax, bx) - clear, min(ay, by) - clear, max(ax, bx) + clear, max(ay, by) + clear))
    for i, e in enumerate(edges):
        p1, p2 = layout.segment(e)
        bi = boxes[i]
        for j in range(i + 1, len(edges)):
            f = edges[j]
            bj = boxes[j]
            if bi[0] > bj[2] or bj[0] > bi[2] or bi[1] > bj[3] or bj[1] > bi[3]:
                continue  # too far apart to clash
            q1, q2 = layout.segment(f)
            shared = {e.a, e.b} & {f.a, f.b}
            if not shared and run_of.get(e.id) == run_of.get(f.id):
                continue  # consecutive items on one straight run
            if shared:
                n = shared.pop()
                de = _away(layout, e, n)
                df = _away(layout, f, n)
                if de[0] * df[0] + de[1] * df[1] > math.cos(math.radians(FOLD_DEG)):
                    clashes.append((e.id, f.id))
                continue
            if _seg_dist(p1, p2, q1, q2) < clear:
                clashes.append((e.id, f.id))
    layout.clashes = clashes
    return clashes


def _away(layout, edge, node):
    a, b = layout.segment(edge)
    other = b if edge.a == node else a
    here = layout.pos[node]
    return unit2((other[0] - here[0], other[1] - here[1]))


def _tree_path_edges(layout, u, v):
    """Edge ids on the spanning-tree path between nodes u and v."""
    def ancestors(n):
        chain = []
        while n is not None:
            chain.append(n)
            n = layout.parent.get(n, (None, None))[0]
        return chain

    au, av = ancestors(u), ancestors(v)
    common = next((n for n in au if n in set(av)), None)
    path = []
    for chain in (au, av):
        for n in chain:
            if n == common:
                break
            path.append(layout.parent[n][1])
    return [p for p in path if p is not None]


def resolve_clashes(layout):
    """Stretch pipes until nothing clashes, or MAX_ROUNDS is reached."""
    graph = layout.graph
    place(layout)
    clashes = find_clashes(layout)
    for _round in range(MAX_ROUNDS):
        if not clashes:
            break
        e1, e2 = graph.edges[clashes[0][0]], graph.edges[clashes[0][1]]
        path = _tree_path_edges(layout, e1.a, e2.a) + [e1.id, e2.id]
        candidates = [eid for eid in dict.fromkeys(path) if graph.edges[eid].role == "pipe"]
        if not candidates:
            candidates = list(dict.fromkeys(path))
        best = None
        for eid in candidates:
            trial = dict(layout.stretch)
            trial[eid] = trial.get(eid, 1.0) * 1.35 + 0.15
            saved = layout.stretch
            layout.stretch = trial
            place(layout)
            count = len(find_clashes(layout))
            layout.stretch = saved
            if best is None or count < best[0]:
                best = (count, trial)
        layout.stretch = best[1]
        place(layout)
        clashes = find_clashes(layout)
    return clashes


def _extent(layout):
    x0, y0, x1, y1 = layout.bbox()
    return x1 - x0 + 2 * MARGIN, y1 - y0 + 2 * MARGIN


def _largest(ok, lo, hi, steps=25):
    """Largest value in [lo, hi] for which ok(value) holds, given ok(lo)."""
    for _ in range(steps):
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def fit(layout, width, height):
    """Fill width x height: the largest pipe factor k (K_MIN..K_MAX) that fits.
    Symbols grow (zoom up to ZOOM_MAX) when even K_MAX leaves room, and shrink
    only when even K_MIN does not fit."""
    def fits(k):
        layout.k = k
        place(layout)
        w, h = _extent(layout)
        return w <= width and h <= height

    if not fits(K_MIN):
        w, h = _extent(layout)
        layout.zoom *= min(width / w, height / h) * 0.95
    if fits(K_MAX):
        base = layout.zoom

        def fits_zoom(factor):
            layout.zoom = base * factor
            return fits(K_MAX)

        fits_zoom(_largest(fits_zoom, 1.0, ZOOM_MAX))
        return layout
    fits(_largest(fits, K_MIN, K_MAX))
    return layout


def layout_graph(graph, width, height, rotation=None, compression=None):
    """Best Layout of graph for an iso area of width x height mm.

    rotation: quarter turns about Z (None = try all four);
    compression: fixed pipe factor k (None = fit to the area).
    """
    best = None
    for rot in ([rotation] if rotation is not None else range(4)):
        lay = Layout(graph, rotation=rot, k=compression or K_START)
        for _ in range(3):
            resolve_clashes(lay)
            if compression is None:
                fit(lay, width, height)
            if not find_clashes(lay):
                break
        score = (len(lay.clashes), -(lay.k * lay.zoom), rot)  # fewest clashes, then biggest
        if best is None or score < best[0]:
            best = (score, lay)
    lay = best[1]
    place(lay)
    find_clashes(lay)
    return lay
