"""Ports: where along a node's side each edge attaches.

Spreading the edges that share a side by direction alone leaves the two ends of an edge a few px out of line, and the
line wobbles. Ports are placed so that edges run straight instead. The sides with the most edges are placed first,
spread at least PITCH apart. Every other end then takes the coordinate of the end it is connected to, whenever that
coordinate lies on its own side: the edge is a straight line and needs no waypoints at all. What cannot line up
because the two sides do not overlap becomes an ordinary elbow, never a few-pixel step.

    ends = {(node, side): [(edge idx, "exit"|"entry", box of the far end)]}     boxes (x, y, w, h), top-down space
    pos = assign(B, ends, kinds, tb)      # {(idx, "exit"|"entry"): coordinate along its side}
    f = fraction(pos[...], box, side, kind)      # the anchor fraction that makes draw.io attach the edge there
"""
import math

PITCH = 12      # between two ports on one side
NEAR = 2 * PITCH    # ends this close, but not level, are a step: lined up when the room allows
MARGIN = 8      # from a corner: the rounded corner and the arrowhead need the room
PARA_SLANT = 0.2    # how far the slanted sides of a parallelogram lean, as a fraction of its width (draw.io's default)


def pav(desired, weights, seps):
    """Positions closest (weighted least squares) to `desired` with p[i+1] - p[i] >= seps[i]."""
    off = [0.0]
    for s in seps:
        off.append(off[-1] + s)
    blocks = []
    for i, d in enumerate(desired):
        blocks.append([(d - off[i]) * weights[i], weights[i], 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            a = blocks.pop()
            blocks[-1][0] += a[0]
            blocks[-1][1] += a[1]
            blocks[-1][2] += a[2]
    q = []
    for s, w, c in blocks:
        q += [s / w] * c
    return [q[i] + off[i] for i in range(len(desired))]


def span(box, side):
    """Extent of a box along the given side (a top/bottom side runs along x, a left/right side along y)."""
    x, y, w, h = box
    return (x, x + w) if side in "tb" else (y, y + h)


def half(kind, n, other, flat):
    """How far from the middle of a side, each way, an edge can attach and still be drawn straight and touching the
    outline. kind: the node's shape if its outline matters (diamond, ellipse, stadium, para, cylinder, note), n: length
    of the side, other: the node's other dimension, flat: the side is a real top or bottom one (a left-right layout
    turns the picture over, the shapes stay as they are). draw.io attaches an edge on a diamond or an ellipse where
    the line from the centre through the port meets the outline, which moves the port towards the middle (see fraction)."""
    if kind == "diamond":
        return 0.2 * n                                       # the port can be moved up to a quarter of the side
    if kind == "ellipse":
        return 0.28 * n
    if kind == "note":
        return 0
    if kind == "stadium":
        return max(n / 2 - other / 2, 0) if flat else 0.25 * n      # its ends are semicircles
    if kind == "para":
        return 0.3 * n
    if kind == "cylinder":
        return 0.25 * n if flat else max(n / 2 - 20, 0)      # its top and bottom are arcs
    return max(n / 2 - MARGIN, 0)


def fraction(pos, box, side, kind, tb=True):
    """Anchor fraction along a side that makes draw.io attach the edge at coordinate pos. Linear for most shapes. A
    diamond or an ellipse moves a port towards the middle, so it is asked for further out than it will land; so does
    a parallelogram on its slanted sides (PARA_SLANT of its width). Inverted here, so the edge ends where it was placed."""
    lo, hi = span(box, side)
    n, d = hi - lo, pos - (lo + hi) / 2
    if kind == "diamond":
        u = abs(d) / (n - 2 * abs(d))
    elif kind == "ellipse":
        u = abs(d) / math.sqrt(n * n - 4 * d * d)
    elif kind == "para" and (side in "tb") != tb:
        w, h = (box[2], box[3]) if tb else (box[3], box[2])          # the real width and height
        right = side == "r" if tb else side == "b"                     # a left-right layout turns the picture: top-down 'b' is the right side
        inset = PARA_SLANT * w * (0.5 + d / h if right else 0.5 - d / h)   # how far the slanted side lies inside the box at that height
        return 0.5 + d * (w / 2) / (w / 2 - inset) / h
    else:
        return (pos - lo) / n
    return 0.5 + math.copysign(min(u, 0.5), d)


def widen(sizes, edges, tb, kinds=()):
    """Grow nodes so that the side carrying the most edges can hold them PITCH apart. sizes {id: (w, h)} is changed in
    place; kinds {id: shape kind}. A shape whose room does not grow with its size (a note) is left alone."""
    ins, outs = {}, {}
    for a, b, _ in edges:
        if a != b:
            outs[a] = outs.get(a, 0) + 1
            ins[b] = ins.get(b, 0) + 1
    for n, (w, h) in sizes.items():
        side, other = (w, h) if tb else (h, w)
        kind, need = kinds.get(n), (max(ins.get(n, 0), outs.get(n, 0)) - 1) * PITCH
        if half(kind, side + 60, other, tb) > half(kind, side, other, tb):
            while 2 * half(kind, side, other, tb) < need:
                side += 6
        sizes[n] = (side, other) if tb else (other, side)


def _usable(box, side, kind, tb):
    lo, hi = span(box, side)
    reach = half(kind, hi - lo, box[3] if side in "tb" else box[2], (side in "tb") == tb)
    return (lo + hi) / 2 - reach, (lo + hi) / 2 + reach


def _fit(wants, lo, hi):
    """Positions nearest to `wants` (in this order), at least PITCH apart, inside [lo, hi]; squeezed when they do not fit."""
    n = len(wants)
    pitch = min(PITCH, (hi - lo) / (n - 1)) if n > 1 else 0
    off = [i * pitch for i in range(n)]
    q = [p - o for p, o in zip(pav(wants, [1] * n, [pitch] * (n - 1)), off)]
    return [min(max(v, lo), hi - off[-1]) + o for v, o in zip(q, off)]


def _far(other, side, tb):
    """Stretch along `side`'s axis where the far end of an edge can be: its node's usable stretch, or a single point (a corridor)."""
    if other[2] or other[3]:
        return _usable(other, side, None, tb)
    return (other[0], other[0]) if side in "tb" else (other[1], other[1])


class _End:
    def __init__(self, idx, end, side, box, lo, hi, other, tb):
        self.idx, self.end, self.side, self.lo, self.hi, self.other = idx, end, side, lo, hi, other
        self.far = _far(other, side, tb)                        # the far end's own stretch along this axis
        self.pos = None
        self.mates = None                                      # the ends sharing this side
        self.order = self._order(box)

    def _order(self, box):
        """Sort key among the ends of one side, chosen so that the edges do not cross each other next to the node.
        Top/bottom side: left to right as the far ends lie. Left/right side: ends whose column lies above come first,
        nearest column on top; then those facing the node; then those whose column lies below, farthest on top."""
        ox, oy, ow, oh = self.other
        if self.side in "tb":
            return (1, ox + ow / 2)
        x, y, w, h = box
        reach = abs(ox + ow / 2 - (x + w if self.side == "r" else x))
        a, b = max(self.lo, self.far[0]), min(self.hi, self.far[1])
        if a <= b:
            return (1, oy + oh / 2)
        return (0, reach) if oy + oh / 2 < y + h / 2 else (2, -reach)

    def first_wish(self):
        """Where this end would like to sit before anything is known about the far end's port."""
        a, b = max(self.lo, self.far[0]), min(self.hi, self.far[1])
        if a <= b:
            return (a + b) / 2
        return min(max((self.far[0] + self.far[1]) / 2, self.lo), self.hi)


def _faces(r, m):
    """Is m the other end of an edge that runs straight between r and it (both on a top/bottom side, or both on a left/right one)?"""
    return m is not None and (m.side in "tb") == (r.side in "tb")


def _place(sides, mate):
    """Greedy placement: the busiest sides first, spread PITCH apart; every other end takes the coordinate of the end it meets."""
    for group in sorted(sides, key=len, reverse=True):
        want = {}
        for r in group:
            m = mate(r)
            want[id(r)] = min(max(m.pos, r.lo), r.hi) if _faces(r, m) and m.pos is not None else r.first_wish()
        group.sort(key=lambda r: (r.order, want[id(r)], r.idx))
        for r, p in zip(group, _fit([want[id(r)] for r in group], group[0].lo, group[0].hi)):
            r.pos = p


def _kinks(group, mate):
    return sum(1 for r in group if _faces(r, mate(r)) and 0.5 < abs(r.pos - mate(r).pos) < NEAR)


def _shift(sides, mate):
    """Moving the ports of one side together, a few px, can line up several edges at once: something the greedy
    placement cannot see. Tried for every side that has a kink, keeping the move that leaves the fewest."""
    for _ in range(2):
        for group in sides:
            best, move = _kinks(group, mate), 0
            for r in group:
                m = mate(r)
                d = m.pos - r.pos if _faces(r, m) else 0
                if not 0.5 < abs(d) < NEAR or min(o.pos for o in group) + d < r.lo or max(o.pos for o in group) + d > r.hi:
                    continue
                for o in group:
                    o.pos += d
                k = _kinks(group, mate)
                for o in group:
                    o.pos -= d
                if k < best:
                    best, move = k, d
            for o in group:
                o.pos += move


def _snap(recs, mate):
    """Two ends still a few px apart: line them up if the room beside their neighbours allows."""
    for r in recs.values():
        m = mate(r)
        if r.end != "exit" or not _faces(r, m) or not 0.0005 < abs(r.pos - m.pos) < NEAR:
            continue
        t = (r.pos + m.pos) / 2
        if r.lo <= t <= r.hi and m.lo <= t <= m.hi and all(abs(o.pos - t) >= PITCH - 0.5 for e in (r, m) for o in e.mates if o is not e):
            r.pos = m.pos = t


def assign(B, ends, kinds=None, tb=True):
    """-> {(idx, end): coordinate along the side}. kinds {node: shape kind}, tb: the layout runs top-down."""
    kinds = kinds or {}
    recs, sides = {}, []
    for (n, side), lst in ends.items():
        lo, hi = _usable(B[n], side, kinds.get(n), tb)
        group = []
        for idx, end, other in lst:
            r = _End(idx, end, side, B[n], lo, hi, other, tb)
            recs[(idx, end)] = r
            group.append(r)
        for r in group:
            r.mates = group
        sides.append(group)

    def mate(r):                                        # the other end of the edge, unless a corridor lies between the two
        return None if r.other[2] == 0 and r.other[3] == 0 else recs.get((r.idx, "entry" if r.end == "exit" else "exit"))

    _place(sides, mate)
    _shift(sides, mate)
    _snap(recs, mate)
    return {k: r.pos for k, r in recs.items()}
