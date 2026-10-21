#!/usr/bin/env python3
"""Structural validator for .drawio files.

Usage: python validate_drawio.py <file.drawio>

Checks (deterministic, no rendering needed):
  E1  XML parses
  E2  mxGraphModel/root present with root cells id="0" and id="1"
  E3  no duplicate cell ids
  E4  every cell's parent attribute refers to an existing cell
  E5  every vertex has an mxGeometry with positive width/height
  E6  every edge has source and target referring to existing cells
  E7  manually-waypointed edge segments do not pass through the bounding
      box of any vertex that is not the edge's source/target (or one of
      their ancestor containers)
  E8  sibling vertices (same parent) do not partially overlap (a vertex fully inside another is W5, not an error)
  W1  edge missing <mxGeometry relative="1">
  W2  vertex with an empty label (unless it is a separator/spacer style)
  W3  sibling vertices closer than 20px (recommended gap is 40px)
  W5  vertex lying fully inside a sibling that is not its container (use swimlane / container=1)
  W6  container whose child rows run past its bottom edge (clipped class/table rows)
  W4  html=1 label whose (XML-decoded) text contains '<' that is not a
      known HTML tag: draw.io's HTML renderer treats it as a tag and the
      text after it disappears. Write List&amp;lt;Order&amp;gt; in the
      XML (double-escaped) so it renders as List<Order>.

Exit code 0 = no errors (warnings allowed), 1 = errors found.
"""
import re
import sys

try:  # hardened parser if available (XXE / billion-laughs protection)
    import defusedxml.ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

ALLOWED_HTML = re.compile(r"</?(b|i|u|br|div|span|font|hr|sub|sup)\b[^>]*/?>", re.I)
SPACER_STYLES = ("line;", "text;", "edgeLabel", "group")


def parse_cells(model):
    """Return {id: cell-dict}. Handles <object>/<UserObject> wrappers."""
    cells = {}
    for child in model.find("root"):
        if child.tag == "mxCell":
            el, cid, value = child, child.get("id"), child.get("value", "")
        elif child.tag in ("object", "UserObject"):
            el = child.find("mxCell")
            if el is None:
                continue
            cid, value = child.get("id"), child.get("label", "")
        else:
            continue
        geom = el.find("mxGeometry")
        points = []
        if geom is not None:
            arr = geom.find("Array[@as='points']")
            if arr is not None:
                for p in arr.findall("mxPoint"):
                    points.append((float(p.get("x", 0)), float(p.get("y", 0))))
        cells[cid] = {
            "id": cid,
            "value": value or "",
            "style": el.get("style", "") or "",
            "vertex": el.get("vertex") == "1",
            "edge": el.get("edge") == "1",
            "parent": el.get("parent"),
            "source": el.get("source"),
            "target": el.get("target"),
            "geom": geom,
            "points": points,
        }
    return cells


def is_spacerish(cell, cells):
    """Cells that legitimately lack labels / touch neighbors: separator and
    text rows, rows inside a stackLayout class shape, and thin line-like
    shapes (sequence lifelines, activation bars)."""
    if any(s in cell["style"] for s in SPACER_STYLES):
        return True
    # outline-only frames (alt/opt/loop boxes, dashed groups) and black initial-state dots carry no label of their own
    if "fillColor=none" in cell["style"] or "fillColor=#000000" in cell["style"]:
        return True
    parent = cells.get(cell["parent"])
    if parent and "childLayout=stackLayout" in parent["style"]:
        return True
    g = cell["geom"]
    if g is not None and (
        float(g.get("width", 0)) <= 12 or float(g.get("height", 0)) <= 12
    ):
        return True
    return False


def abs_bbox(cell, cells):
    """Absolute (x, y, w, h) accounting for container nesting; None if no geometry."""
    g = cell["geom"]
    if g is None or g.get("relative") == "1":
        return None
    x, y = float(g.get("x", 0)), float(g.get("y", 0))
    w, h = float(g.get("width", 0)), float(g.get("height", 0))
    p = cell["parent"]
    while p in cells and p not in ("0", "1"):
        pg = cells[p]["geom"]
        if pg is not None and pg.get("relative") != "1":
            x += float(pg.get("x", 0))
            y += float(pg.get("y", 0))
        p = cells[p]["parent"]
    return (x, y, w, h)


def ancestors(cid, cells):
    out = set()
    while cid in cells:
        out.add(cid)
        cid = cells[cid]["parent"]
    return out


def box_contains(outer, inner, tol=1):
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ix >= ox - tol and iy >= oy - tol and ix + iw <= ox + ow + tol and iy + ih <= oy + oh + tol


def point_in_box(p, box, tol=1):
    return box[0] - tol <= p[0] <= box[0] + box[2] + tol and box[1] - tol <= p[1] <= box[1] + box[3] + tol


def container_like(cell):
    """A lane, group or frame: an edge may legitimately run inside it."""
    return any(k in cell["style"] for k in ("swimlane", "container=1", "fillColor=none"))


def seg_hits_rect(p1, p2, rect, margin=2):
    """True if segment p1-p2 passes through rect (shrunk by margin)."""
    x, y, w, h = rect
    xmin, ymin, xmax, ymax = x + margin, y + margin, x + w - margin, y + h - margin
    if xmin >= xmax or ymin >= ymax:
        return False
    # Liang-Barsky clip
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, p1[0] - xmin), (dx, xmax - p1[0]),
                 (-dy, p1[1] - ymin), (dy, ymax - p1[1])):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


def validate(path):
    """Check a .drawio file. Returns (errors, warnings, vertex count, edge count); messages start with their code."""
    errors, warnings = [], []
    with open(path, "rb") as f:
        head = f.read(4096)
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        return ["E1: DTD/ENTITY declarations are not allowed in .drawio files"], [], 0, 0
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        return [f"E1: XML does not parse: {e} (raw <, >, & inside value=\"...\" must be &lt; &gt; &amp;)"], [], 0, 0

    model = tree.getroot().find(".//mxGraphModel")
    if model is None or model.find("root") is None:
        return ["E2: no mxGraphModel/root element found (compressed diagram? save it uncompressed)"], [], 0, 0

    cells = parse_cells(model)
    if "0" not in cells or "1" not in cells:
        errors.append('E2: required root cells id="0" and id="1" are missing')

    seen = set()
    for child in model.find("root"):
        cid = child.get("id")
        if cid in seen:
            errors.append(f"E3: duplicate cell id '{cid}'")
        seen.add(cid)

    for c in cells.values():
        cid = c["id"]
        if cid != "0" and c["parent"] not in cells:
            errors.append(f"E4: cell '{cid}' has unknown parent '{c['parent']}'")
        if c["vertex"]:
            bb = abs_bbox(c, cells)
            g = c["geom"]
            if g is None:
                errors.append(f"E5: vertex '{cid}' has no mxGeometry")
            elif g.get("relative") != "1" and (
                float(g.get("width", 0)) <= 0 or float(g.get("height", 0)) <= 0
            ):
                errors.append(f"E5: vertex '{cid}' has non-positive width/height")
            if not c["value"].strip() and not is_spacerish(c, cells):
                warnings.append(f"W2: vertex '{cid}' has an empty label")
            if "html=1" in c["style"]:
                stripped = ALLOWED_HTML.sub("", c["value"])
                if "<" in stripped:
                    warnings.append(
                        f"W4: label of '{cid}' ({c['value'][:40]!r}) contains '<' "
                        f"which the html=1 renderer will eat as a tag - "
                        f"double-escape it in the XML as &amp;lt;"
                    )
        if c["edge"]:
            for end in ("source", "target"):
                ref = c[end]
                if ref is None:
                    errors.append(f"E6: edge '{cid}' has no {end}")
                elif ref not in cells:
                    errors.append(f"E6: edge '{cid}' {end} '{ref}' does not exist")
            g = c["geom"]
            if g is None or g.get("relative") != "1":
                warnings.append(f"W1: edge '{cid}' missing <mxGeometry relative=\"1\">")

    # E7: waypointed edges crossing unrelated vertices
    for c in cells.values():
        if not (c["edge"] and c["points"]):
            continue
        skip = ancestors(c["source"], cells) | ancestors(c["target"], cells)
        pts = c["points"]
        # anchors are unknown without resolving exitX/entryX; check the
        # explicitly-specified waypoint-to-waypoint segments only
        for i in range(len(pts) - 1):
            for v in cells.values():
                if not v["vertex"] or v["id"] in skip:
                    continue
                bb = abs_bbox(v, cells)
                if bb and seg_hits_rect(pts[i], pts[i + 1], bb):
                    if container_like(v) and point_in_box(pts[i], bb) and point_in_box(pts[i + 1], bb):
                        continue  # the segment runs inside a lane/frame; it does not cut across it
                    errors.append(
                        f"E7: edge '{c['id']}' segment {pts[i]}->{pts[i + 1]} "
                        f"passes through vertex '{v['id']}' ({v['value'][:30]!r})"
                    )

    # E8/W3: sibling vertex overlap / proximity (skip relative-positioned, e.g. edge labels)
    verts = [c for c in cells.values()
             if c["vertex"] and c["geom"] is not None and c["geom"].get("relative") != "1"]
    for i, a in enumerate(verts):
        for b in verts[i + 1:]:
            if a["parent"] != b["parent"]:
                continue
            if is_spacerish(a, cells) or is_spacerish(b, cells):
                continue
            ax, ay, aw, ah = abs_bbox(a, cells)
            bx, by, bw, bh = abs_bbox(b, cells)
            gap_x = max(ax - (bx + bw), bx - (ax + aw))
            gap_y = max(ay - (by + bh), by - (ay + ah))
            if gap_x < 0 and gap_y < 0:
                box_a, box_b = abs_bbox(a, cells), abs_bbox(b, cells)
                if box_contains(box_a, box_b) or box_contains(box_b, box_a):
                    outer, inner = (a, b) if box_contains(box_a, box_b) else (b, a)
                    warnings.append(
                        f"W5: '{inner['id']}' sits inside '{outer['id']}' but is not its child; make '{outer['id']}' a "
                        f"container (swimlane, or container=1) so they move together in draw.io"
                    )
                else:
                    errors.append(f"E8: vertices '{a['id']}' and '{b['id']}' overlap")
            elif max(gap_x, gap_y) < 20:
                warnings.append(
                    f"W3: vertices '{a['id']}' and '{b['id']}' are <20px apart "
                    f"(recommended gap: 40px)"
                )

    # W6: a container whose rows run past its bottom edge (class/table shapes with a wrong height clip their rows)
    for c in cells.values():
        if not c["vertex"] or c["geom"] is None:
            continue
        rows = [r for r in cells.values() if r["parent"] == c["id"] and r["vertex"] and r["geom"] is not None
                and r["geom"].get("relative") != "1"]
        if rows and ("childLayout=stackLayout" in c["style"] or "swimlane" in c["style"]):
            need = max(float(r["geom"].get("y", 0)) + float(r["geom"].get("height", 0)) for r in rows)
            have = float(c["geom"].get("height", 0))
            if have + 1 < need:
                warnings.append(f"W6: container '{c['id']}' is {have:.0f}px tall but its rows end at {need:.0f}px; rows will clip")

    n_v = sum(1 for c in cells.values() if c["vertex"])
    n_e = sum(1 for c in cells.values() if c["edge"])
    return errors, warnings, n_v, n_e


def main(path):
    errors, warnings, n_v, n_e = validate(path)
    for e in errors:
        print(f"ERROR {e}")
    for w in warnings:
        print(f"WARNING {w}")
    print(f"{'FAIL' if errors else 'OK'}: {n_v} vertices, {n_e} edges, {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: validate_drawio.py <file.drawio>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
