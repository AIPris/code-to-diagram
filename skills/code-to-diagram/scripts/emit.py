"""Model (mermaid.py) -> Diagram (drawio_lib.py), with positions from layout.py / sequence.py."""
import re

import sequence
from drawio_lib import CHAR_W, LINE_H, PALETTE, Diagram, class_height, text_width, wrap_lines
import labels
import metrics
from layout import HEADER, layout
from ports import widen
from routing import route_edges

M = 40
STRAIGHTEN_MAX = 120      # nodes up to which the straightened variant of each layout is tried as well
KIND_OF = {"diamond": "diamond", "circle": "ellipse", "start": "ellipse", "end": "ellipse", "stadium": "stadium", "para": "para",
           "cylinder": "cylinder", "note": "note"}     # shapes whose outline decides where an edge can attach (ports.half)
SHAPE_OF = {"rect": "rounded", "round": "rounded", "stadium": "stadium", "circle": "ellipse", "diamond": "diamond",
            "cylinder": "cylinder", "para": "para", "note": "note", "start": "start", "end": "end", "fork": "fork"}


def _hex(v):
    v = v.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", v):
        v = "#" + "".join(c * 2 for c in v[1:])
    return v


def look(model, n):
    """(palette name, fill, stroke, extra style) of a node, from its class, `style` line or shape default."""
    props = dict(model.classes.get(n.cls, {}))
    name = n.cls if n.cls in PALETTE else "yellow" if n.shape in ("diamond", "note") else "gray" if n.shape == "group" else "blue"
    fill = _hex(n.fill or props.get("fill", "")) or None
    stroke = _hex(n.stroke or props.get("stroke", "")) or None
    extra = ""
    if props.get("stroke-width"):
        extra += f"strokeWidth={re.sub(r'[^0-9.]', '', props['stroke-width']) or 1};"
    if props.get("color"):
        extra += f"fontColor={_hex(props['color'])};"
    if props.get("stroke-dasharray"):
        extra += "dashed=1;"
    return name, fill, stroke, extra


def node_size(n, direction):
    tb = direction in ("TB", "TD", "BT")
    if n.shape == "start":
        return 24, 24
    if n.shape == "end":
        return 28, 28
    if n.shape == "fork":
        return (90, 8) if tb else (8, 90)
    if n.shape == "class":
        rows = n.members + n.methods + [n.label] + ([f"«{n.stereotype}»"] if n.stereotype else [])
        return max(160, max(text_width(r) for r in rows) + 28), class_height(n.members, n.methods, n.stereotype)
    lines = wrap_lines(n.label, 34 if n.shape == "note" else 28)
    tw, th = max(text_width(l) for l in lines), len(lines) * LINE_H
    if n.shape == "diamond":
        return max(130, tw * 1.55 + 30), max(72, th * 2 + 28)
    if n.shape == "circle":
        s = max(64, tw + 28, th + 28)
        return s, s
    if n.shape == "stadium":
        return max(110, tw + 44), max(44, th + 22)
    if n.shape == "cylinder":
        return max(110, tw + 32), max(64, th + 44)
    if n.shape == "para":
        return max(120, tw + 56), max(48, th + 22)
    return max(110, tw + 30), max(46, th + 24)


def edge_style(e):
    """(kind preset, extra style) for an edge from flowchart/state options."""
    kind, extra = e.kind, ""
    if e.head == "cross":
        extra += "endArrow=cross;endFill=0;"
    elif e.head == "oval":
        extra += "endArrow=oval;endFill=1;"
    if e.both and kind == "flow":
        extra += "startArrow=classic;"
    if e.line == "dotted":
        extra += "dashed=1;"
    elif e.line == "thick":
        extra += "strokeWidth=3;"
    return kind, extra


def legend(d, model, x, y):
    """Legend under the diagram for `%% legend: key=meaning` entries (key = a class or colour name). Returns its height."""
    rows = []
    for key, meaning in model.legend:
        props = model.classes.get(key, {})
        fill, stroke = PALETTE.get(key, PALETTE["blue"])
        rows.append((meaning, _hex(props.get("fill", "")) or fill, _hex(props.get("stroke", "")) or stroke))
    return d.legend(x, y, rows, w=max(200, max(text_width(m, 6.6) for m, _, _ in rows) + 30))


def build(model):
    """-> (Diagram, (width, height))"""
    if model.kind == "sequence":
        d, size = sequence.build(model)
    else:
        d, size = _graph(model)
    return d, size


def _lay_out(model, direction, gname, edge_text, flatten=False, tidy=False):
    """One complete layout in the given direction -> (result, plan, label spots, quality metrics)."""
    resolve = lambda i: i if i in model.nodes else gname.get(i, i)
    sizes = {i: node_size(n, direction) for i, n in model.nodes.items()}
    edges = [(resolve(e.src), resolve(e.tgt), e.kind in ("inherit", "realize")) for e in model.edges]
    kinds = {i: KIND_OF[n.shape] for i, n in model.nodes.items() if n.shape in KIND_OF}
    widen(sizes, edges, direction in ("TB", "TD", "BT"), kinds)
    parent = {gname[g]: (gname[grp.parent] if grp.parent else None) for g, grp in model.groups.items()}
    parent.update({i: (gname[n.parent] if n.parent else None) for i, n in model.nodes.items()})
    groups = {gname[g]: text_width(grp.label, 8.4) + 40 for g, grp in model.groups.items()}
    label_w = max((text_width(t, 6.6) for t in edge_text if t), default=0)
    res = layout(sizes, parent, groups, edges, direction, label_w=label_w, side_leaves=model.kind in ("flow", "state"), kinds=kinds, flatten=flatten)
    plan = route_edges(res.abs_box, edges, res.chains_abs, direction, parents=parent, labelled=[bool(t) for t in edge_text], kinds=kinds,
                       styles=[edge_style(e) for e in model.edges], tidy=tidy)
    lines = labels.lines_of(plan, res.abs_box, edges)
    spots, touching = labels.place(lines, edges, edge_text, res.abs_box, model.nodes)
    titles = [(x + (w - groups[g]) / 2 + 20, y, groups[g] - 40, HEADER) for g, (x, y, w, _) in ((g, res.abs_box[g]) for g in groups)]    # centred title text
    quality = metrics.evaluate(lines, res.abs_box, edges, list(model.nodes), touching, res.extent, {i: p.get("bundle") for i, p in plan.items()}, titles,
                              [i for i, n in model.nodes.items() if n.shape == "diamond"])
    return res, plan, spots, quality, parent, edges


def _graph(model):
    gname = {g: (g if g not in model.nodes else g + "_grp") for g in model.groups}
    resolve = lambda i: i if i in model.nodes else gname.get(i, i)
    edge_text = ["\n".join(wrap_lines(e.label, 24)) if e.label else "" for e in model.edges]
    tried = [model.direction] if model.direction else ["TB", "LR"]
    flats = (False, True) if len(model.nodes) <= STRAIGHTEN_MAX else (False,)         # a huge graph takes too long to lay out twice more
    runs = [(_lay_out(model, d, gname, edge_text, flat), d, flat) for d in tried for flat in flats]     # the layout that measures best wins
    best, direction, flat = min(runs, key=lambda r: r[0][3]["score"])
    if best[3]["crossings"] or best[3]["shared"]:                                  # only the winner is worth the slower search for better routes
        again = _lay_out(model, direction, gname, edge_text, flat, tidy=True)
        best = min(best, again, key=lambda r: r[3]["score"])
    res, plan, spots, quality, parent, edges = best
    model.layout_report = dict(quality, direction=direction, auto=len(tried) > 1)
    W, H = res.extent
    d = Diagram()
    off = lambda p: M if not p else 0

    for g, grp in model.groups.items():
        gid = gname[g]
        x, y = res.rel[gid]
        w, h = res.size[gid]
        p = parent[gid]
        d.container(gid, grp.label, x + off(p), y + off(p), w, h, parent=p or "1")
    for i, n in model.nodes.items():
        x, y = res.rel[i]
        w, h = res.size[i]
        p = parent[i]
        color, fill, stroke, extra = look(model, n)
        if n.shape == "class":
            d.class_box(i, n.label, n.members, x + off(p), y + off(p), w, n.methods, n.stereotype, color, p or "1", fill=fill, stroke=stroke)
        else:
            label = "" if n.shape in ("start", "end", "fork") else "\n".join(wrap_lines(n.label, 34 if n.shape == "note" else 28))
            d.box(i, label, x + off(p), y + off(p), w, h, SHAPE_OF.get(n.shape, "rounded"), color, p or "1", fill=fill, stroke=stroke,
                  style=extra + ("aspect=fixed;" if n.shape == "circle" else ""))
    for k, e in enumerate(model.edges):
        pl = plan[k]
        kind, extra = edge_style(e)
        d.edge(resolve(e.src), resolve(e.tgt), edge_text[k], kind, extra, exit=pl.get("exit"), entry=pl.get("entry"),
               points=[(x + M, y + M) for x, y in pl["points"]] or None, src_label=e.src_card, tgt_label=e.tgt_card, er=e.er,
               label_x=spots.get(k))
    top = H + 2 * M
    if model.legend:
        top += legend(d, model, M, H + 2 * M) + M
    d.width, d.height = W + 2 * M, top
    return d, (W + 2 * M, top)
