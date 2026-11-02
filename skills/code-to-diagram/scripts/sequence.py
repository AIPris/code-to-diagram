"""Sequence diagrams: participants with lifelines, messages in time order, notes, alt/opt/loop/break frames.

Lifelines are thin, equally tall rectangles, grouped with their header box so that the two move together; message k
leaves and enters at the fraction (y_k - top) / height of that height on both ends, which keeps every arrow exactly
horizontal. (shape=umlLifeline is a container with special child behaviour and breaks when edited, so it is not used.)
Frames are umlFrame shapes, the condition a text beside the tab. build measures the result (model.sequence_report):
texts lying on texts or on messages, notes hiding a lifeline.
"""
import html

from drawio_lib import Diagram, Html, wrap_lines, text_width
from labels import _overlap
from metrics import label_cost

M = 40                  # page margin
BOX_H = 44
TITLE_H = 40            # heading above the participants
STEP = 48               # vertical space one message takes
CHAR_W = 6.8            # message and note text is 11 px
LABEL_W = 5.2           # width per character of a message label as drawn, for measuring (CHAR_W leaves room to spare)
SLACK = 3               # two texts that overlap by less than this many px per side still read as apart


def _text_w(text):
    return text_width(text, CHAR_W)


def clashes(texts, lines):
    """How many of the texts [(rect (x, y, w, h), key of the message line it belongs to or None)] lie on another text or on the
    line of a message that is not theirs. Lines: {key: polyline}."""
    small = [(x + SLACK, y + SLACK, w - 2 * SLACK, h - 2 * SLACK) for (x, y, w, h), _ in texts]
    return sum(1 for i, ((_, own), r) in enumerate(zip(texts, small))
               if any(_overlap(r, o) for j, o in enumerate(small) if j != i) or label_cost(r, own, lines, []))


def build(model):
    parts = model.participants
    if not parts:
        raise ValueError("sequence diagram without participants")
    idx = {p[0]: i for i, p in enumerate(parts)}
    box_w = max(120, max(_text_w(p[1]) for p in parts) + 28)
    gaps = [box_w + 60] * (len(parts) - 1) + [box_w + 60]     # gap after each participant; the last is spare room

    # frames and rows ----------------------------------------------------------------------------------------------
    n = 0
    rows, frames, stack = [], [], []
    for it in model.items:
        if it[0] == "msg":
            n += 1
            text = (f"{n}. " if model.numbered else "") + it[3]
            rows.append(("msg", it[1], it[2], text, it[4], it[5]))
            for fr in stack:
                fr["span"] |= {idx[it[1]], idx[it[2]]}
                if it[1] == it[2]:                                 # a self call's label hangs to the right of its loop
                    fr["selfs"].append((idx[it[1]], text))
        elif it[0] == "note":
            rows.append(("note", it[1], it[2], it[3]))
            for fr in stack:
                fr["span"] |= {idx[p] for p in it[2]}
        elif it[0] == "open":
            fr = dict(kind=it[1], text=it[2], span=set(), depth=len(stack), start=len(rows), elses=[], selfs=[])
            frames.append(fr)
            stack.append(fr)
            rows.append(("open", fr))
        elif it[0] == "else":
            stack[-1]["elses"].append((len(rows), it[1]))
            rows.append(("else", it[1], stack[-1]))
        elif it[0] == "close":
            fr = stack.pop()
            fr["end"] = len(rows)
            rows.append(("close", fr))

    # horizontal positions: widen gaps so every label fits ----------------------------------------------------------
    need = []
    for r in rows:
        if r[0] == "msg":
            a, b = sorted((idx[r[1]], idx[r[2]]))
            need.append((a, b, _text_w(r[3]) + 50) if a != b else (a, a + 1, _text_w(r[3]) + 70))
        elif r[0] == "note" and r[1] == "over" and len(r[2]) == 2:
            a, b = sorted(idx[p] for p in r[2])
            need.append((a, b, _text_w(r[3]) + 20 - 140))
    for a, b, width in sorted(need, key=lambda x: x[1] - x[0]):
        have = sum(gaps[a:b])
        if have < width:
            for k in range(a, b):
                gaps[k] += (width - have) / (b - a)
    cx = [M + box_w / 2]
    for g in gaps[:-1]:
        cx.append(cx[-1] + g)

    # vertical layout ----------------------------------------------------------------------------------------------
    head_y = M + (TITLE_H if model.title else 0)                  # participant boxes sit below the title
    top = head_y + BOX_H + 6
    t, ys, note_boxes = top + 20, [], {}
    for k, r in enumerate(rows):
        if r[0] == "msg":
            extra = 16 * (len(r[3].split("\n")) - 1)
            ys.append(t + 28 + extra)
            t += (STEP + extra) + (14 if r[1] == r[2] else 0)
        elif r[0] == "note":
            lines = wrap_lines(r[3], 34)
            h = len(lines) * 17 + 14
            w = max(120, max(len(l) for l in lines) * CHAR_W + 22)
            ys.append(t + 4)
            note_boxes[k] = (w, h)
            t += h + 18
        elif r[0] == "open":
            ys.append(t)
            t += 30
        elif r[0] == "else":
            ys.append(t + 4)
            t += 32
        else:
            ys.append(t + 2)
            t += 14
    height = t + 14 - top

    d = Diagram()
    left_most = min([cx[0] - box_w / 2] + [_note_x(r, idx, cx, note_boxes[k])[0] for k, r in enumerate(rows) if r[0] == "note"])
    shift = M - left_most if left_most < M else 0
    cx = [c + shift for c in cx]

    texts, lines = [], {}                                          # what build measures: text rectangles, message polylines
    for i, (pid, label, actor) in enumerate(parts):
        g = d.group(f"seq-p-{pid}", cx[i] - box_w / 2, head_y, box_w, top + height - head_y)
        d.box(f"seq-hd-{pid}", label, 0, 0, box_w, BOX_H, "rounded", "purple" if actor else "blue", parent=g)
        d.box(f"seq-ll-{pid}", "", box_w / 2 - 5, top - head_y, 10, height, "rect", "gray", parent=g, style="dashed=1;")
    for k, fr in enumerate(frames):
        a, b = min(fr["span"], default=0), max(fr["span"], default=len(parts) - 1)
        inset = 12 * fr["depth"]
        x0, x1 = cx[a] - box_w / 2 - 16 + inset, cx[b] + box_w / 2 + 16 - inset
        x1 = max([x1] + [cx[i] + 40 + text_width(t, 5.2) + 40 - inset for i, t in fr["selfs"]])
        y0, y1 = ys[fr["start"]] - 4, ys[fr["end"]] + 6
        if fr["kind"]:                                              # a UML fragment: the kind in the tab, the condition beside it
            tab = text_width(fr["kind"], 8) + 24
            d.box(f"seq-fr{k}", fr["kind"], x0, y0, x1 - x0, y1 - y0, "frame", font=11, style=f"width={tab:.0f};height=22;")
            texts.append(((x0, y0, tab, 22), None))
            if fr["text"]:
                d.box(f"seq-fr{k}-tag", Html(f"[{html.escape(fr['text'])}]"), x0 + tab + 6, y0 - 2, min(300, x1 - x0 - tab - 12), 20, "text", font=11,
                      style="labelBackgroundColor=#ffffff;")           # in line with the tab's text, and over any lifeline it crosses
                texts.append(((x0 + tab + 6, y0 + 1, text_width(f"[{fr['text']}]", LABEL_W) + 4, 16), None))
        else:                                                       # a highlight (`rect`), not a fragment
            d.box(f"seq-fr{k}", "", x0, y0, x1 - x0, y1 - y0, "rect", "white", style="dashed=1;fillColor=none;")
        for j, (row_k, text) in enumerate(fr["elses"]):
            y = ys[row_k]
            d.box(f"seq-fr{k}-else{j}", "", x0, y - 4, x1 - x0, 8, "line")
            if text:
                d.box(f"seq-fr{k}-else{j}-tag", Html(f"[{html.escape(text)}]"), x0 + 4, y + 2, min(300, x1 - x0 - 8), 22, "text", font=11)
                texts.append(((x0 + 4, y + 4, text_width(f"[{text}]", LABEL_W) + 4, 16), None))
    note_hits = 0
    for k, r in enumerate(rows):
        if r[0] == "note":
            x, w = _note_x(r, idx, cx, note_boxes[k])
            d.box(f"seq-note{k}", "\n".join(wrap_lines(r[3], 34)), x, ys[k], w, note_boxes[k][1], "note", "yellow", font=11)
            texts.append(((x, ys[k], w, note_boxes[k][1]), None))
            named = sorted(idx[p] for p in r[2])
            named = set(range(named[0], named[-1] + 1)) if r[1] == "over" else set(named)         # a note over A,B covers what lies between
            note_hits += any(_overlap((x, ys[k], w, note_boxes[k][1]), (cx[i] - 5, top, 10, height)) for i in range(len(parts)) if i not in named)
        elif r[0] == "msg":
            _, a, b, text, kind, cross = r
            f = (ys[k] - top) / height
            arrow = "endArrow=cross;endFill=0;" if cross else "endArrow=open;endFill=0;dashed=1;" if kind == "return" else "endArrow=block;endFill=1;"
            style = f"html=1;fontSize=11;labelBackgroundColor=#ffffff;verticalAlign=bottom;{arrow}"
            if a == b:
                f1, f2 = (ys[k] - 10 - top) / height, (ys[k] + 10 - top) / height
                x = cx[idx[a]] + 40
                d.edge(f"seq-ll-{a}", f"seq-ll-{a}", text, "flow", style.replace("verticalAlign=bottom;", "") + "edgeStyle=orthogonalEdgeStyle;",
                       exit=(1, f1), entry=(1, f2), points=[(x, ys[k] - 10), (x, ys[k] + 10)], id=f"seq-m{k}",
                       label_dx=text_width(text, 5.2) / 2 + 10)             # the label is centred on the loop: move it clear of it
                lines[k] = [(x - 35, ys[k] - 10), (x, ys[k] - 10), (x, ys[k] + 10), (x - 35, ys[k] + 10)]
                w_, h_ = text_width(text, LABEL_W) + 8, 14 * len(text.split("\n"))
                texts.append(((x + text_width(text, 5.2) / 2 + 10 - w_ / 2, ys[k] - h_ / 2, w_, h_), k))
            else:
                right = cx[idx[b]] > cx[idx[a]]
                d.edge(f"seq-ll-{a}", f"seq-ll-{b}", text, "plain", style + "edgeStyle=none;",
                       exit=(1 if right else 0, f), entry=(0 if right else 1, f), id=f"seq-m{k}")
                ends = (cx[idx[a]] + (5 if right else -5), cx[idx[b]] - (5 if right else -5))
                lines[k] = [(ends[0], ys[k]), (ends[1], ys[k])]
                w_, h_ = text_width(text, LABEL_W) + 8, 14 * len(text.split("\n"))                  # the label sits on the line, centred
                texts.append((((ends[0] + ends[1]) / 2 - w_ / 2, ys[k] - 4 - h_, w_, h_), k))
    notes_right = [sum(_note_x(r, idx, cx, note_boxes[k])) for k, r in enumerate(rows) if r[0] == "note"]
    right_edge = max([cx[-1] + box_w / 2] + notes_right)
    self_room = max([cx[idx[r[1]]] + 40 + _text_w(r[3]) for r in rows if r[0] == "msg" and r[1] == r[2]], default=0)
    d.width, d.height = max(right_edge, self_room) + M, top + height + M
    if model.title:
        d.title(model.title, M, M)
    model.sequence_report = dict(clashes=clashes(texts, lines), note_hits=note_hits, messages=n, frames=len(frames))
    return d, (d.width, d.height)


def _note_x(row, idx, cx, size):
    """(left x, width) of a note row."""
    _, where, ps, _ = row
    w = size[0]
    if where == "over" and len(ps) == 2:
        a, b = sorted(cx[idx[p]] for p in ps)
        left = (a + b) / 2 - max(w, b - a + 140) / 2
        return left, max(w, b - a + 140)
    c = cx[idx[ps[0]]]
    if where == "over":
        return c - w / 2, w
    if where == "left of":
        return c - 24 - w, w
    return c + 24, w
