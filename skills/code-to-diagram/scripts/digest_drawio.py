#!/usr/bin/env python3
"""Compact text digest of a .drawio file, for faithfulness review.

Usage: python digest_drawio.py <file.drawio>

Prints what the diagram *claims* in a few lines per shape and per edge, with edge ends resolved to the labels of the
shapes they connect, so a reviewer can compare it with the source code without reading raw XML (which is long, and
whose layout noise hides the content). Message-like edges are listed top to bottom, so call order is visible.
Free-floating edges (no source/target) are flagged: they do not follow their shapes when the diagram is edited.
"""
import html
import re
import sys

try:  # hardened parser if available (XXE / billion-laughs protection)
    import defusedxml.ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET


def plain(v):
    v = re.sub(r"<\s*br\s*/?>", " / ", v or "", flags=re.I)
    v = re.sub(r"<[^>]+>", "", v)
    return re.sub(r"\s+", " ", html.unescape(v)).strip()


def style_of(el):
    return {k: v for k, _, v in (p.partition("=") for p in (el.get("style") or "").split(";") if p)}


def load(path):
    with open(path, "rb") as f:
        head = f.read(4096)
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        sys.exit("DTD/ENTITY declarations are not allowed in .drawio files")
    model = ET.parse(path).getroot().find(".//mxGraphModel")
    if model is None:
        sys.exit("no mxGraphModel (compressed diagram? save it uncompressed)")
    cells = {}
    for el in model.find("root"):
        c = el.find("mxCell") if el.tag in ("object", "UserObject") else el
        if c is None or el.get("id") in ("0", "1"):
            continue
        cells[el.get("id")] = (el, c)
    return cells


def kind_of(style):
    for k in ("rhombus", "ellipse", "cylinder3", "cloud", "swimlane", "note", "umlLifeline", "text", "line"):
        if k in style:
            return k.replace("rhombus", "diamond").replace("cylinder3", "cylinder")
    return "box"


def main(path):
    cells = load(path)
    label = {}
    for cid, (el, c) in cells.items():
        label[cid] = plain(el.get("label") if el.tag in ("object", "UserObject") else c.get("value"))
    parent = {cid: c.get("parent") for cid, (_, c) in cells.items()}
    kids = {}
    for cid, p in parent.items():
        kids.setdefault(p, []).append(cid)

    def geom(cid):
        g = cells[cid][1].find("mxGeometry")
        return g if g is not None else None

    def abs_y(cid):
        g = geom(cid)
        y = float(g.get("y", 0)) if g is not None else 0.0
        p = parent.get(cid)
        while p in cells:
            pg = geom(p)
            y += float(pg.get("y", 0)) if pg is not None else 0.0
            p = parent.get(p)
        return y

    def box(cid):
        g = geom(cid)
        if g is None:
            return None
        x, y = float(g.get("x", 0)), abs_y(cid)
        p = parent.get(cid)
        while p in cells:
            pg = geom(p)
            x += float(pg.get("x", 0)) if pg is not None else 0.0
            p = parent.get(p)
        return x, y, float(g.get("width", 0)), float(g.get("height", 0))

    def name(cid):
        """Label of the shape or its nearest labelled ancestor; an unlabelled thin shape (a sequence lifeline) takes the
        label of the closest labelled shape above it that spans its x position (its header); else the id."""
        cur = cid
        while cur in cells:
            if label.get(cur):
                return label[cur]
            cur = parent.get(cur)
        b = box(cid)
        if b:
            cx, top, best = b[0] + b[2] / 2, b[1], None
            for other in verts:
                ob = box(other)
                if other != cid and label.get(other) and ob and ob[1] + ob[3] <= top + 1 and ob[0] <= cx <= ob[0] + ob[2]:
                    if best is None or ob[1] > best[0]:
                        best = (ob[1], label[other])
            if best:
                return best[1]
        return f"<{cid}>"

    verts = [cid for cid, (_, c) in cells.items() if c.get("vertex") == "1"]
    edges = [cid for cid, (_, c) in cells.items() if c.get("edge") == "1"]
    unglued = [e for e in edges if not (cells[e][1].get("source") and cells[e][1].get("target"))]
    print(f"{len(verts)} shapes, {len(edges)} edges" + (f", {len(unglued)} FREE-FLOATING edges (not glued to shapes)" if unglued else ""))

    print("\nSHAPES (containers list their rows)")
    for cid in verts:
        if parent[cid] in cells and cells[parent[cid]][1].get("vertex") == "1":
            # a child: shown under its container below, unless the container itself has no label (then list it here)
            if label.get(parent[cid]):
                continue
        if not label[cid] and not [k for k in kids.get(cid, []) if label.get(k)]:
            continue
        s = style_of(cells[cid][1])
        rows = [label[k] for k in kids.get(cid, []) if cells[k][1].get("vertex") == "1" and label.get(k)]
        extra = f"  rows: {' | '.join(rows)}" if rows else ""
        print(f"- [{kind_of(cells[cid][1].get('style') or '')}] {label[cid]}{extra}")

    print("\nEDGES (top to bottom)")
    for e in sorted(edges, key=lambda x: abs_y(cells[x][1].get("source") or x)):
        c = cells[e][1]
        s = c.get("style") or ""
        hints = [h for h, t in (("dashed", "dashed=1"), ("hollow/open arrowhead", "endFill=0"), ("diamond", "diamondThin"),
                                ("crow's-foot", "ERmany"), ("no-arrow", "endArrow=none")) if t in s]
        lab = label[e] or " / ".join(label[k] for k in kids.get(e, []) if label.get(k))
        src, tgt = c.get("source"), c.get("target")
        print(f"- {name(src) if src in cells else '?'} -> {name(tgt) if tgt in cells else '?'}"
              + (f"  [{lab}]" if lab else "") + (f"  ({', '.join(hints)})" if hints else ""))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: digest_drawio.py <file.drawio>")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main(sys.argv[1])
