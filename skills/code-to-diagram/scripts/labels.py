"""Edge label placement.

draw.io puts an edge label at the middle of the edge, so labels of neighbouring edges land on top of each other or on a
node. Every route is explicit by the time labels are placed (routing.route_edges), so the polyline of each edge is known:
slide each label along its own edge to the first spot where it overlaps no node and no earlier label. draw.io stores
that as the label's relative position along the edge (mxGeometry x in -1..1), so it stays editable.
"""
import math

from drawio_lib import text_width
from metrics import label_cost

SPOTS = (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.12, 0.88)     # fractions of the edge length, tried in this order
END_ZONE = 45                                                 # px at each end of an edge that labels avoid


def polyline(plan, s, t):
    """Corner points of an edge as draw.io routes it: port, waypoints, port, with an elbow where two points do not line up.
    The ports are where the edge really attaches (exit_at, entry_at) when the plan says so, else the anchor fractions on the box."""
    at = lambda box, f: (box[0] + f[0] * box[2], box[1] + f[1] * box[3])
    side = lambda f: f[0] in (0, 1) and 0 < f[1] < 1             # port on a left/right side
    pts = [plan.get("exit_at") or at(s, plan["exit"])] + list(plan["points"]) + [plan.get("entry_at") or at(t, plan["entry"])]
    out = [pts[0]]
    for i in range(1, len(pts)):
        a, b = out[-1], pts[i]
        if abs(a[0] - b[0]) > 1 and abs(a[1] - b[1]) > 1:
            first, last = i == 1, i == len(pts) - 1
            if first and side(plan["exit"]):
                if last and side(plan["entry"]):
                    xm = (a[0] + b[0]) / 2
                    out += [(xm, a[1]), (xm, b[1])]
                else:
                    out.append((b[0], a[1]))
            elif last and side(plan["entry"]):
                out.append((a[0], b[1]))
            else:
                out.append((a[0], b[1]))
        out.append(b)
    return out


def _point_at(line, f):
    lengths = [math.dist(line[i], line[i + 1]) for i in range(len(line) - 1)]
    want, total = f * sum(lengths), 0
    for (a, b), n in zip(zip(line, line[1:]), lengths):
        if n and total + n >= want:
            k = (want - total) / n
            return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
        total += n
    return line[-1]


def lines_of(plan, boxes, edges):
    return {idx: polyline(plan[idx], boxes[a], boxes[b]) for idx, (a, b, _) in enumerate(edges)}


def place(lines, edges, labels, boxes, obstacles):
    """-> ({edge index: relative position (-1..1) for its label}, number of labels still touching a node, label or other edge).
    lines: polylines of all edges; labels: per edge text ('' for none); obstacles: ids of nodes a label must not cover."""
    rects = []
    solid = [boxes[i] for i in obstacles]
    todo = []
    for idx in range(len(edges)):
        if labels[idx]:
            line = lines[idx]
            todo.append((sum(math.dist(line[i], line[i + 1]) for i in range(len(line) - 1)), idx, line))
    out, touching = {}, 0
    for _, idx, line in sorted(todo, key=lambda t: t[0]):          # short edges have the fewest choices: place them first
        w, h = text_width(labels[idx], 6.6) + 12, 15 * len(labels[idx].split("\n")) + 4
        best = None
        length = sum(math.dist(line[i], line[i + 1]) for i in range(len(line) - 1))
        # arrowheads, diamonds and cardinalities live within ~45 px of an end: keep labels off them
        spots = [f for f in SPOTS if f * length >= END_ZONE and (1 - f) * length >= END_ZONE] or [0.5]
        for f in spots:
            cx, cy = _point_at(line, f)
            r = (cx - w / 2, cy - h / 2, w, h)
            hits = 10 * (sum(_overlap(r, o) for o in solid) + sum(_overlap(r, o) for o in rects)) + 3 * label_cost(r, idx, lines, solid)
            if best is None or hits < best[0]:
                best = (hits, f, r)
            if hits == 0:
                break
        rects.append(best[2])
        touching += best[0] > 0
        out[idx] = 2 * best[1] - 1
    return out, touching


def _overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
