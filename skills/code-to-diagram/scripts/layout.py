"""Layered (Sugiyama-style) layout for nested graphs, stdlib only.

    from layout import layout
    res = layout(sizes, parent, groups, edges, "TB")   # positions + container sizes + corridors of long edges

Why it exists: asking a model to place 30 boxes by hand costs thousands of output tokens and still overlaps.
This computes ranks, node order, coordinates, container sizes and the corridors long edges take between ranks. Routing the edges is routing.py.
"""
import math
from collections import defaultdict

from ports import half, pav

PAD, HEADER = 24, 30            # container inner margin / title bar
GAP_O, GAP_R = 40, 56           # gap between nodes in a rank / between ranks
DUMMY_W = 12                    # width reserved for an edge corridor passing through a rank: parallel edges run PITCH apart
CORRIDOR_GAP = 18               # clear space between a corridor and a node beside it
CORRIDOR_SLACK = 8              # of which a corridor straightened later may give up
MAX_O = {"TB": 1800, "LR": 1100}  # wrap a rank into rows past this extent along the order axis


class Layout:
    def __init__(self):
        self.rel = {}           # id -> (x, y) relative to the parent container's top-left
        self.size = {}          # id -> (w, h), containers included
        self.abs_box = {}       # id -> (x, y, w, h) absolute
        self.chains_abs = {}    # edge index -> [(x, y)] absolute waypoints through the ranks it spans
        self.extent = (0, 0)


# ---- ranking ------------------------------------------------------------------------------------------------------
def _acyclic(ids, edges):
    """edges [(a, b, rev, idx)] -> [(u, v, idx, flipped)] with u ranked before v and no cycles (DFS back edges flip)."""
    adj = defaultdict(list)
    for a, b, rev, idx in edges:
        u, v = (b, a) if rev else (a, b)
        adj[u].append((v, idx, rev))
    indeg = defaultdict(int)
    for u in adj:
        for v, _, _ in adj[u]:
            indeg[v] += 1
    state, flipped = {}, set()

    def visit(root):
        stack = [(root, iter(adj[root]))]
        state[root] = 1
        while stack:
            u, it = stack[-1]
            for v, idx, _ in it:
                if state.get(v) == 1:
                    flipped.add(idx)
                elif v not in state:
                    state[v] = 1
                    stack.append((v, iter(adj[v])))
                    break
            else:
                state[u] = 2
                stack.pop()

    for n in sorted(ids, key=lambda n: indeg[n] > 0):     # sources first, declaration order kept (sort is stable)
        if n not in state:
            visit(n)
    out = []
    for a, b, rev, idx in edges:
        u, v = (b, a) if rev else (a, b)
        out.append((v, u, idx, True) if idx in flipped else (u, v, idx, False))
    return out


def _longest_path(ids, rank_edges):
    succ, pred = defaultdict(set), defaultdict(set)
    for u, v, _, _ in rank_edges:
        succ[u].add(v)
        pred[v].add(u)
    rank, todo = {n: 0 for n in ids}, [n for n in ids if not pred[n]]
    seen = defaultdict(int)
    while todo:
        u = todo.pop()
        for v in succ[u]:
            rank[v] = max(rank[v], rank[u] + 1)
            seen[v] += 1
            if seen[v] == len(pred[v]):
                todo.append(v)
    for u in sorted(ids, key=lambda n: -rank[n]):          # pull sources down next to their nearest successor
        if not pred[u] and succ[u]:
            rank[u] = min(rank[v] for v in succ[u]) - 1
    low = min(rank.values(), default=0)
    return {n: r - low for n, r in rank.items()}


def _side_leaves(rank, rank_edges):
    """A decision with exactly two children, one of them a dead end (an error exit, a final state), draws that leaf beside
    it in the same row, as people do by hand. Mutates rank; returns {leaf: parent}."""
    kids, parents = defaultdict(list), defaultdict(int)
    for u, v, _, _ in rank_edges:
        kids[u].append(v)
        parents[v] += 1
    flat = {}
    for p, vs in kids.items():
        if len(vs) == 2 and len(set(vs)) == 2:
            leaves = [v for v in vs if v not in kids and parents[v] == 1]
            if len(leaves) == 1 and (vs[0] in kids or vs[1] in kids):
                flat[leaves[0]] = p
                rank[leaves[0]] = rank[p]
    return flat


def _layers(rank, rank_edges):
    """Adjacent-rank links with dummy nodes on long edges. Returns layers, links, chains{idx: [dummy..]} (u->v order)."""
    n_layers = max(rank.values(), default=-1) + 1
    layers, links, chains = [[] for _ in range(n_layers)], [], {}
    for n, r in rank.items():
        layers[r].append(n)
    for u, v, idx, _ in rank_edges:
        chain, prev = [], u
        for r in range(rank[u] + 1, rank[v]):
            d = ("~", idx, r)
            layers[r].append(d)
            links.append((prev, d))
            chain.append(d)
            prev = d
        links.append((prev, v))
        chains[idx] = chain
    return layers, links, chains


def _spine(layers, links):
    """Preferred child of every node (the one that continues the longest path): flows read best when it sits straight
    below its parent. Returns {parent: child}."""
    down = defaultdict(list)
    for u, v in links:
        down[u].append(v)
    height = {}
    for L in reversed(layers):
        for n in L:
            height[n] = 1 + max((height[v] for v in down.get(n, ())), default=-1)
    return {u: max(vs, key=lambda v: height[v]) for u, vs in down.items() if vs}       # max() keeps the first on ties


# ---- ordering -----------------------------------------------------------------------------------------------------
def _crossings(layers, links, pos, lay):
    by = defaultdict(list)
    for u, v in links:
        by[lay[u]].append((pos[u], pos[v]))
    total = 0
    for pairs in by.values():
        for i in range(len(pairs)):
            for j in range(i + 1, len(pairs)):
                if (pairs[i][0] - pairs[j][0]) * (pairs[i][1] - pairs[j][1]) < 0:
                    total += 1
    return total


def _order(layers, links, flat, sweeps=14):
    """Barycenter sweeps, keeping the order with the fewest crossings. Mutates and returns layers."""
    up, down = defaultdict(list), defaultdict(list)
    for u, v in links:
        down[u].append(v)
        up[v].append(u)
    lay = {n: r for r, L in enumerate(layers) for n in L}
    pos = {}

    def reindex():
        for L in layers:
            for i, n in enumerate(L):
                pos[n] = i

    spine = set(_spine(layers, links).values())

    def sort_layer(L, nb):
        reindex()
        L.sort(key=lambda n: (sum(pos[m] for m in nb[n]) / len(nb[n]) if nb[n] else pos[n], n not in spine, pos[n]))
        beside(L)

    def beside(L):                                           # a side leaf sits right next to its parent
        for leaf in [n for n in flat if n in L]:
            L.remove(leaf)
        for leaf, p in flat.items():
            if p in L:
                L.insert(L.index(p) + 1, leaf)

    for L in layers:
        beside(L)
    reindex()
    for r in range(1, len(layers)):                          # initial order: follow the parents
        sort_layer(layers[r], up)
    reindex()
    best, best_x = [list(L) for L in layers], _crossings(layers, links, pos, lay)
    for it in range(sweeps):
        if best_x == 0:
            break
        if it % 2 == 0:
            for r in range(1, len(layers)):
                sort_layer(layers[r], up)
        else:
            for r in range(len(layers) - 2, -1, -1):
                sort_layer(layers[r], down)
        reindex()
        x = _crossings(layers, links, pos, lay)
        if x < best_x:
            best, best_x = [list(L) for L in layers], x
    for L, B in zip(layers, best):
        L[:] = B
    return layers


# ---- coordinates --------------------------------------------------------------------------------------------------
def _coords(layers, links, so, sr, tb, gap, flat, kinds):
    up, down = defaultdict(list), defaultdict(list)
    for u, v in links:
        down[u].append(v)
        up[v].append(u)
    spine = _spine(layers, links)
    kept = set(spine.values())
    link_w = lambda u, v: 1000 if spine.get(u) == v else 1       # spine links pull nodes into a straight line
    c = {}
    for L in layers:
        x = 0.0
        for n in L:
            c[n] = x + so[n] / 2
            x += so[n] + gap(n, None)

    pieces = defaultdict(list)                                # the corridor points of each long edge
    for L in layers:
        for n in L:
            if isinstance(n, tuple):
                pieces[n[1]].append(n)

    def settle(L, nbs):
        if not L:
            return
        want = []
        for n in L:
            nb = nbs(n)
            tot = sum(w for _, w in nb)
            if n in flat:
                want.append(c[flat[n]] + (so[flat[n]] + so[n]) / 2 + GAP_O)
            elif isinstance(n, tuple):                                  # a long edge is one rigid line: every point wants the mean
                want.append(sum(c[m] for m in pieces[n[1]]) / len(pieces[n[1]]))
            else:
                want.append(sum(c[m] * w for m, w in nb) / tot if nb else c[n])
        weight = [(3 if isinstance(n, tuple) else 1) * (1000 if n in kept or n in flat or isinstance(n, tuple) else 1) for n in L]   # spines, corridors, side leaves
        seps = [(so[a] + so[b]) / 2 + gap(a, b) for a, b in zip(L, L[1:])]
        for n, p in zip(L, pav(want, weight, seps)):
            c[n] = p

    ups = lambda n: [(m, link_w(m, n)) for m in up[n]]
    downs = lambda n: [(m, link_w(n, m)) for m in down[n]]
    for _ in range(4):
        for L in layers[1:]:
            settle(L, ups)
        for L in reversed(layers[:-1]):
            settle(L, downs)
    for L in layers:
        settle(L, lambda n: ups(n) + downs(n))
    for u, v in spine.items():                                # sub-pixel leftovers of the weighted fit become exact
        if abs(c[u] - c[v]) < 1:
            c[v] = c[u]
    _straighten(layers, up, down, c, so, sr, tb, gap, kinds)
    return c


def _straighten(layers, up, down, c, so, sr, tb, gap, kinds):
    """Put every long edge on one line: all the corridor points of an edge get the same coordinate when the room between
    their neighbours allows it. Of the possible lines the one inside the extent of the edge's source and target is
    preferred: there the edge leaves or enters its node without a bend."""
    chains = defaultdict(list)
    for r, L in enumerate(layers):
        for n in L:
            if isinstance(n, tuple):
                chains[n[1]].append((r, n))
    def reach(n):                                             # where along a node an edge can attach
        h = half(kinds.get(n), so[n], sr[n], tb)
        return c[n] - h, c[n] + h

    tight = lambda a, b: gap(a, b) - (CORRIDOR_SLACK if isinstance(a, tuple) != isinstance(b, tuple) else 0)   # a straight corridor may pass a node a little closer
    for _ in range(3):
        for idx, chain in sorted(chains.items(), key=lambda kv: -len(kv[1])):
            lo, hi = -math.inf, math.inf
            for r, d in chain:
                L = layers[r]
                i = L.index(d)
                if i > 0:
                    lo = max(lo, c[L[i - 1]] + (so[L[i - 1]] + so[d]) / 2 + tight(L[i - 1], d))
                if i < len(L) - 1:
                    hi = min(hi, c[L[i + 1]] - (so[L[i + 1]] + so[d]) / 2 - tight(d, L[i + 1]))
            if lo > hi:
                continue
            near = sorted(c[d] for _, d in chain)[len(chain) // 2]
            ends = [reach(m) for m in (up[chain[0][1]][0], down[chain[-1][1]][0])]
            away = lambda t: sum(max(a - t, t - b, 0) for a, b in ends)           # how far t lies outside the two ends
            best = min((min(max(t, lo), hi) for t in [near] + [v for a, b in ends for v in (a, b)]), key=lambda t: (round(away(t)), abs(t - near)))
            for _, d in chain:
                c[d] = best


# ---- one level ----------------------------------------------------------------------------------------------------
def _layered(ids, size, edges, direction, gap_r, side_leaves, kinds=()):
    """Lay out one level. ids in declaration order, size[id] = (w, h), edges [(a, b, rev, idx)].
    Returns pos{id: (x, y)} top-left, (W, H), chains{idx: [(x, y), ...]} in real path order a -> b."""
    tb = direction in ("TB", "TD", "BT")
    mirror = direction in ("BT", "RL")
    so = {n: size[n][0] if tb else size[n][1] for n in ids}
    sr = {n: size[n][1] if tb else size[n][0] for n in ids}
    so_of = lambda n: DUMMY_W if isinstance(n, tuple) else so[n]
    sr_of = lambda n: 0 if isinstance(n, tuple) else sr[n]
    gap = lambda a, b: (0 if isinstance(a, tuple) and isinstance(b, tuple) else CORRIDOR_GAP) if isinstance(a, tuple) or isinstance(b, tuple) else GAP_O

    rank_edges = _acyclic(ids, edges)
    rank = _longest_path(ids, rank_edges)
    flat = _side_leaves(rank, rank_edges) if side_leaves else {}
    rank_edges = [e for e in rank_edges if flat.get(e[1]) != e[0]]
    layers, links, chains = _layers(rank, rank_edges)
    _order(layers, links, flat)
    max_o = MAX_O["TB" if tb else "LR"]
    rows = {}
    for r, L in enumerate(layers):                           # wrap a rank that is wider than the page into rows
        real = [n for n in L if not isinstance(n, tuple)]
        k = max(1, math.ceil(sum(so[n] + GAP_O for n in real) / max_o))
        for i, n in enumerate(real):
            rows[n] = (r, i * k // len(real))
    if any(row for _, row in rows.values()):
        base, shift = {}, 0
        for r, L in enumerate(layers):
            base[r] = shift
            shift += max((rows[n][1] for n in L if n in rows), default=0) + 1
        hint = {n: i for L in layers for i, n in enumerate(L)}
        rank = {n: base[r] + row for n, (r, row) in rows.items()}
        layers, links, chains = _layers(rank, rank_edges)
        for L in layers:
            L.sort(key=lambda n: hint.get(n, 0))
        _order(layers, links, flat)

    c = _coords(layers, links, {n: so_of(n) for L in layers for n in L}, {n: sr_of(n) for L in layers for n in L}, tb, gap, flat, kinds)
    top, band, y = [], [], 0.0
    for L in layers:
        band.append(max((sr_of(n) for n in L), default=0))
        top.append(y)
        y += band[-1] + gap_r
    R = max(y - gap_r, 0)
    lo = min((c[n] - so_of(n) / 2 for L in layers for n in L), default=0)
    hi = max((c[n] + so_of(n) / 2 for L in layers for n in L), default=0)

    def to_xy(o, r):                                         # (order axis, rank axis) -> page (x, y)
        if mirror:
            r = R - r
        return (o, r) if tb else (r, o)

    pos, where = {}, {}
    for r, L in enumerate(layers):
        for n in L:
            where[n] = r
            if not isinstance(n, tuple):
                rt = top[r] + (band[r] - sr[n]) / 2
                pos[n] = to_xy(c[n] - so[n] / 2 - lo, rt + (sr[n] if mirror else 0))
    real_src = {idx: a for a, _, _, idx in edges}
    out = {}
    for u, _, idx, _ in rank_edges:
        pts = []
        for d in chains.get(idx, []):
            r = where[d]
            for rr in ((top[r], top[r] + band[r]) if band[r] else (top[r],)):
                pts.append(to_xy(c[d] - lo, rr))
        if pts and u != real_src[idx]:
            pts.reverse()
        out[idx] = pts
    return pos, ((hi - lo, R) if tb else (R, hi - lo)), out


def _grid(ids, size, max_o):
    """Pack nodes that have no edges into a near-landscape grid."""
    n = len(ids)
    w = max(size[i][0] for i in ids) + GAP_O
    h = max(size[i][1] for i in ids) + 24
    cols = max(1, min(n, round(math.sqrt(1.6 * n * h / w))))
    cols = min(cols, max(1, int(max_o // w)))
    pos = {i: ((k % cols) * w, (k // cols) * h) for k, i in enumerate(ids)}
    rows = math.ceil(n / cols)
    return pos, (cols * w - GAP_O, rows * h - 24)


# ---- nested levels ------------------------------------------------------------------------------------------------
def layout(sizes, parent, groups, edges, direction="TB", label_w=0, side_leaves=False, kinds=()):
    """sizes {id: (w, h)} for leaf nodes; parent {id: group id | None} for nodes and groups, in declaration order;
    groups = {container id: minimum width (title)}; edges [(src, tgt, rev)] (rev: rank tgt before src, e.g. UML inheritance).
    Returns a Layout with absolute boxes for every node and container."""
    res = Layout()
    res.size = dict(sizes)
    children = defaultdict(list)
    for i, p in parent.items():
        children[p].append(i)
    tb = direction in ("TB", "TD", "BT")
    # labels sit on the edge inside the gap between ranks: they need height in a top-down layout, their full width in a left-right one
    gap_r = (GAP_R + (14 if label_w else 0)) if tb else min(max(GAP_R, label_w + 44), 240)
    chains_rel = {}

    def ancestor(x, kids):
        while x is not None and x not in kids:
            x = parent.get(x)
        return x

    def level(gid):
        kids = children[gid]
        for k in kids:
            if k in groups:
                level(k)
        kidset = set(kids)
        lifted, direct, seen = [], {}, {}
        for idx, (a, b, rev) in enumerate(edges):
            la, lb = ancestor(a, kidset), ancestor(b, kidset)
            if la is None or lb is None or la == lb:
                continue
            key = (la, lb, rev)
            if key not in seen:
                seen[key] = idx
                lifted.append((la, lb, rev, idx))
            if (la, lb) == (a, b):
                direct[idx] = seen[key]
        linked = {x for la, lb, _, _ in lifted for x in (la, lb)}
        loose = [k for k in kids if k not in linked]
        if linked and len(loose) < 3 and not any(k in groups for k in loose):   # a couple of unlinked nodes stay in the flow
            main, loose = kids, []
        else:
            main = [k for k in kids if k in linked]
        pos, W, H, chains = {}, 0, 0, {}
        if main:
            pos, (W, H), chains = _layered(main, res.size, lifted, direction, gap_r, side_leaves, kinds)
        if loose:                                            # nodes without edges: a compact grid below the flow
            gpos, (gw, gh) = _grid(loose, res.size, MAX_O["TB" if direction in ("TB", "TD", "BT") else "LR"])
            dy = H + (GAP_R if main else 0)
            for k, (x, y) in gpos.items():
                pos[k] = (x + max(W - gw, 0) / 2, y + dy)
            W, H = max(W, gw), dy + gh
        dx, dy = (PAD, HEADER + PAD) if gid is not None else (0, 0)
        for k in kids:
            res.rel[k] = (pos[k][0] + dx, pos[k][1] + dy)
        for idx, key_idx in direct.items():
            if key_idx in chains and chains[key_idx]:
                chains_rel[idx] = (gid, [(x + dx, y + dy) for x, y in chains[key_idx]])
        if gid is not None:
            res.size[gid] = (max(W + 2 * PAD, groups[gid], 160), H + HEADER + 2 * PAD) if kids else (max(groups[gid], 160), 70)
        else:
            res.extent = (W, H)

    level(None)
    origin = {None: (0, 0)}

    def absolute(i):
        if i in origin:
            return origin[i]
        px, py = absolute(parent.get(i))
        x, y = px + res.rel[i][0], py + res.rel[i][1]
        origin[i] = (x, y)
        return origin[i]

    for i in parent:
        x, y = absolute(i)
        res.abs_box[i] = (x, y, *res.size[i])
    for idx, (gid, pts) in chains_rel.items():
        ox, oy = absolute(gid) if gid is not None else (0, 0)
        res.chains_abs[idx] = [(ox + x, oy + y) for x, y in pts]
    return res
