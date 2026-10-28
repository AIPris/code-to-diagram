"""Mechanical layout quality: what a reviewer would otherwise have to find by looking at the picture.

Every route is explicit (routing.route_edges), so the polylines are known and defects are countable: edges that run
through an unrelated node, edges that cross each other, labels lying on another edge. build prints these numbers, and
emit uses the score to choose between a top-down and a left-to-right layout when the spec does not say which.
"""
import math


def _segments(line):
    return [(line[i], line[i + 1]) for i in range(len(line) - 1) if math.dist(line[i], line[i + 1]) > 0.5]


def _through(seg, box, shrink=3):
    """Does an axis-parallel segment pass through the inside of a box (x, y, w, h)?"""
    (x1, y1), (x2, y2) = seg
    x, y, w, h = box
    return (min(x1, x2) < x + w - shrink and max(x1, x2) > x + shrink and min(y1, y2) < y + h - shrink and max(y1, y2) > y + shrink)


def _cross(s, t):
    """Do two axis-parallel segments cross in their interiors (a horizontal and a vertical one)?"""
    (a1, b1), (a2, b2) = s
    (c1, d1), (c2, d2) = t
    horiz_s, horiz_t = abs(b1 - b2) < 0.5, abs(d1 - d2) < 0.5
    if horiz_s == horiz_t:
        return False
    if not horiz_s:
        (a1, b1), (a2, b2), (c1, d1), (c2, d2) = (c1, d1), (c2, d2), (a1, b1), (a2, b2)
    hx0, hx1, hy = min(a1, a2), max(a1, a2), b1
    vx, vy0, vy1 = c1, min(d1, d2), max(d1, d2)
    return hx0 + 2 < vx < hx1 - 2 and vy0 + 2 < hy < vy1 - 2


JOG = 24        # a middle segment shorter than this (two lanes of 12 px) is a wobble in a line meant to be straight, not a turn
DETOUR = 40     # a route that travels this far against its own direction (out and back) is a detour, not a path
INLINE = 8       # ends on opposite sides of one diamond this close in height are one line to the eye
SHARE = 3       # two parallel runs closer than this look like one line


def _corners(line):
    """The polyline without repeated points and without points in the middle of a straight run."""
    pts = [line[0]]
    for p in line[1:]:
        if math.dist(p, pts[-1]) > 0.5:
            pts.append(p)
    out = pts[:1]
    for i in range(1, len(pts) - 1):
        a, b, c = out[-1], pts[i], pts[i + 1]
        if not (abs(a[0] - b[0]) < 0.5 and abs(b[0] - c[0]) < 0.5 or abs(a[1] - b[1]) < 0.5 and abs(b[1] - c[1]) < 0.5):
            out.append(b)
    return out + pts[-1:]


def bends_and_jogs(line):
    """(turns, kinks) of one edge: kinks are middle segments shorter than JOG, the small sideways steps that make a line wobble."""
    pts = _corners(line)
    lengths = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    return max(len(pts) - 2, 0), sum(1 for n in lengths[1:-1] if n < JOG)


def backtrack(line):
    """How far a route travels against its own net direction, summed over both axes: 0 for a straight line, an L or a Z,
    positive where it goes out past its target and comes back, around the side of a column of nodes."""
    pts = _corners(line)
    total = 0.0
    for axis in (0, 1):
        sign = 1 if pts[-1][axis] >= pts[0][axis] else -1
        total += sum(max(0.0, -sign * (b[axis] - a[axis])) for a, b in zip(pts, pts[1:]))
    return total


def _runs_overlap(s, t):
    """Do two parallel axis-aligned segments of different edges lie on top of each other for more than a few px?"""
    (a1, b1), (a2, b2) = s
    (c1, d1), (c2, d2) = t
    if abs(a1 - a2) < 0.5 and abs(c1 - c2) < 0.5:                      # both vertical
        return abs(a1 - c1) < SHARE and min(max(b1, b2), max(d1, d2)) - max(min(b1, b2), min(d1, d2)) > 6
    if abs(b1 - b2) < 0.5 and abs(d1 - d2) < 0.5:                      # both horizontal
        return abs(b1 - d1) < SHARE and min(max(a1, a2), max(c1, c2)) - max(min(a1, a2), min(c1, c2)) > 6
    return False


def label_cost(rect, own, lines, solid):
    """Segments of other edges and nodes under a label rectangle (x, y, w, h): lower is better."""
    cost = 0
    for k, line in lines.items():
        if k != own:
            cost += sum(_through(s, rect, 0) for s in _segments(line))
    return cost


def _inline_exits(box, ends):
    """Pairs of edge ends on opposite sides of a box at one height (or one column): drawn through a diamond they read as one line."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    left, right = [p[1] for p in ends if p[0] < cx - 1], [p[1] for p in ends if p[0] > cx + 1]
    above, below = [p[0] for p in ends if p[1] < cy - 1], [p[0] for p in ends if p[1] > cy + 1]
    return sum(abs(a - b) < INLINE for a in left for b in right) + sum(abs(a - b) < INLINE for a in above for b in below)


def evaluate(lines, boxes, edges, leaves, label_conflicts, size, bundles=None, titles=(), diamonds=()):
    """lines {edge idx: polyline}; bundles {edge idx: fan key}: edges of one fan share their trunk on purpose; titles: rectangles
    (x, y, w, h) of container title text, which no edge may strike through; diamonds: ids of decision nodes, whose edges
    must not leave on opposite sides at one height (they read as one line through the diamond). -> dict(node_hits, title_hits, crossings, label_conflicts, bends, jogs, shared, detour, score)."""
    hits = struck = 0
    for idx, line in lines.items():
        a, b, _ = edges[idx]
        for n in leaves:
            if n not in (a, b) and any(_through(s, boxes[n]) for s in _segments(line)):
                hits += 1
        struck += sum(1 for t in titles if any(_through(s, t, 0) for s in _segments(line)))
    inline = sum(_inline_exits(boxes[n], [p for idx, line in lines.items() for p, end in ((line[0], edges[idx][0]), (line[-1], edges[idx][1])) if end == n])
                 for n in diamonds)
    segs = [(idx, s) for idx, line in lines.items() for s in _segments(line)]
    crossings = sum(1 for i in range(len(segs)) for j in range(i + 1, len(segs)) if segs[i][0] != segs[j][0] and _cross(segs[i][1], segs[j][1]))
    w, h = size
    aspect = abs(math.log(max(w, 1) / max(h, 1) / 1.6))
    bends = jogs = detour = 0
    for line in lines.values():
        b, j = bends_and_jogs(line)
        bends, jogs = bends + b, jogs + j
        detour += backtrack(line) >= DETOUR
    merged = [(idx, s) for idx, line in lines.items() for s in _segments(_corners(line))]
    bundles = bundles or {}
    same_fan = lambda a, b: bundles.get(a) is not None and bundles.get(a) == bundles.get(b)
    shared = sum(1 for i in range(len(merged)) for j in range(i + 1, len(merged))
                 if merged[i][0] != merged[j][0] and not same_fan(merged[i][0], merged[j][0]) and _runs_overlap(merged[i][1], merged[j][1]))
    score = 25 * hits + 10 * struck + 6 * inline + crossings + 6 * label_conflicts + 4 * aspect + 2 * (w * h) / 1e6 + 0.25 * bends + 2 * jogs + 2 * shared + 2 * detour
    return dict(node_hits=hits, title_hits=struck, inline=inline, crossings=crossings, label_conflicts=label_conflicts, bends=bends, jogs=jogs, shared=shared, detour=detour, score=score)
