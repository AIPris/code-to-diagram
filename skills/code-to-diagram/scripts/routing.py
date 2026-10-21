"""Edge routing on top of a layout: exit and entry side of every edge, ports, and the waypoints that pin its route.

    ports = route_edges(res.abs_box, edges, res.chains_abs, "TB")     # {edge idx: {exit, entry, points}}

layout.py decides where nodes and the corridors of long edges go. Here every edge gets its sides (straight down, an
L-shaped route leaving a node sideways, or through its corridor), ports along those sides (ports.py), and, where it
changes column between two ranks, its own track in the gap between them. draw.io then only has to draw straight pieces.
Work is done top-down (a left-right layout is the same picture turned over its diagonal).
"""
from collections import defaultdict

from metrics import SHARE, _through
from ports import MARGIN, NEAR, _far, _usable, assign, fraction
from reroute import fix as reroute_fix, untangle

LAND = 20      # a target this far past the source's edge is reached from the source's side (an L-shaped route)


def _hits(box, p, q):
    """Does the axis-parallel segment p-q run through box (x, y, w, h)?"""
    x, y, w, h = box
    return min(p[0], q[0]) < x + w and max(p[0], q[0]) > x and min(p[1], q[1]) < y + h and max(p[1], q[1]) > y


def _sides(s, t, solid):
    """Exit side of s and entry side of t (TB space; t may be a zero-size waypoint box) for an edge s -> t.
    solid: boxes an L-shaped route must not cross."""
    sx, sy, sw, sh = s
    tx, ty, tw, th = t
    if ty >= sy + sh - 1:                                    # target below
        side = "r" if tx >= sx + sw + LAND else "l" if tx + tw <= sx - LAND else None
        if side:
            y0, x0, x1 = sy + sh / 2, (sx + sw if side == "r" else sx), tx + tw / 2
            legs = [((x0, y0 - 1), (x1, y0 + 1)), ((x1 - 1, y0), (x1 + 1, ty))]
            if not any(_hits(b, p, q) for b in solid for p, q in legs):
                return side, "t"
        return "b", "t"
    if ty + th <= sy + 1:                                    # target above: a back edge, up and into the target's side
        side = "r" if sx >= tx + tw + LAND else "l" if sx + sw <= tx - LAND else None
        if side:
            x0, y1, x1 = sx + sw / 2, ty + th / 2, (tx + tw if side == "r" else tx)
            legs = [((x0 - 1, y1), (x0 + 1, sy)), ((min(x0, x1), y1 - 1), (max(x0, x1), y1 + 1))]
            if not any(_hits(b, p, q) for b in solid for p, q in legs):
                return "t", side
        return "t", "b"
    return ("r", "l") if tx + tw / 2 >= sx + sw / 2 else ("l", "r")


def _sideways(box, x, y_far, others, leaving):
    """Side ('r' or 'l') through which an edge leaves (or enters) `box` when its corridor, a column at x, lies beside the
    node and the way between them is free: sideways at the node's height, then along the column to y_far. One turn
    instead of two, and no stub hugging the node. None when the column is not beside the node or the way is blocked."""
    bx, by, bw, bh = box
    if x >= bx + bw + LAND:
        side, edge = "r", bx + bw
    elif x <= bx - LAND:
        side, edge = "l", bx
    else:
        return None
    for y in (by + MARGIN, by + bh - MARGIN):
        way = [(edge, y), (x, y), (x, y_far)]
        if not _clear(way if leaving else way[::-1], others):
            return None
    return side


SIDE_GAP = 24      # column of an edge that leaves a node through a side vertex, beside the node
FEW = {"diamond", "ellipse", "note"}      # shapes with room for a port or two per side


def _unshare(info, edges, B, few):
    """A diamond, a circle or a note has room for a port or two per side. Where several edges meet at its top or
    bottom they would crowd together. The edge nearest to straight keeps the side, arrivals give way before
    departures; the others use the side vertex facing their other end, through a short column beside the node (a
    station of their own), when that way is free."""
    groups = defaultdict(list)
    for idx, e in info.items():
        a, b, _ = edges[idx]
        if a in few and e["es"] in "tb":
            groups[(a, e["es"])].append((idx, "exit"))
        if b in few and e["et"] in "tb":
            groups[(b, e["et"])].append((idx, "entry"))
    for (n, side), lst in groups.items():
        if len(lst) < 2:
            continue
        x, y, w, h = B[n]
        far = lambda idx, end: info[idx]["nxt" if end == "exit" else "prv"]                    # box of the other end
        lst.sort(key=lambda m: (m[1] == "entry", abs(far(*m)[0] + far(*m)[2] / 2 - (x + w / 2))))
        for idx, end in lst[1:]:
            e, (fx, fy, fw, fh) = info[idx], far(idx, end)
            fx, fy = fx + fw / 2, fy + fh / 2
            down = fy > y + h / 2
            edge_y = y + h if down else y
            for to in ("r", "l") if fx >= x + w / 2 else ("l", "r"):
                col = x + w + SIDE_GAP if to == "r" else x - SIDE_GAP
                if _sideways(B[n], col, edge_y + (20 if down else -20), e["others"], end == "exit") is None:
                    continue
                if end == "exit":
                    e["es"], e["st"] = to, [[col, y + h / 2, edge_y]] + e["st"]
                    e["sideways"] = (True, e["sideways"][1])
                    e["nxt"] = (col, edge_y, 0, 0)
                else:
                    e["et"], e["st"] = to, e["st"] + [[col, edge_y, y + h / 2]]
                    e["sideways"] = (e["sideways"][0], True)
                    e["prv"] = (col, edge_y, 0, 0)
                break


FAN = 3      # edges that leave a node towards the same side, from this many on, share one stem and one trunk


def _fans(info, edges, B, labelled, kinds, styles):
    """Edges that leave one node downwards, none of them able to run straight, would each take a track of their own: a
    comb of parallel lines, long ones when a target is far away. From FAN of them on (per side) they leave through one
    port and share one horizontal run, the way a tree is drawn. An edge that can run straight keeps its own port, and
    labelled edges stay apart (a label must belong to one line), and so do edges of different style (solid, dashed,
    thick: on a shared line only one of them would show). Returns {edge idx: (node, 'l' or 'r', style)}."""
    groups = defaultdict(list)
    for idx, e in info.items():
        a, _, _ = edges[idx]
        if e["es"] not in "blr" or labelled[idx] or a == edges[idx][1] or kinds.get(a) in FEW:
            continue
        nx, ny, nw, nh = e["nxt"]
        if ny + nh / 2 <= B[a][1] + B[a][3] / 2:                       # towards a lower rank only
            continue
        lo, hi = _usable(B[a], "b", kinds.get(a), True)
        f0, f1 = _far(e["nxt"], "b", True)
        if f1 < lo or f0 > hi:
            groups[(a, "l" if f1 < lo else "r", styles[idx])].append(idx)
    out = {}
    for key, lst in groups.items():
        if len(lst) < FAN:
            continue
        for idx in lst:
            e = info[idx]
            if e["es"] in "lr":                                        # out of the bottom instead of a side
                e["es"], e["sideways"] = "b", (False, e["sideways"][1])
            out[idx] = key
    return out


def _stations(pts):
    """Corridor waypoints (two per rank crossed: entering and leaving its band) -> [(x, y_enter, y_leave)] in path order."""
    out = []
    for x, y in pts:
        if out and abs(out[-1][0] - x) < 0.5:
            out[-1][2] = y
        else:
            out.append([x, y, y])
    return out


CLEAR = 5      # a pinned route keeps this far from every node it does not belong to


def _clear(points, boxes):
    """True when no segment of the orthogonal polyline `points` passes through any of `boxes`."""
    return not any(_through((points[i], points[i + 1]), b, shrink=-CLEAR) for i in range(len(points) - 1) for b in boxes)


def _loop(s, others):
    """A self-loop on the side of the node that has room (right, left, below, above)."""
    x, y, w, h = s
    reach = 34
    options = [((1, 0.3), (1, 0.7), [(x + w + reach, y + 0.3 * h), (x + w + reach, y + 0.7 * h)]),
               ((0, 0.3), (0, 0.7), [(x - reach, y + 0.3 * h), (x - reach, y + 0.7 * h)]),
               ((0.3, 1), (0.7, 1), [(x + 0.3 * w, y + h + reach), (x + 0.7 * w, y + h + reach)]),
               ((0.3, 0), (0.7, 0), [(x + 0.3 * w, y - reach), (x + 0.7 * w, y - reach)])]
    for ex, en, pts in options:
        start, end = (x + ex[0] * w, y + ex[1] * h), (x + en[0] * w, y + en[1] * h)
        if _clear([start] + pts + [end], others):
            break
    return dict(exit=ex, entry=en, points=pts)


def _verticals(plan, edges):
    """Vertical pieces of the routes that no track decides: an L-shaped route runs down (or up) the column of the port
    it turns towards, and a straight one runs from port to port. [(x, y_from, y_to, edge idx)]."""
    out = []
    for idx, (a, b, _) in enumerate(edges):
        p = plan[idx]
        if a == b or "exit_at" not in p:
            continue
        (x0, y0), (x1, y1) = p["exit_at"], p["entry_at"]
        side_out, side_in = p["sides"][0] in "lr", p["sides"][1] in "lr"
        if p.get("routed"):
            if not p["stations"] and abs(x0 - x1) < 2:
                out.append((x0, min(y0, y1), max(y0, y1), idx))
        elif side_out != side_in:                            # an L: the vertical leg is at the port that is on a top or bottom side
            x = x1 if side_out else x0
            out.append((x, min(y0, y1), max(y0, y1), idx))
    return out


def _covers(x, y0, y1, fixed, idx):
    """Does a vertical line at x from y0 to y1 lie on top of a vertical piece of another route? idx: the route, or the
    set of routes of a bundle, which may share their pieces."""
    own = idx if isinstance(idx, (set, frozenset)) else {idx}
    return any(other not in own and abs(x - fx) < SHARE and min(max(y0, y1), hi) - max(min(y0, y1), lo) > 6 for fx, lo, hi, other in fixed)


def _on_top(y, cols, fixed, idx):
    """Would a column change at height y put the route on top of a vertical piece of another route? cols: the two
    columns (x before, x after) and where they are left and entered."""
    xa, xb, ya, yb = cols
    return _covers(xa, ya, y, fixed, idx) or _covers(xb, y, yb, fixed, idx)


def _dodge(p, idx, fixed, others):
    """The first and last corridor point of a route lie in the column the ports were asked for, but a port that had to
    make room for its neighbours sits a few px beside it. When that column would lie on top of another route over
    the stretch to the port, the corridor point moves to the port's column: the step happens a gap earlier."""
    st, (side_out, side_in) = p["stations"], p["sideways"]
    if not st:
        return
    for q, (x, y), sideways, first in ((st[0], p["exit_at"], side_out, True), (st[-1], p["entry_at"], side_in, False)):
        if sideways or not 0 < abs(x - q[0]) < NEAR:
            continue
        y0, y1 = (y, q[1]) if first else (q[2], y)                   # the stretch between the port and the corridor point
        if _covers(q[0], y0, y1, fixed, idx) and _clear([(x, q[1]), (x, q[2])], others):
            q[0] = x


def _share_trunks(runs, plan):
    """The first horizontal run of the edges of one fan (_fans) becomes a single run: one trunk, at one height, that all
    of them leave the stem by. Runs that cannot agree on a height (their gaps do not overlap) stay as they are."""
    trunks, out = defaultdict(list), []
    for r in runs:
        (idx, j), = r[5]
        key = plan[idx].get("bundle")
        if key and j == 0 and not plan[idx]["sideways"][0]:
            trunks[key].append(r)
        else:
            out.append(r)
    for rs in trunks.values():
        top, bot = max(r[2] for r in rs), min(r[3] for r in rs)
        if len(rs) == 1 or top > bot:
            out += rs
        else:
            out.append([min(r[0] for r in rs), max(r[1] for r in rs), top, bot, frozenset().union(*(r[4] for r in rs)),
                        [k for r in rs for k in r[5]], max(r[6] for r in rs), [c for r in rs for c in r[7]]])
    return out


def _square(p, others):
    """A route down one corridor column whose two ports lie in line: the column moves onto them, and the route is one
    straight line (instead of a corridor a few px beside it, which would be a kink at each end). Only if that stays clear."""
    st, (side_out, side_in) = p["stations"], p["sideways"]
    (x0, y0), (x1, y1) = p["exit_at"], p["entry_at"]
    if len(st) == 1 and not (side_out or side_in) and abs(x0 - x1) < 0.5 and 0.5 < abs(st[0][0] - x0) < NEAR             and _clear([(x0, y0), (x0, y1)], others):
        st[0][0] = x0


def _tracks(plan, B, edges, labelled, solid, skip):
    """Routes through corridors, and straight-down routes between two ranks, change column in the gap between ranks.
    Left to draw.io they all do it at the middle of the gap, on top of each other, or hugging a node's top edge. Each
    change of column is a horizontal run; give every run its own height inside its gap and pin it with waypoints. An
    edge that leaves or enters through a node's side turns at the height of its port instead. A pinned route must not
    cut through a node: if the first choice does, other heights are tried, and if none is clear the edge is left to
    draw.io's own routing."""
    runs, ranges, fixed = [], {}, _verticals(plan, edges)
    for idx, (a, b, _) in enumerate(edges):
        p = plan[idx]
        if a == b or "exit" not in p or not p.get("routed"):
            continue
        s, t = B[a], B[b]
        (x0, y0), (x1, y1) = p["exit_at"], p["entry_at"]
        _square(p, [box for k, box in solid.items() if k not in skip(a, b)])
        _dodge(p, idx, fixed, [box for k, box in solid.items() if k not in skip(a, b)])
        st, (side_out, side_in) = p["stations"], p["sideways"]
        xs = ([] if side_out else [x0]) + [q[0] for q in st] + ([] if side_in else [x1])
        leave = [y0] + [q[2] for q in st]                    # where each column is left / entered, for the legs that run
        enter = [q[1] for q in st] + [y1]                    # between two columns; a sideways start skips the first
        skipped = 1 if side_out else 0
        p["_legs"] = []
        for j in range(len(xs) - 1):
            if abs(xs[j] - xs[j + 1]) < 2:
                continue
            lo, hi = sorted((leave[j + skipped], enter[j + skipped]))
            ranges[(idx, j)] = (lo + 6, hi - 6)
            runs.append([min(xs[j], xs[j + 1]), max(xs[j], xs[j + 1]), lo + 6, hi - 6, frozenset({idx}), [(idx, j)], 18 if labelled[idx] else 9,
                         [(xs[j], xs[j + 1], leave[j + skipped], enter[j + skipped])]])
            p["_legs"].append((j, xs[j], xs[j + 1]))
        p["_ends"] = ((x0, y0), (x1, y1))
        p["_start"] = xs[0]
    placed, ys = [], {}
    for lo, hi, top, bot, who, keys, step, cols in sorted(_share_trunks(runs, plan), key=lambda r: (r[0], r[1])):
        mid = (top + bot) / 2
        pick = mid
        for k in range(14):
            y = mid + (-1) ** k * ((k + 1) // 2) * step
            if top <= y <= bot and not any(abs(y - py) < step - 1 and lo < phi and hi > plo for plo, phi, py in placed)                     and not any(_on_top(y, c, fixed, who) for c in cols):
                pick = y
                break
        placed.append((lo, hi, pick))
        for key in keys:
            ys[key] = pick
    for idx, p in plan.items():
        legs, ends, start = p.pop("_legs", []), p.pop("_ends", None), p.pop("_start", None)
        p.pop("stations", None)
        p.pop("routed", None)
        p.pop("sides", None)
        side_out, side_in = p.pop("sideways", (False, False))
        if not (legs or side_out or side_in):
            continue
        a, b, _ = edges[idx]
        others = [box for k, box in solid.items() if k not in skip(a, b)]
        height = {j: ys[(idx, j)] for j, _, _ in legs}

        def route():
            pts, cur = [(start, ends[0][1])] if side_out else [], start      # a sideways start turns at its port's height
            for j, _, xb in legs:
                pts += [(cur, height[j]), (xb, height[j])]
                cur = xb                                     # a column change too small to draw keeps the previous column
            return pts + ([(cur, ends[1][1])] if side_in else [])

        for leg in list(legs):                               # a step of less than two lanes is not worth two bends: stay on the column if the way is free
            if abs(leg[2] - leg[1]) < NEAR:
                legs.remove(leg)
                on_port = side_in or abs((legs[-1][2] if legs else start) - ends[1][0]) < 2        # the route must still end at its port
                if not on_port or not _clear([ends[0]] + route() + [ends[1]], others):
                    legs.insert(0, leg)
                    legs.sort()

        if not _clear([ends[0]] + route() + [ends[1]], others):
            for j, xa, xb in legs:                           # look for a free height, nearest to the first choice first
                lo, hi = ranges[(idx, j)]
                tries = sorted((lo + k * 8 for k in range(int(max(hi - lo, 0) // 8) + 1)), key=lambda y: abs(y - height[j]))
                for y in tries:
                    height[j] = y
                    if _clear([ends[0]] + route() + [ends[1]], others):
                        break
            if not _clear([ends[0]] + route() + [ends[1]], others):
                continue                                     # nothing is clear: draw.io's own routing, reported by metrics
        p["points"] += route()


def route_edges(boxes, edges, chains, direction="TB", parents=None, labelled=None, kinds=None, styles=None, tidy=False):
    """For every edge (src, tgt, rev): {exit, entry: (fx, fy) anchor fractions, points: absolute waypoints}.
    Ends that share a node side are spread along it (ports.assign) and line up with the end they connect to, so edges run straight.
    parents {id: container id}: containers other than an edge's own ancestors count as obstacles, like nodes.
    labelled: per edge, does it carry a label. kinds {node: shape kind}: where on its sides an edge can attach (ports.half).
    styles: per edge, whatever makes it look different (line, arrowheads): only edges that look alike share a trunk.
    tidy: also give edges that cross others a second route (reroute.untangle), which takes longer."""
    tb = direction in ("TB", "TD", "BT")
    flip = (lambda b: b) if tb else (lambda b: (b[1], b[0], b[3], b[2]))
    pt = (lambda p: p) if tb else (lambda p: (p[1], p[0]))
    B = {i: flip(b) for i, b in boxes.items()}
    parents = parents or {}
    kinds = kinds or {}

    def skip(a, b):                                        # the two ends and the containers around them
        out = {a, b}
        for i in (a, b):
            while parents.get(i):
                i = parents[i]
                out.add(i)
        return out

    solid = B
    labelled = labelled or [False] * len(edges)
    styles = styles or [None] * len(edges)
    plan, info, ends = {}, {}, defaultdict(list)
    for idx, (a, b, _) in enumerate(edges):
        s, t = B[a], B[b]
        if a == b:
            plan[idx] = _loop(s, [v for k, v in solid.items() if k not in skip(a, a)])
            continue
        pts = [pt(p) for p in chains.get(idx, [])]
        st = _stations(pts)
        others = [v for k, v in solid.items() if k not in skip(a, b)]
        sideways = (False, False)
        if st:                                               # through corridors: down (or up) the column, out of and into a side if it is beside the node
            down = st[0][1] > s[1] + s[3] / 2
            es, et = ("b", "t") if down else ("t", "b")
            out, into = _sideways(s, st[0][0], st[0][1], others, True), _sideways(t, st[-1][0], st[-1][2], others, False)
            es, et, sideways = out or es, into or et, (bool(out), bool(into))
            nxt, prv = (st[0][0], st[0][1], 0, 0), (st[-1][0], st[-1][2], 0, 0)
        else:
            es, et = _sides(s, t, others)
            nxt, prv = t, s
        info[idx] = dict(es=es, et=et, st=st, sideways=sideways, nxt=nxt, prv=prv, others=others)
    _unshare(info, edges, B, {n for n, k in kinds.items() if k in FEW})
    fans = _fans(info, edges, B, labelled, kinds, styles)
    for idx, e in info.items():
        a, b, _ = edges[idx]
        plan[idx] = dict(points=[], stations=e["st"], routed=bool(e["st"]) or (e["es"] in "tb" and e["et"] in "tb"), sideways=e["sideways"],
                         sides=(e["es"], e["et"]), bundle=fans.get(idx))
        ends[(a, e["es"])].append((idx, "exit", e["nxt"]))
        ends[(b, e["et"])].append((idx, "entry", e["prv"]))
    where = assign(B, ends, kinds, tb, {(idx, "exit"): key for idx, key in fans.items()})
    for (n, side), lst in ends.items():
        x, y, w, h = B[n]
        for idx, kind, _ in lst:
            pos = where[(idx, kind)]
            f = fraction(pos, B[n], side, kinds.get(n), tb)
            plan[idx][kind] = {"t": (f, 0), "b": (f, 1), "l": (0, f), "r": (1, f)}[side]
            plan[idx][kind + "_at"] = {"t": (pos, y), "b": (pos, y + h), "l": (x, pos), "r": (x + w, pos)}[side]     # where the edge really attaches
    _tracks(plan, B, edges, labelled, solid, skip)
    reroute_fix(plan, B, edges, solid, skip)
    if tidy:
        untangle(plan, B, edges, solid, skip, kinds, tb)
    if not tb:
        for p in plan.values():
            for k in ("exit", "entry"):
                if k in p:
                    p[k] = (p[k][1], p[k][0])
                    if k + "_at" in p:
                        p[k + "_at"] = (p[k + "_at"][1], p[k + "_at"][0])
            p["points"] = [(q[1], q[0]) for q in p["points"]]
    return plan
