"""Obstacle-avoiding routes for edges that would cut through a node.

draw.io routes an unpinned orthogonal edge by looking only at its two ends, so an edge between nodes in different groups
can run straight through a third node. For every edge whose route (as routing.route_edges and labels.polyline know it)
hits a node, search a grid built from the nodes' own edges for the cheapest orthogonal path that stays clear of all of
them (length plus a penalty per bend) and pin it with waypoints. Edges that are already clear are left alone, so moving
a shape in draw.io still re-routes them.
"""
import heapq

from labels import polyline
from metrics import SHARE, _corners, _cross, _runs_overlap, _segments, _through
from ports import NEAR, PITCH, _usable, fraction

CLEAR = 6          # distance kept from every node the edge does not belong to
STUB = 16          # straight piece leaving and entering a node before the first turn
BEND = 60          # cost of a turn, in px of extra length
CROSS = 100        # cost of crossing another edge, in px of extra length
LINE = 400         # cost of running along another edge's line
MAX_STATES = 90000
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
OUT = {"r": (1, 0), "l": (-1, 0), "b": (0, 1), "t": (0, -1)}


def side_of(frac):
    """Which side of its node a port fraction (fx, fy) sits on."""
    fx, fy = frac
    return "t" if fy == 0 else "b" if fy == 1 else "l" if fx == 0 else "r"


def clear(line, boxes, margin=CLEAR):
    return not any(_through((line[i], line[i + 1]), b, shrink=-margin) for i in range(len(line) - 1) for b in boxes)


def _simplify(pts):
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = out[-1], pts[i], pts[i + 1]
        if (abs(a[0] - b[0]) < 0.5 and abs(b[0] - c[0]) < 0.5) or (abs(a[1] - b[1]) < 0.5 and abs(b[1] - c[1]) < 0.5):
            continue
        out.append(b)
    out.append(pts[-1])
    return out


def _square_off(pts):
    """Grid lines a fraction of a px apart (a node edge and a port nearly in line) leave a waypoint pair that is not
    axis-aligned: draw.io would add an elbow of its own there. The later point takes the earlier one's coordinate."""
    out = [pts[0]] if pts else []
    for x, y in pts[1:]:
        px, py = out[-1]
        out.append((px if 0 < abs(x - px) < 1 else x, py if 0 < abs(y - py) < 1 else y))
    return out


def _penalty(lines):
    """-> f(x0, y0, x1, y1): cost of an axis-parallel move for crossing the polylines `lines` or running along them
    (the same tests as metrics: a crossing in the interior of both, a parallel run closer than SHARE for over 6 px)."""
    flat, upright = [], []                                   # (y, x from, x to) and (x, y from, y to) of the other edges' pieces
    for line in lines:
        for (a1, b1), (a2, b2) in _segments(_corners(line)):
            if abs(b1 - b2) < 0.5:
                flat.append((b1, min(a1, a2), max(a1, a2)))
            elif abs(a1 - a2) < 0.5:
                upright.append((a1, min(b1, b2), max(b1, b2)))
    cache = {}

    def cost(x0, y0, x1, y1):
        key = (x0, y0, x1, y1)
        if key not in cache:
            total = 0
            if abs(y0 - y1) < 0.5:                           # along a row: crosses the upright pieces, runs along the flat ones
                lo, hi = min(x0, x1), max(x0, x1)
                total += CROSS * sum(1 for x, a, b in upright if lo + 2 < x < hi - 2 and a + 2 < y0 < b - 2)
                total += LINE * any(abs(y - y0) < SHARE and min(hi, b) - max(lo, a) > 6 for y, a, b in flat)
            else:
                lo, hi = min(y0, y1), max(y0, y1)
                total += CROSS * sum(1 for y, a, b in flat if lo + 2 < y < hi - 2 and a + 2 < x0 < b - 2)
                total += LINE * any(abs(x - x0) < SHARE and min(hi, b) - max(lo, a) > 6 for x, a, b in upright)
            cache[key] = total
        return cache[key]
    return cost


def route(p0, exit_dir, p1, entry_dir, obstacles, lines=(), limit=MAX_STATES, meter=None):
    """Orthogonal path p0 -> p1 around obstacles (x, y, w, h), leaving p0 along exit_dir and arriving against entry_dir.
    lines: polylines of other edges to keep away from (crossing them costs CROSS, running along them LINE).
    limit: most states the search may expand; meter: a one-item list that counts the states expanded over many searches.
    Returns the inner corner points, or None when there is no clear path."""
    tangle = _penalty(lines) if lines else None
    s = (p0[0] + exit_dir[0] * STUB, p0[1] + exit_dir[1] * STUB)
    e = (p1[0] + entry_dir[0] * STUB, p1[1] + entry_dir[1] * STUB)
    boxes = [(x - CLEAR, y - CLEAR, x + w + CLEAR, y + h + CLEAR) for x, y, w, h in obstacles]
    pts_x = [s[0], e[0], p0[0], p1[0]] + [v for b in boxes for v in (b[0], b[2])]
    pts_y = [s[1], e[1], p0[1], p1[1]] + [v for b in boxes for v in (b[1], b[3])]
    xs = sorted(set(pts_x) | {min(pts_x) - 40, max(pts_x) + 40})          # a lane around everything, so a way round always exists
    ys = sorted(set(pts_y) | {min(pts_y) - 40, max(pts_y) + 40})
    if tangle:                                                            # and the middle of every gap, where two edges can pass side by side
        xs = sorted(set(xs) | {(a + b) / 2 for a, b in zip(xs, xs[1:]) if b - a > 4 * CLEAR})
        ys = sorted(set(ys) | {(a + b) / 2 for a, b in zip(ys, ys[1:]) if b - a > 4 * CLEAR})
    ix = {v: i for i, v in enumerate(xs)}
    iy = {v: i for i, v in enumerate(ys)}
    blocked_v, blocked_h, blocked_s = set(), set(), set()     # vertices, horizontal segments (i, j)->(i+1, j), vertical (i, j)->(i, j+1)
    for x0, y0, x1, y1 in boxes:
        i0, i1, j0, j1 = ix[x0], ix[x1], iy[y0], iy[y1]
        for i in range(i0 + 1, i1):
            for j in range(j0 + 1, j1):
                blocked_v.add((i, j))
        for j in range(j0 + 1, j1):
            for i in range(i0, i1):
                blocked_h.add((i, j))
        for i in range(i0 + 1, i1):
            for j in range(j0, j1):
                blocked_s.add((i, j))
    start, goal = (ix[s[0]], iy[s[1]]), (ix[e[0]], iy[e[1]])
    if start in blocked_v or goal in blocked_v:
        return None
    d0 = DIRS.index(exit_dir)
    d_end = DIRS.index((-entry_dir[0], -entry_dir[1]))          # the last move runs towards the port
    h = lambda i, j: abs(xs[i] - xs[goal[0]]) + abs(ys[j] - ys[goal[1]])
    best, parent, heap, n = {(start, d0): 0.0}, {}, [(h(*start), 0.0, start, d0)], 0
    while heap and n < limit:
        _, g, (i, j), d = heapq.heappop(heap)
        if g > best.get(((i, j), d), 1e18):
            continue
        n += 1
        if (i, j) == goal:
            if meter:
                meter[0] += n
            pts, state = [], ((i, j), d)
            while True:
                pts.append((xs[state[0][0]], ys[state[0][1]]))
                if state not in parent:
                    break
                state = parent[state]
            pts.reverse()
            return _square_off(_simplify([p0] + pts + [p1])[1:-1])
        for nd, (dx, dy) in enumerate(DIRS):
            ni, nj = i + dx, j + dy
            if (dx, dy) == (-DIRS[d][0], -DIRS[d][1]) or not (0 <= ni < len(xs) and 0 <= nj < len(ys)) or (ni, nj) in blocked_v:
                continue                                              # no U-turns
            if dx and (min(i, ni), j) in blocked_h or dy and (i, min(j, nj)) in blocked_s:
                continue
            cost = g + abs(xs[ni] - xs[i]) + abs(ys[nj] - ys[j]) + (BEND if nd != d else 0) + (BEND if (ni, nj) == goal and nd != d_end else 0)
            if tangle:
                cost += tangle(xs[i], ys[j], xs[ni], ys[nj])
            key = ((ni, nj), nd)
            if cost < best.get(key, 1e18):
                best[key] = cost
                parent[key] = ((i, j), d)
                heapq.heappush(heap, (cost + h(ni, nj), cost, (ni, nj), nd))
    if meter:
        meter[0] += n
    return None


def fix(plan, boxes, edges, solid, skip):
    """Pin an obstacle-free route for every edge whose current route cuts through a node. TB-space boxes/plan."""
    for idx, (a, b, _) in enumerate(edges):
        p = plan[idx]
        if a == b or "exit" not in p:
            continue
        others = [v for k, v in solid.items() if k not in skip(a, b)]
        line = polyline(p, boxes[a], boxes[b])
        if clear(line, others):
            continue
        path = route(line[0], OUT[side_of(p["exit"])], line[-1], OUT[side_of(p["entry"])], others)
        if path is not None:
            p["points"] = path


def _enters(line, *own):
    """Does the route run through one of its own two nodes (leaving by one side, crossing the node, arriving at another)?"""
    return any(_through(seg, box) for box in own for seg in _segments(line))


TANGLE_EDGES = 80        # a diagram with more edges is left as it is: the search below would take too long
TANGLE_TRIES = 12        # edges given a second route, the most tangled first
TANGLE_STATES = 40000    # states all the searches of one diagram may expand together, and one search at most:
TANGLE_ONE = 2500        # what bounds the time this takes (a search that hits its limit is dropped)
SIDES = "tblr"


def _free_port(box, side, kind, tb, taken, want=None):
    """Coordinate along `side` of box for a new port: as near to `want` (default the middle) as is PITCH away from the `taken` ones."""
    lo, hi = _usable(box, side, kind, tb)
    near = min(max((lo + hi) / 2 if want is None else want, lo), hi)
    for k in range(int((hi - lo) // PITCH) + 2):
        for c in ((near,) if k == 0 else (near - k * PITCH, near + k * PITCH)):
            if lo - 0.5 <= c <= hi + 0.5 and all(abs(c - t) >= PITCH - 0.5 for t in taken):
                return c
    return None


def untangle(plan, boxes, edges, solid, skip, kinds=None, tb=True):
    """Rip up and reroute: an edge that crosses other edges, or runs along one, gets a new route found with a penalty for
    both, and keeps it only if the diagram measures better (a crossing counts as much as four bends). It is also tried
    on other sides of its two nodes, at a free port: with the ports where they are, a crossing is often unavoidable.
    Edges of a fan keep the trunk they share."""
    if len(edges) > TANGLE_EDGES:
        return
    kinds = kinds or {}
    meter = [0]
    live = [i for i, (a, b, _) in enumerate(edges) if a != b and "exit" in plan[i]]
    lines = {i: polyline(plan[i], boxes[edges[i][0]], boxes[edges[i][1]]) for i in live}
    fan = lambda i: plan[i].get("bundle")

    def apart(i):
        return {j: l for j, l in lines.items() if j != i and not (fan(i) and fan(i) == fan(j))}

    def tangles(i, line):
        others = [_segments(_corners(l)) for l in apart(i).values()]
        return sum(1 for s in _segments(_corners(line)) for segs in others for t in segs if _cross(s, t) or _runs_overlap(s, t))

    def worth(i, line):
        return 4 * tangles(i, line) + max(len(_corners(line)) - 2, 0)

    def taken(node, side, skip_edge):
        out = []
        for k in live:
            if k == skip_edge:
                continue
            for end, at, n in (("exit", "exit_at", edges[k][0]), ("entry", "entry_at", edges[k][1])):
                if n == node and side_of(plan[k][end]) == side:
                    out.append(plan[k][at][0] if side in "tb" else plan[k][at][1])
        return out

    def attach(node, side, pos):
        x, y, w, h = boxes[node]
        f = fraction(pos, boxes[node], side, kinds.get(node), tb)
        return {"t": (f, 0), "b": (f, 1), "l": (0, f), "r": (1, f)}[side], {"t": (pos, y), "b": (pos, y + h), "l": (x, pos), "r": (x + w, pos)}[side]

    worst = sorted((i for i in live if not fan(i) and tangles(i, lines[i])), key=lambda i: -tangles(i, lines[i]))
    for i in worst[:TANGLE_TRIES]:
        a, b, _ = edges[i]
        p = plan[i]
        others = [v for k, v in solid.items() if k not in skip(a, b)]
        apart_lines = list(apart(i).values())
        best = (worth(i, lines[i]), None)
        now = (side_of(p["exit"]), side_of(p["entry"]))
        ca, cb = (boxes[a][0] + boxes[a][2] / 2, boxes[a][1] + boxes[a][3] / 2), (boxes[b][0] + boxes[b][2] / 2, boxes[b][1] + boxes[b][3] / 2)

        def nudge(node, side, port, edge, path, leaving):
            """A route that ends with a step of a few px sideways before its port: move the port onto the line instead,
            when there is room for it (inside the side, PITCH from its neighbours). -> (port, path)."""
            pts = [port[1]] + path if leaving else path + [port[1]]
            if len(pts) < 3:
                return port, path
            a_, b_, c_ = (pts[0], pts[1], pts[2]) if leaving else (pts[-1], pts[-2], pts[-3])      # the port, then the two waypoints nearest to it
            ax = 0 if side in "tb" else 1                    # the axis along the side
            if abs(b_[ax] - a_[ax]) > 0.5 or abs(c_[1 - ax] - b_[1 - ax]) > 0.5 or not 0.5 < abs(c_[ax] - b_[ax]) < NEAR:
                return port, path                           # the route meets the port on a clean line, or the step is a real turn
            want = c_[ax]
            lo, hi = _usable(boxes[node], side, kinds.get(node), tb)
            if not lo <= want <= hi or any(abs(want - t) < PITCH - 0.5 for t in taken(node, side, edge)):
                return port, path
            return attach(node, side, want), (pts[2:] if leaving else pts[:-2])

        def ports_at(node, side, far, edge_end):
            """Candidate ports on one side: where the edge is now, the middle, and the free place nearest to the other end."""
            box, out = boxes[node], []
            for want in (None, far[0] if side in "tb" else far[1]):
                pos = _free_port(box, side, kinds.get(node), tb, taken(node, side, i), want)
                if pos is not None and pos not in [o[0] for o in out]:
                    out.append((pos, attach(node, side, pos)))
            if side == now[0 if edge_end == "exit" else 1]:
                out.append((None, (p[edge_end], p[edge_end + "_at"])))
            return [o[1] for o in out]

        tries = ((es, et, pa, pb) for es, et in sorted(((x, y) for x in SIDES for y in SIDES), key=lambda m: m != now)     # where it is now first
                 for pa in ports_at(a, es, cb, "exit") for pb in ports_at(b, et, ca, "entry"))
        for es, et, pa, pb in tries:
            if meter[0] > TANGLE_STATES or best[0] <= 1:                 # out of budget, or nothing left to gain
                break
            path = route(pa[1], OUT[es], pb[1], OUT[et], others, apart_lines, TANGLE_ONE, meter)
            if path is None:
                continue
            pa, path = nudge(a, es, pa, i, path, True)
            pb, path = nudge(b, et, pb, i, path, False)
            path = _simplify([pa[1]] + path + [pb[1]])[1:-1]
            trial = dict(p, points=path, exit=pa[0], exit_at=pa[1], entry=pb[0], entry_at=pb[1])
            line = polyline(trial, boxes[a], boxes[b])
            score = worth(i, line)
            if score < best[0] and clear(line, others) and not _enters(line, boxes[a], boxes[b]):
                best = (score, (trial, line))
        if best[1]:
            plan[i].update({k: v for k, v in best[1][0].items() if k in ("points", "exit", "exit_at", "entry", "entry_at")})
            lines[i] = best[1][1]
