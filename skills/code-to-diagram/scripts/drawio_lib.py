"""drawio_lib - write a .drawio file (uncompressed mxGraph XML) from Python calls.

The parts that go wrong in hand-written XML are done here once: labels are escaped for html=1 (a literal "<" must be
double-escaped), every edge is glued to real shapes (so it follows them when someone moves a box in draw.io),
class/table heights match their rows, containers are real containers (children move with them).

    d = Diagram()
    api = d.box("api", "API server", 40, 40, 160, 60)
    db = d.box("db", "SQLite", 320, 40, 120, 60, shape="cylinder", color="yellow")
    d.edge(api, db, "reads / writes")
    d.save("diagrams/orders/orders.drawio")

Labels are plain text ("\\n" = line break, "<List>" shows literally). For markup use Html("<b>x</b><br>y").
"""
import hashlib
import html
import os
import re
import xml.etree.ElementTree as ET

PALETTE = {  # role -> (fill, stroke); colour-blind friendly
    "blue": ("#dae8fc", "#6c8ebf"), "green": ("#d5e8d4", "#82b366"), "red": ("#f8cecc", "#b85450"),
    "yellow": ("#fff2cc", "#d6b656"), "purple": ("#e1d5e7", "#9673a6"), "gray": ("#f5f5f5", "#666666"),
    "white": ("#ffffff", "#666666"),
}
SHAPES = {
    "rounded": "rounded=1;whiteSpace=wrap;html=1;arcSize=12;",
    "rect": "rounded=0;whiteSpace=wrap;html=1;",
    "stadium": "rounded=1;arcSize=50;whiteSpace=wrap;html=1;",
    "ellipse": "ellipse;whiteSpace=wrap;html=1;",
    "diamond": "rhombus;whiteSpace=wrap;html=1;",
    "cylinder": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;",
    "cloud": "ellipse;shape=cloud;whiteSpace=wrap;html=1;",
    "note": "shape=note;whiteSpace=wrap;html=1;size=14;verticalAlign=top;align=left;spacingLeft=4;spacingTop=-4;",
    "para": "shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;",
    "text": "text;html=1;whiteSpace=wrap;strokeColor=none;fillColor=none;align=left;verticalAlign=top;",
    "start": "ellipse;shape=startState;html=1;fillColor=#000000;strokeColor=#000000;",
    "end": "ellipse;shape=endState;html=1;fillColor=#000000;strokeColor=#000000;",
    "fork": "rounded=0;html=1;fillColor=#000000;strokeColor=#000000;",
    "line": "line;strokeWidth=1;dashed=1;html=1;fillColor=none;strokeColor=#666666;",
}
EDGE_BASE = "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#333333;fontSize=11;labelBackgroundColor=#ffffff;"
EDGES = {
    "flow": "endArrow=classic;",
    "plain": "endArrow=none;",
    "inherit": "endArrow=block;endFill=0;endSize=14;",                             # hollow triangle at the parent (target)
    "realize": "dashed=1;endArrow=block;endFill=0;endSize=14;",
    "compose": "startArrow=diamondThin;startFill=1;startSize=14;endArrow=none;",    # diamond at the owner (source)
    "aggregate": "startArrow=diamondThin;startFill=0;startSize=14;endArrow=none;",
    "assoc": "endArrow=open;endFill=0;",
    "depend": "dashed=1;endArrow=open;endFill=0;endSize=8;",
    "er": "endFill=0;startFill=0;",
}
ROW_STYLE = ("text;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;spacingLeft=6;spacingRight=4;"
             "overflow=hidden;rotatable=0;points=[[0,0.5],[1,0.5]];portConstraint=eastwest;whiteSpace=nowrap;html=1;")
SEP_STYLE = ("line;strokeWidth=1;fillColor=none;align=left;verticalAlign=middle;spacingTop=-1;spacingLeft=3;"
             "spacingRight=3;rotatable=0;labelPosition=right;points=[];portConstraint=eastwest;html=1;")


CHAR_W, LINE_H = 7.0, 17      # px per character at 12 px (a little generous so nothing clips) / per text line


def wrap_lines(text, width=30):
    """Break a label into lines of at most `width` characters at spaces (existing line breaks kept)."""
    out = []
    for raw in text.split("\n"):
        line = ""
        for word in raw.split(" "):
            if line and len(line) + 1 + len(word) > width:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        out.append(line)
    return out


def text_width(text, per_char=CHAR_W):
    return max((len(l) for l in text.split("\n")), default=0) * per_char


STAMP = re.compile(r'<diagram id="c2d-([0-9a-f]{12})"')


def content_hash(text):
    """Short hash of a generated file, computed with the stamp itself left out."""
    plain = STAMP.sub('<diagram id="diagram-1"', text, count=1)
    return hashlib.sha1(plain.encode("utf-8")).hexdigest()[:12]


def untouched(text):
    """True when `text` is exactly what build generated: its stamp matches its content. A file saved by draw.io
    (hand edits) loses or breaks the stamp."""
    m = STAMP.search(text)
    return bool(m) and m.group(1) == content_hash(text)


class Html(str):
    """Pre-formatted label markup (<b>, <br>, <i>, <font>); passed through unescaped."""


def label_text(label):
    """Visible text -> html=1 label source. The XML layer of escaping is done by ElementTree on save."""
    if isinstance(label, Html):
        return str(label)
    return html.escape(str(label), quote=False).replace("\n", "<br>")


def class_height(members, methods=(), stereotype="", row_h=24):
    head = 40 if stereotype else 26
    return head + row_h * (len(members) + len(methods)) + (8 if members and methods else 0)


def _num(v):
    return str(int(v)) if float(v) == int(v) else f"{float(v):.2f}"


def _colors(color, fill, stroke):
    f, s = PALETTE.get(color, PALETTE["blue"])
    return fill or f, stroke or s


def _anchor(kind, spec):
    if spec is None:
        return ""
    fx, fy = spec
    return f"{kind}X={fx:.4f};{kind}Y={fy:.4f};{kind}Dx=0;{kind}Dy=0;"


# draw.io fails the whole export when a cell id equals a member of a JavaScript Array or Object ("join", "map", "find",
# "constructor"): natural names in a code diagram. Such ids are written with a prefix that a Mermaid id cannot contain.
JS_MEMBERS = frozenset("""at concat copyWithin entries every fill filter find findIndex findLast findLastIndex flat flatMap forEach
    includes indexOf join keys lastIndexOf length map pop push reduce reduceRight reverse shift slice some sort splice
    toLocaleString toReversed toSorted toSpliced toString unshift valueOf values with constructor hasOwnProperty
    isPrototypeOf propertyIsEnumerable __proto__ __defineGetter__ __defineSetter__ __lookupGetter__ __lookupSetter__""".split())


def safe_id(cid):
    return f"n-{cid}" if cid in JS_MEMBERS else cid


class Diagram:
    def __init__(self, width=1169, height=826, name="Page-1"):
        self.width, self.height, self.name = width, height, name
        self._cells, self._ids = [], set()

    # -- low level ---------------------------------------------------------------------------------------------
    def _cell(self, cid, value, style, kind, parent, geom=None, children=()):
        cid, parent = safe_id(cid), safe_id(parent)
        if cid in self._ids or cid in ("0", "1"):
            raise ValueError(f"duplicate id {cid!r}")
        self._ids.add(cid)
        el = ET.Element("mxCell", {"id": cid, "value": value, "style": style, kind: "1", "parent": parent})
        g = ET.SubElement(el, "mxGeometry", {k: _num(v) for k, v in (geom or {}).items()})
        g.set("as", "geometry")
        for c in children:
            g.append(c)
        self._cells.append(el)
        return el

    # -- shapes ------------------------------------------------------------------------------------------------
    def box(self, id, label, x, y, w=160, h=60, shape="rounded", color="blue", parent="1", fill=None, stroke=None, font=12, style=""):
        f, s = _colors(color, fill, stroke)
        look = "" if shape in ("text", "start", "end", "fork", "line") else f"fillColor={f};strokeColor={s};"
        self._cell(id, label_text(label), f"{SHAPES[shape]}{look}fontSize={font};{style}", "vertex", parent, dict(x=x, y=y, width=w, height=h))
        return id

    def title(self, text, x, y):
        """Heading above the diagram."""
        return self.box("title", Html(f"<b>{html.escape(text)}</b>"), x, y, text_width(text, 9.5) + 20, 28, "text", font=16, style="align=left;")

    def container(self, id, title, x, y, w, h, color="gray", parent="1", header=30, fill=None, stroke=None):
        """Swimlane. Children pass parent=<this id> and coordinates RELATIVE to it. Edges stay on parent='1'."""
        f, s = _colors(color, fill, stroke)
        style = (f"swimlane;fontStyle=1;startSize={header};html=1;whiteSpace=wrap;container=1;collapsible=0;"
                 f"fillColor={f};strokeColor={s};swimlaneFillColor=none;")
        self._cell(id, label_text(title), style, "vertex", parent, dict(x=x, y=y, width=w, height=h))
        return id

    def class_box(self, id, title, members, x, y, w=200, methods=(), stereotype="", color="blue", parent="1", row_h=24, fill=None, stroke=None):
        """UML class / interface / ER table: title, one row per member, optional separator and methods.
        Height is computed (class_height), so rows never clip. Connect edges to the returned id."""
        head = 40 if stereotype else 26
        sep = 8 if members and methods else 0
        f, s = _colors(color, fill, stroke)
        title_html = Html(f"<i>«{html.escape(stereotype)}»</i><br><b>{html.escape(title)}</b>") if stereotype else Html(f"<b>{html.escape(title)}</b>")
        style = ("swimlane;fontStyle=0;align=center;verticalAlign=top;childLayout=stackLayout;horizontal=1;"
                 f"startSize={head};horizontalStack=0;resizeParent=1;resizeParentMax=0;resizeLast=0;collapsible=0;"
                 f"marginBottom=0;whiteSpace=wrap;html=1;fillColor={f};strokeColor={s};swimlaneFillColor=#ffffff;")
        self._cell(id, label_text(title_html), style, "vertex", parent, dict(x=x, y=y, width=w, height=class_height(members, methods, stereotype, row_h)))
        y_off = head
        for i, text in enumerate(members):
            self._cell(f"{id}-f{i}", label_text(text), ROW_STYLE, "vertex", id, dict(y=y_off, width=w, height=row_h))
            y_off += row_h
        if sep:
            self._cell(f"{id}-sep", "", SEP_STYLE, "vertex", id, dict(y=y_off, width=w, height=sep))
            y_off += sep
        for i, text in enumerate(methods):
            self._cell(f"{id}-m{i}", label_text(text), ROW_STYLE, "vertex", id, dict(y=y_off, width=w, height=row_h))
            y_off += row_h
        return id

    def legend(self, x, y, entries, w=240, row_h=26):
        """Legend box. entries: [(label, fill, stroke)]. A stack-layout container: draw.io re-stacks rows when one is added."""
        head = 28
        style = ("swimlane;fontStyle=1;childLayout=stackLayout;horizontal=1;startSize=28;horizontalStack=0;resizeParent=1;"
                 "resizeParentMax=0;resizeLast=0;collapsible=0;marginBottom=0;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#999999;")
        self._cell("legend", "Legend", style, "vertex", "1", dict(x=x, y=y, width=w, height=head + row_h * len(entries)))
        for i, (label, fill, stroke) in enumerate(entries):
            self._cell(f"legend-r{i}", label_text(label),
                       f"text;html=1;align=left;verticalAlign=middle;spacingLeft=8;fontSize=11;whiteSpace=nowrap;overflow=hidden;"
                       f"fillColor={fill};strokeColor={stroke};rotatable=0;portConstraint=eastwest;", "vertex", "legend",
                       dict(y=head + row_h * i, width=w, height=row_h))
        return head + row_h * len(entries)

    # -- edges -------------------------------------------------------------------------------------------------
    def edge(self, src, tgt, label="", kind="flow", extra="", exit=None, entry=None, points=None, id=None,
             src_label="", tgt_label="", er=(), label_x=None, label_dx=0):
        """Glued edge between two existing ids. kind: flow plain inherit realize compose aggregate assoc depend er.
        extra: more style (e.g. 'dashed=1;'). exit/entry: (fx, fy) anchor fractions of the shape's box.
        points: absolute waypoints [(x, y)]. src_label/tgt_label: small texts near each end (cardinalities).
        er: (start arrow, end arrow) draw.io ER names for kind='er'. label_x: position of the label along the edge, -1..1; label_dx: shift it sideways by this many px."""
        style = EDGE_BASE + EDGES[kind] + (f"startArrow={er[0]};endArrow={er[1]};" if er else "") + extra
        style += _anchor("exit", exit) + _anchor("entry", entry)
        kids = []
        if points:
            arr = ET.Element("Array", {"as": "points"})
            for px, py in points:
                ET.SubElement(arr, "mxPoint", {"x": _num(px), "y": _num(py)})
            kids = [arr]
        if label_dx:
            kids.append(ET.Element("mxPoint", {"as": "offset", "x": _num(label_dx), "y": "0"}))
        eid = id or f"e{len(self._cells)}"
        el = self._cell(eid, label_text(label), style, "edge", "1", None, kids)
        el.set("source", safe_id(src))
        el.set("target", safe_id(tgt))
        el.find("mxGeometry").set("relative", "1")
        if label_x is not None and abs(label_x) > 0.01:
            el.find("mxGeometry").set("x", f"{label_x:.3f}")
        for side, text in ((-0.8, src_label), (0.8, tgt_label)):
            if text:
                lab = self._cell(f"{eid}-l{'s' if side < 0 else 't'}", label_text(text),
                                 "edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];fontSize=11;labelBackgroundColor=#ffffff;",
                                 "vertex", eid, dict(x=side, relative=1))
                lab.set("connectable", "0")
                ET.SubElement(lab.find("mxGeometry"), "mxPoint", {"as": "offset", "x": "0", "y": "-12"})
        return eid

    # -- output ------------------------------------------------------------------------------------------------
    def xml(self):
        for el in self._cells:                                   # glued edges: every end must exist
            for end in ("source", "target"):
                ref = el.get(end)
                if ref is not None and ref not in self._ids:
                    raise ValueError(f"edge {el.get('id')!r}: {end} {ref!r} is not a shape id")
        attrs = dict(dx="1200", dy="800", grid="1", gridSize="10", guides="1", tooltips="1", connect="1", arrows="1", fold="1",
                     page="1", pageScale="1", pageWidth=str(int(self.width)), pageHeight=str(int(self.height)), math="0", shadow="0")
        mxfile = ET.Element("mxfile", {"host": "app.diagrams.net", "agent": "code-to-diagram"})
        diagram = ET.SubElement(mxfile, "diagram", {"id": "diagram-1", "name": self.name})
        root = ET.SubElement(ET.SubElement(diagram, "mxGraphModel", attrs), "root")
        ET.SubElement(root, "mxCell", {"id": "0"})
        ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
        for el in self._cells:
            root.append(el)
        ET.indent(mxfile)
        text = ET.tostring(mxfile, encoding="unicode")
        return text.replace('<diagram id="diagram-1"', f'<diagram id="c2d-{content_hash(text)}"', 1)

    def save(self, path):
        text = self.xml()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return path
