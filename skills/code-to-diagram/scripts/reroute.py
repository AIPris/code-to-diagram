"""Obstacle-avoiding routes for edges that would cut through a node.

draw.io routes an unpinned orthogonal edge by looking only at its two ends, so an edge between nodes in different groups
can run straight through a third node. For every edge whose route (as routing.route_edges and labels.polyline know it)
hits a node, search a grid built from the nodes' own edges for the cheapest orthogonal path that stays clear of all of
them (length plus a penalty per bend) and pin it with waypoints. Edges that are already clear are left alone, so moving
a shape in draw.io still re-routes them.
"""
import heapq

from labels import polyline
from metrics import _through

CLEAR = 6          # distance kept from every node the edge does not belong to
STUB = 16          # straight piece leaving and entering a node before the first turn
BEND = 60          # cost of a turn, in px of extra length
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


def route(p0, exit_dir, p1, entry_dir, obstacles):
    """Orthogonal path p0 -> p1 around obstacles (x, y, w, h), leaving p0 along exit_dir and arriving against entry_dir.
    Returns the inner corner points, or None when there is no clear path."""
    s = (p0[0] + exit_dir[0] * STUB, p0[1] + exit_dir[1] * STUB)
    e = (p1[0] + entry_dir[0] * STUB, p1[1] + entry_dir[1] * STUB)
    boxes = [(x - CLEAR, y - CLEAR, x + w + CLEAR, y + h + CLEAR) for x, y, w, h in obstacles]
    pts_x = [s[0], e[0], p0[0], p1[0]] + [v for b in boxes for v in (b[0], b[2])]
    pts_y = [s[1], e[1], p0[1], p1[1]] + [v for b in boxes for v in (b[1], b[3])]
    xs = sorted(set(pts_x) | {min(pts_x) - 40, max(pts_x) + 40})          # a lane around everything, so a way round always exists
    ys = sorted(set(pts_y) | {min(pts_y) - 40, max(pts_y) + 40})
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
    while heap and n < MAX_STATES:
        _, g, (i, j), d = heapq.heappop(heap)
        if g > best.get(((i, j), d), 1e18):
            continue
        n += 1
        if (i, j) == goal:
            pts, state = [], ((i, j), d)
            while True:
                pts.append((xs[state[0][0]], ys[state[0][1]]))
                if state not in parent:
                    break
                state = parent[state]
            pts.reverse()
            return _simplify([p0] + pts + [p1])[1:-1]
        for nd, (dx, dy) in enumerate(DIRS):
            ni, nj = i + dx, j + dy
            if (dx, dy) == (-DIRS[d][0], -DIRS[d][1]) or not (0 <= ni < len(xs) and 0 <= nj < len(ys)) or (ni, nj) in blocked_v:
                continue                                              # no U-turns
            if dx and (min(i, ni), j) in blocked_h or dy and (i, min(j, nj)) in blocked_s:
                continue
            cost = g + abs(xs[ni] - xs[i]) + abs(ys[nj] - ys[j]) + (BEND if nd != d else 0) + (BEND if (ni, nj) == goal and nd != d_end else 0)
            key = ((ni, nj), nd)
            if cost < best.get(key, 1e18):
                best[key] = cost
                parent[key] = ((i, j), d)
                heapq.heappush(heap, (cost + h(ni, nj), cost, (ni, nj), nd))
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
