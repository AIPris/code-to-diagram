"""Mechanical layout quality: what a reviewer would otherwise have to find by looking at the picture.

Every route is explicit (layout.route_edges), so the polylines are known and defects are countable: edges that run
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


def label_cost(rect, own, lines, solid):
    """Segments of other edges and nodes under a label rectangle (x, y, w, h): lower is better."""
    cost = 0
    for k, line in lines.items():
        if k != own:
            cost += sum(_through(s, rect, 0) for s in _segments(line))
    return cost


def evaluate(lines, boxes, edges, leaves, label_conflicts, size):
    """lines {edge idx: polyline}. -> dict(node_hits, crossings, label_conflicts, score)."""
    hits = 0
    for idx, line in lines.items():
        a, b, _ = edges[idx]
        for n in leaves:
            if n not in (a, b) and any(_through(s, boxes[n]) for s in _segments(line)):
                hits += 1
    segs = [(idx, s) for idx, line in lines.items() for s in _segments(line)]
    crossings = sum(1 for i in range(len(segs)) for j in range(i + 1, len(segs)) if segs[i][0] != segs[j][0] and _cross(segs[i][1], segs[j][1]))
    w, h = size
    aspect = abs(math.log(max(w, 1) / max(h, 1) / 1.6))
    score = 25 * hits + crossings + 6 * label_conflicts + 4 * aspect + 2 * (w * h) / 1e6
    return dict(node_hits=hits, crossings=crossings, label_conflicts=label_conflicts, score=score)
