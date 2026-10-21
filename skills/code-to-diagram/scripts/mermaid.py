"""Mermaid subset -> diagram model.

Supported: flowchart/graph, stateDiagram(-v2), sequenceDiagram, classDiagram, erDiagram. Models already know Mermaid,
so the skill does not have to teach a format. Anything this parser cannot represent is reported with its line number
(never silently dropped: a missing arrow is a lie in the diagram).

Extra directives, written as comments so the file stays valid Mermaid:
    %% src: src/ lib/util.js            files checked against the labels (build warns about names found nowhere);
                                         quote a path with spaces: %% src: "my repo/src"
    %% legend: hub=imported by 4+ files; leaf=imports nothing      class or colour name = meaning
Built-in colour names usable as classes without classDef: blue green red yellow purple gray white.
"""
import html
import re
from dataclasses import dataclass, field


class SpecError(Exception):
    pass


@dataclass
class Node:
    id: str
    label: str
    shape: str = "rect"      # rect round stadium diamond circle cylinder para note start end fork
    cls: str = ""
    parent: str = ""
    members: list = field(default_factory=list)
    methods: list = field(default_factory=list)
    stereotype: str = ""
    fill: str = ""
    stroke: str = ""


@dataclass
class Edge:
    src: str
    tgt: str
    label: str = ""
    kind: str = "flow"       # flow plain inherit realize compose aggregate assoc depend er
    line: str = "solid"      # solid dotted thick
    head: str = ""           # "" (kind default) or none cross oval
    both: bool = False       # arrowheads at both ends
    src_card: str = ""
    tgt_card: str = ""
    er: tuple = ()           # (start arrow, end arrow) draw.io ER names


@dataclass
class Group:
    id: str
    label: str
    parent: str = ""


@dataclass
class Model:
    kind: str                # flow state sequence class er
    direction: str = ""      # TB LR RL BT, or "" = not chosen (the layout picks the better of TB and LR)
    nodes: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)
    groups: dict = field(default_factory=dict)
    classes: dict = field(default_factory=dict)      # class name -> {fill, stroke}
    legend: list = field(default_factory=list)       # [(key, meaning)]
    srcs: list = field(default_factory=list)
    participants: list = field(default_factory=list) # sequence: [(id, label, actor)]
    items: list = field(default_factory=list)        # sequence: message/note/frame rows in time order
    numbered: bool = False
    errors: list = field(default_factory=list)       # [(line no, text, reason)]


COLORS = {"blue", "green", "red", "yellow", "purple", "gray", "white"}
COSMETIC = re.compile(r"^(linkStyle|activate|deactivate|title|accTitle|accDescr|hide|show|%%\{)\b", re.I)


def clean(text):
    """Label text from a spec: quotes off, <br> and \\n to line breaks, #quot; style entities decoded."""
    t = text.strip()
    if len(t) >= 2 and t[0] == t[-1] == '"':
        t = t[1:-1]
    t = re.sub(r"<br\s*/?>|\\n", "\n", t, flags=re.I)
    t = re.sub(r"#(\w+);", lambda m: "#" if m.group(1) == "35" else (html.unescape("&%s;" % m.group(1)) if not m.group(1).isdigit() else chr(int(m.group(1)))), t)
    t = t.replace("`", "")
    return t.strip()


def parse_style(text):
    out = {}
    for part in text.split(","):
        k, _, v = part.partition(":")
        out[k.strip()] = v.strip()
    return out


def parse(text):
    """Mermaid source -> Model (errors collected in model.errors, never raised)."""
    lines = text.splitlines()
    head = next(((i, l.strip()) for i, l in enumerate(lines) if l.strip() and not l.strip().startswith("%%")), None)
    srcs, legend = [], []
    for l in lines:
        m = re.match(r"\s*%%\s*(src|legend)\s*:\s*(.*)$", l)
        if m and m.group(1) == "src":
            srcs += [p.strip('"') for p in re.findall(r'"[^"]+"|\S+', m.group(2))]
        elif m:
            legend += [tuple(x.strip() for x in e.split("=", 1)) for e in m.group(2).split(";") if "=" in e]
    if not head:
        m = Model("flow")
        m.errors.append((1, "", "empty diagram"))
        return m
    first = head[1].split()[0].rstrip(":")
    kinds = {"flowchart": _flow, "graph": _flow, "stateDiagram": _state, "stateDiagram-v2": _state,
             "sequenceDiagram": _sequence, "classDiagram": _class, "erDiagram": _er}
    if first not in kinds:
        m = Model("flow")
        m.errors.append((head[0] + 1, head[1], "first line must be one of: flowchart TD|LR, stateDiagram-v2, sequenceDiagram, classDiagram, erDiagram"))
        return m
    model = kinds[first](lines, head[0])
    model.srcs, model.legend = srcs, legend
    return model


def _body(lines, start):
    """(line number, stripped text) of the statements after the header; comments and blanks dropped."""
    for i in range(start + 1, len(lines)):
        t = lines[i].strip()
        if t and not t.startswith("%%"):
            yield i + 1, t


def _fail(model, no, text, why):
    model.errors.append((no, text, why))


# ---- flowchart ----------------------------------------------------------------------------------------------------
SHAPE_OPEN = [("(((", ")))", "circle"), ("((", "))", "circle"), ("([", "])", "stadium"), ("[(", ")]", "cylinder"),
              ("[[", "]]", "rect"), ("[/", "/]", "para"), ("[\\", "\\]", "para"), ("[/", "\\]", "rect"), ("[\\", "/]", "rect"),
              ("{{", "}}", "rect"), ("{", "}", "diamond"), ("[", "]", "rect"), ("(", ")", "round"), (">", "]", "rect")]
ID_RE = re.compile(r"[\w\u0080-￿]+")
LINKS = [  # (regex, line, head when the arrow end is present, text group)
    (re.compile(r"(<?)--\s+(.+?)\s+-{2,}([>xo]?)(?=\s|$|\|)"), "solid", True),
    (re.compile(r"(<?)-\.\s+(.+?)\s+\.+-([>]?)"), "dotted", True),
    (re.compile(r"(<?)==\s+(.+?)\s+={2,}([>]?)"), "thick", True),
    (re.compile(r"(<?)-\.+-([>]?)"), "dotted", False),
    (re.compile(r"(<?)={2,}([>]?)"), "thick", False),
    (re.compile(r"(<?)-{2,}([>]|[xo](?=\s|$))?"), "solid", False),
    (re.compile(r"~{3,}()"), "invisible", False),
]


def _node_ref(m, s, i):
    """Parse `id`, `id[text]`, `id:::cls` at s[i:]. Returns (id, shape, label | None, cls, next index)."""
    mm = ID_RE.match(s, i)
    if not mm:
        raise SpecError(f"expected a node id at {s[i:i + 25]!r}")
    nid, i = mm.group(0), mm.end()
    shape, label = None, None
    for op, cl, sh in SHAPE_OPEN:
        if s.startswith(op, i):
            j = i + len(op)
            if s[j:j + 1] == '"':
                k = s.find('"', j + 1)
                if k < 0:
                    raise SpecError(f"unterminated quote in label of {nid}")
                end = s.find(cl, k + 1)
                raw = s[j:k + 1]
            else:
                end = s.find(cl, j)
                raw = s[j:end] if end >= 0 else ""
            if end < 0:
                continue
            shape, label, i = sh, clean(raw), end + len(cl)
            break
    cls = ""
    if s.startswith(":::", i):
        mm = ID_RE.match(s, i + 3)
        cls, i = mm.group(0), mm.end()
    return nid, shape, label, cls, i


def _flow(lines, start):
    d = lines[start].split()
    m = Model("flow", {"TD": "TB", "TB": "TB", "LR": "LR", "RL": "RL", "BT": "BT"}.get(d[1] if len(d) > 1 else "TB", "TB"))
    stack = []

    def declare(nid, shape, label, cls):
        n = m.nodes.get(nid)
        if n is None:
            n = m.nodes[nid] = Node(nid, label if label is not None else nid, shape or "rect", parent=stack[-1] if stack else "")
        else:
            if label is not None:
                n.label = label
            if shape:
                n.shape = shape
            if stack and not n.parent:                      # listed inside a subgraph: it belongs there
                n.parent = stack[-1]
        if cls:
            n.cls = cls
        return nid

    for no, t in _body(lines, start):
        try:
            if t.startswith("subgraph"):
                rest = t[8:].strip()
                mm = re.match(r'^([\w\u0080-￿]+)\s*\[(.*)\]$', rest)
                gid, title = (mm.group(1), clean(mm.group(2))) if mm else (re.sub(r"\W+", "_", rest.strip('"')) or f"g{len(m.groups)}", clean(rest))
                m.groups[gid] = Group(gid, title, stack[-1] if stack else "")
                stack.append(gid)
            elif t == "end":
                if not stack:
                    raise SpecError("`end` without subgraph")
                stack.pop()
            elif re.match(r"direction\s+\w+$", t) or COSMETIC.match(t):
                continue
            elif t.startswith("classDef"):
                names, _, props = t[8:].strip().partition(" ")
                for nm in names.split(","):
                    m.classes[nm.strip()] = parse_style(props)
            elif t.startswith("class "):
                ids, _, cname = t[6:].strip().rpartition(" ")
                for nid in ids.split(","):
                    declare(nid.strip(), None, None, cname)
            elif t.startswith("style "):
                nid, _, props = t[6:].strip().partition(" ")
                st = parse_style(props)
                n = declare(nid, None, None, "")
                m.nodes[n].fill, m.nodes[n].stroke = st.get("fill", ""), st.get("stroke", "")
            elif re.match(r"(click|callback|call)\b", t):
                raise SpecError("`click` is not supported (interaction does not exist in draw.io files)")
            else:
                _flow_statement(m, t, declare)
        except SpecError as e:
            _fail(m, no, t, str(e))
    if stack:
        _fail(m, len(lines), "", f"subgraph {stack[-1]} is never closed with `end`")
    for gid in m.groups:                                       # an edge to a subgraph id is not a node of its own
        n = m.nodes.get(gid)
        if n and n.label == gid and n.shape == "rect" and not n.cls:
            del m.nodes[gid]
    return m


def _flow_statement(m, s, declare):
    i, prev, pending = 0, None, None
    while True:
        group = []
        while True:
            while i < len(s) and s[i] == " ":
                i += 1
            nid, shape, label, cls, i = _node_ref(m, s, i)
            group.append(declare(nid, shape, label, cls))
            while i < len(s) and s[i] == " ":
                i += 1
            if s.startswith("&", i):
                i += 1
                continue
            break
        if pending:
            for a in prev:
                for b in group:
                    if pending["line"] != "invisible":
                        m.edges.append(Edge(a, b, **pending))
        prev = group
        if i >= len(s):
            return
        pending = None
        for rx, line, texty in LINKS:
            mm = rx.match(s, i)
            if mm:
                gs = mm.groups()
                both, text, head = bool(gs[0]), (clean(gs[1]) if texty else ""), gs[-1] or ""
                i = mm.end()
                lm = re.match(r"\s*\|(.*?)\|", s[i:])
                if lm:
                    text, i = clean(lm.group(1)), i + lm.end()
                hd = {"x": "cross", "o": "oval", ">": ""}.get(head, "none")
                if line == "invisible":
                    hd = "none"
                pending = dict(label=text, kind="flow" if hd != "none" else "plain", line=line, head=hd if hd in ("cross", "oval") else "", both=both)
                break
        if pending is None:
            raise SpecError(f"cannot read an edge at {s[i:i + 25]!r} (use -->, ---, -.->, ==>, -->|label|)")


# ---- state diagram ------------------------------------------------------------------------------------------------
def _state(lines, start):
    m = Model("state")
    stack, counter = [], [0]

    def scope():
        return stack[-1] if stack else ""

    titles = {}                                      # `state "Long name" as X` seen before X turns out to be a composite

    def state(sid):
        if sid == "[*]":
            return None
        if sid not in m.nodes and sid not in m.groups:   # an edge to a composite state ends at the composite itself
            m.nodes[sid] = Node(sid, sid, "round", parent=scope())
        return sid

    def pseudo(kind, as_target):
        counter[0] += 1
        nid = f"{kind}_{scope() or 'root'}_{counter[0]}"
        m.nodes[nid] = Node(nid, "", kind, parent=scope())
        return nid

    note_for = None
    for no, t in _body(lines, start):
        try:
            if note_for:
                if re.match(r"end\s*note", t, re.I):
                    note_for = None
                else:
                    m.nodes[note_for[0]].label += ("\n" if m.nodes[note_for[0]].label else "") + clean(t)
                continue
            mm = re.match(r"direction\s+(\w+)$", t)
            if mm:
                m.direction = {"TD": "TB"}.get(mm.group(1), mm.group(1))
                continue
            if COSMETIC.match(t):
                continue
            mm = re.match(r'state\s+"([^"]+)"\s+as\s+(\w+)$', t)
            if mm:
                titles[mm.group(2)] = clean(mm.group(1))
                if mm.group(2) in m.groups:
                    m.groups[mm.group(2)].label = titles[mm.group(2)]
                else:
                    state(mm.group(2))
                    m.nodes[mm.group(2)].label = titles[mm.group(2)]
                continue
            mm = re.match(r"state\s+(\w+)\s*<<(choice|fork|join)>>$", t)
            if mm:
                m.nodes[mm.group(1)] = Node(mm.group(1), "", "diamond" if mm.group(2) == "choice" else "fork", parent=scope())
                continue
            mm = re.match(r"state\s+(\w+)\s*\{$", t)
            if mm:
                gid = mm.group(1)
                m.nodes.pop(gid, None)
                m.groups[gid] = Group(gid, titles.get(gid, gid), scope())
                stack.append(gid)
                continue
            mm = re.match(r"state\s+(\w+)$", t)
            if mm:
                state(mm.group(1))
                continue
            if t == "}":
                if not stack:
                    raise SpecError("`}` without a composite state")
                stack.pop()
                continue
            mm = re.match(r"note\s+(?:left|right|top|bottom)\s+of\s+(\w+)\s*(?::\s*(.*))?$", t, re.I)
            if mm:
                nid = f"note_{len(m.nodes)}"
                m.nodes[nid] = Node(nid, clean(mm.group(2) or ""), "note", "note", parent=scope())
                state(mm.group(1))
                m.edges.append(Edge(nid, mm.group(1), kind="plain", line="dotted", head="none"))
                if mm.group(2) is None:
                    note_for = (nid,)
                continue
            mm = re.match(r"(\[\*\]|\w+)\s*-->\s*(\[\*\]|\w+)\s*(?::\s*(.*))?$", t)
            if mm:
                a, b, label = mm.group(1), mm.group(2), clean(mm.group(3) or "")
                a_id = state(a) or pseudo("start", False)
                b_id = state(b) or pseudo("end", True)
                m.edges.append(Edge(a_id, b_id, label))
                continue
            mm = re.match(r"(\w+)\s*:\s*(.+)$", t)
            if mm:
                state(mm.group(1))
                m.nodes[mm.group(1)].label += "\n" + clean(mm.group(2))
                continue
            if t == "--":
                raise SpecError("concurrent regions (`--`) are not supported; draw them as separate composite states")
            raise SpecError("cannot read this line (use `A --> B : event`, `[*] --> A`, `state X { ... }`, `state \"Text\" as X`, `note right of X : text`)")
        except SpecError as e:
            _fail(m, no, t, str(e))
    if stack:
        _fail(m, len(lines), "", f"composite state {stack[-1]} is never closed with `}}`")
    return m


# ---- sequence diagram ---------------------------------------------------------------------------------------------
MSG = re.compile(r"^([\w\u0080-￿]+)\s*(--?>>|--?>|--?x|--?\))\s*([+-]?)([\w\u0080-￿]+)\s*:\s*(.*)$")
FRAMES = {"alt": "alt", "opt": "opt", "loop": "loop", "par": "par", "critical": "critical", "break": "break", "rect": ""}


def _sequence(lines, start):
    m = Model("sequence")
    open_frames = []

    def part(pid, label=None, actor=False):
        for k, (i, l, a) in enumerate(m.participants):
            if i == pid:
                if label:
                    m.participants[k] = (i, label, a or actor)
                return
        m.participants.append((pid, label or pid, actor))

    for no, t in _body(lines, start):
        try:
            mm = re.match(r"(participant|actor)\s+([\w\u0080-￿]+)(?:\s+as\s+(.+))?$", t)
            if mm:
                part(mm.group(2), clean(mm.group(3)) if mm.group(3) else None, mm.group(1) == "actor")
                continue
            if t == "autonumber":
                m.numbered = True
                continue
            if COSMETIC.match(t) or re.match(r"(activate|deactivate)\s", t):
                continue
            mm = re.match(r"note\s+(over|left of|right of)\s+([\w\u0080-￿]+)(?:\s*,\s*([\w\u0080-￿]+))?\s*:\s*(.*)$", t, re.I)
            if mm:
                for p in (mm.group(2), mm.group(3)):
                    if p:
                        part(p)
                m.items.append(("note", mm.group(1).lower(), [p for p in (mm.group(2), mm.group(3)) if p], clean(mm.group(4))))
                continue
            mm = re.match(r"(alt|opt|loop|par|critical|break|rect)\b\s*(.*)$", t)
            if mm:
                kind = FRAMES[mm.group(1)]
                open_frames.append(kind)
                m.items.append(("open", kind, "" if mm.group(1) == "rect" else clean(mm.group(2))))
                continue
            mm = re.match(r"(else|and|option)\b\s*(.*)$", t)
            if mm:
                if not open_frames:
                    raise SpecError(f"`{mm.group(1)}` outside alt/par/critical")
                m.items.append(("else", clean(mm.group(2))))
                continue
            if t == "end":
                if not open_frames:
                    raise SpecError("`end` without alt/opt/loop/par")
                open_frames.pop()
                m.items.append(("close",))
                continue
            mm = MSG.match(t)
            if mm:
                a, arrow, _, b, text = mm.groups()
                part(a)
                part(b)
                m.items.append(("msg", a, b, clean(text), "return" if arrow.startswith("--") else "call", arrow.endswith("x")))
                continue
            raise SpecError("cannot read this line (use `A->>B: text`, `A-->>B: text`, `Note over A,B: text`, `alt/else/end`, `loop/end`, `participant A as Label`)")
        except SpecError as e:
            _fail(m, no, t, str(e))
    if open_frames:
        _fail(m, len(lines), "", "an alt/opt/loop/par block is never closed with `end`")
    return m


# ---- class diagram ------------------------------------------------------------------------------------------------
REL = re.compile(r'^([\w\u0080-￿~]+)\s*(?:"([^"]*)")?\s*(<\|--|--\|>|\*--|--\*|o--|--o|<--|-->|<\.\.|\.\.>|<\|\.\.|\.\.\|>|--|\.\.)\s*(?:"([^"]*)")?\s*([\w\u0080-￿~]+)\s*(?::\s*(.*))?$')
REL_KIND = {"<|--": ("inherit", True), "--|>": ("inherit", False), "*--": ("compose", False), "--*": ("compose", True),
            "o--": ("aggregate", False), "--o": ("aggregate", True), "-->": ("assoc", False), "<--": ("assoc", True),
            "..>": ("depend", False), "<..": ("depend", True), "..|>": ("realize", False), "<|..": ("realize", True),
            "--": ("plain", False), "..": ("plain", False)}


def _generic(name):
    return re.sub(r"~([^~]*)~", r"<\1>", name)


def _class(lines, start):
    m = Model("class")
    current = None

    def cls(name):
        name = _generic(name)
        if name not in m.nodes:
            m.nodes[name] = Node(name, name, "class")
        return m.nodes[name]

    def member(n, text):
        text = _generic(clean(text))
        mm = re.match(r"^<<(.+)>>$", text)
        if mm:
            n.stereotype = mm.group(1)
        else:
            (n.methods if "(" in text else n.members).append(text)

    for no, t in _body(lines, start):
        try:
            if current:
                body, closes = (t[:-1].strip(), True) if t.endswith("}") else (t, False)
                if body:
                    member(current, body)
                current = None if closes else current
                continue
            mm = re.match(r"direction\s+(\w+)$", t)
            if mm:
                m.direction = {"TD": "TB"}.get(mm.group(1), mm.group(1))
                continue
            if COSMETIC.match(t):
                continue
            mm = re.match(r"class\s+([\w\u0080-￿~]+)(?:\s*<<(\w+)>>)?\s*(?:\{\s*(.*))?$", t)
            if mm:
                n = cls(mm.group(1))
                if mm.group(2):
                    n.stereotype = mm.group(2)
                if mm.group(3) is not None:
                    rest = mm.group(3)
                    body, closes = (rest[:-1].strip(), True) if rest.endswith("}") else (rest, False)
                    if body:
                        member(n, body)
                    current = None if closes else n
                continue
            mm = re.match(r"<<(\w+)>>\s+([\w\u0080-￿~]+)$", t)
            if mm:
                cls(mm.group(2)).stereotype = mm.group(1)
                continue
            mm = REL.match(t)
            if mm:
                a, ca, op, cb, b, label = mm.groups()
                kind, flip = REL_KIND[op]
                a, b = cls(a).id, cls(b).id
                dashed = ".." in op
                if flip:
                    a, b, ca, cb = b, a, cb, ca
                m.edges.append(Edge(a, b, clean(label or ""), kind, "dotted" if dashed else "solid", src_card=ca or "", tgt_card=cb or ""))
                continue
            mm = re.match(r"([\w\u0080-￿~]+)\s*:\s*(.+)$", t)
            if mm:
                member(cls(mm.group(1)), mm.group(2))
                continue
            if re.match(r"(namespace|note|link|click|callback)\b", t):
                raise SpecError("not supported here; use `class X { ... }`, relations like `A <|-- B`, and `%%` comments")
            raise SpecError("cannot read this line (use `class X { +field: type  +method() ret }`, `A <|-- B`, `A *-- B : label`, `A --> B`)")
        except SpecError as e:
            _fail(m, no, t, str(e))
    if current:
        _fail(m, len(lines), "", f"class {current.id} is never closed with `}}`")
    return m


# ---- ER diagram ---------------------------------------------------------------------------------------------------
ER_LEFT = {"||": "ERmandOne", "|o": "ERzeroToOne", "}o": "ERzeroToMany", "}|": "ERoneToMany"}
ER_RIGHT = {"||": "ERmandOne", "o|": "ERzeroToOne", "o{": "ERzeroToMany", "|{": "ERoneToMany"}
ER_REL = re.compile(r'^([\w\u0080-￿]+)\s+(\|\||\|o|\}o|\}\|)(--|\.\.)(\|\||o\||o\{|\|\{)\s+([\w\u0080-￿]+)\s*(?::\s*(.*))?$')


def _er(lines, start):
    m = Model("er")
    current = None

    def attribute(n, text):
        mm = re.match(r'^(\S+)\s+(\S+)((?:\s+(?:PK|FK|UK)\b,?)*)\s*(?:"([^"]*)")?$', text)
        if not mm:
            raise SpecError("attribute must be `type name [PK|FK|UK] [\"comment\"]`")
        typ, name, keys, comment = mm.group(1), mm.group(2), " ".join(mm.group(3).replace(",", " ").split()), mm.group(4)
        n.members.append(f"{name}: {typ}" + (f"  [{keys}]" if keys else "") + (f"  - {comment}" if comment else ""))

    def ent(name):
        if name not in m.nodes:
            m.nodes[name] = Node(name, name, "class")
        return m.nodes[name]

    for no, t in _body(lines, start):
        try:
            if current:
                body, closes = (t[:-1].strip(), True) if t.endswith("}") else (t, False)
                if body:
                    attribute(current, body)
                current = None if closes else current
                continue
            if COSMETIC.match(t):
                continue
            mm = re.match(r"^([\w\u0080-￿]+)\s*\{\s*(.*)$", t)
            if mm:
                current = ent(mm.group(1))
                rest = mm.group(2)
                body, closes = (rest[:-1].strip(), True) if rest.endswith("}") else (rest, False)
                if body:
                    attribute(current, body)
                current = None if closes else current
                continue
            mm = ER_REL.match(t)
            if mm:
                a, left, line, right, b, label = mm.groups()
                ent(a), ent(b)
                m.edges.append(Edge(a, b, clean(label or ""), "er", "dotted" if line == ".." else "solid", er=(ER_LEFT[left], ER_RIGHT[right])))
                continue
            raise SpecError("cannot read this line (use `A ||--o{ B : label` or `A { type name PK }`; cardinalities || |o }o }| on the left, || o| o{ |{ on the right)")
        except SpecError as e:
            _fail(m, no, t, str(e))
    if current:
        _fail(m, len(lines), "", f"entity {current.id} is never closed with `}}`")
    return m
