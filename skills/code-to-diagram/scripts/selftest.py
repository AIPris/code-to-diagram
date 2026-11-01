#!/usr/bin/env python3
"""Regression check for the skill's own scripts: python selftest.py  (no draw.io needed, writes to a temp dir).

Builds one small sample of every diagram type, validates the XML, and checks a few properties that earlier versions
got wrong: edges glued to shapes, labels with "<" survive, no edge or label ends up pointing at a missing shape, and the
lines come out clean (no kinks, no edges on top of each other, none through a node or a container title).
"""
import contextlib
import io
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import c2d  # noqa: E402
import deps  # noqa: E402
import drawio_lib  # noqa: E402
import emit  # noqa: E402
import facts  # noqa: E402
import labels  # noqa: E402
import mermaid  # noqa: E402
import metrics  # noqa: E402
import routing  # noqa: E402
from validate_drawio import validate  # noqa: E402

SAMPLES = {
    "flow": """flowchart TD
  A([Request]) --> B{Switch on?}
  B -->|yes| C[Check List<Order>]
  B -->|no| D([404]):::red
  C --> E{Origin ok?}
  E -->|no| F([403]):::red
  E -->|yes| G([201])
  subgraph S [Handler]
    C
    E
  end
""",
    "decisions": """flowchart TD
  A([POST /login]) --> B{Body valid?}
  B -->|yes| C[Look up user]
  B -->|no| E1([400])
  C --> D{User found?}
  D -->|yes| F[Verify password]
  D -->|no| E2([401])
  F --> G{Hash matches?}
  G -->|yes| H[Create session]
  G -->|no| I[Count failed attempt]
  I --> J{More than 5?}
  J -->|yes| K[Lock account]
  J -->|no| E2
  K --> E3([423])
  H --> M([200])
  subgraph S [Session handling]
    H
  end
""",
    "calls": """flowchart TD
  main --> load_config
  main --> run_server
  run_server --> handle
  handle --> auth
  handle --> route
  route --> list_items
  route --> create_item
  create_item --> validate
  create_item --> db_write
  list_items --> db_read
  auth --> db_read
  validate --> schema
  db_write --> log
  db_read --> log
  handle --> log
  load_config --> schema
""",
    "fan": """flowchart TD
  hub --> a
  hub --> b
  hub --> c
  hub --> d
  hub --> e
  hub --> f
  hub --> g
  hub --> h
  hub --> i
""",
    "state": """stateDiagram-v2
  [*] --> LOADING
  LOADING --> MENU : boot
  MENU --> PLAYING : start
  PLAYING --> PAUSED : pause
  PAUSED --> PLAYING : resume
  PLAYING --> [*]
""",
    "sequence": """sequenceDiagram
  autonumber
  participant U as User
  participant A as API
  U->>A: POST /login
  alt valid
    A-->>U: 200
  else invalid
    A-->>U: 401
  end
  A->>A: audit()
  Note over U,A: done
""",
    "class": """classDiagram
  class Body { <<interface>>
    +x: number }
  class Entity { +hit() void }
  Body <|-- Entity
  Entity "1" --> "*" Body : holds
""",
    "er": """erDiagram
  users ||--o{ posts : writes
  users { INTEGER id PK
    TEXT name }
  posts { INTEGER id PK
    INTEGER user_id FK }
""",
}


def tree(root, files):
    """Create {relative path: text} under root; -> the created paths, in order."""
    out = []
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        out.append(path)
    return out


def build_output(mmd):
    """What `c2d.py build --no-png` prints for a spec, and its exit code (None if it did not exit normally)."""
    out, code = io.StringIO(), None
    with contextlib.redirect_stdout(out):
        try:
            c2d.cmd_build([mmd, "--no-png"])
        except SystemExit as e:
            code = e.code
    return out.getvalue(), code


def many_missing_names(tmp):
    """Any number of names missing from the source is a warning: the build prints ten, a summary, and carries on."""
    problems = []
    for n in (0, 1, 10, 11, 25):
        nodes = "".join(f'  N{i}["missing_name_{i}"]\n' for i in range(n))
        _, mmd = tree(tmp, {f"m{n}/source.py": "", f"m{n}/spec.mmd": f'%% src: "{os.path.join(tmp, f"m{n}", "source.py")}"\nflowchart TB\n{nodes}  K[ok]\n'})
        text, code = build_output(mmd)
        shown = len(re.findall(r"^  missing_name_\d+ ", text, re.M))
        if code != 0:
            problems.append(f"{n} missing: build exited {code}")
        if shown != min(n, 10):
            problems.append(f"{n} missing: {shown} listed, expected {min(n, 10)}")
        if (f"... +{n - 10} more" in text) != (n > 10):
            problems.append(f"{n} missing: wrong overflow summary")
    return problems


def names_missing(label, *paths):
    """The names in a node label that `facts.check` cannot find under paths."""
    res = facts.check(mermaid.parse(f'flowchart TB\n A["{label}"]\n'), list(paths))
    return [tok for tok, _ in res[1]]


def whole_name_matching(tmp):
    """A name counts as found only as a whole identifier or file name, not inside a longer one."""
    tree(tmp, {"a/source.py": "def load_users():\n    my_user_id = 1\n    ids = user_ids\n", "a/main.js": "", "b/domain.js": ""})
    a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
    problems = []
    for label, where, want in (("load_users", a, []), ("load_user", a, ["load_user"]),             # singular of a plural name
                               ("my_user_id", a, []), ("user_id", a, ["user_id"]),                 # suffix of a longer name
                               ("user_ids", a, []), ("load_users_all", a, ["load_users_all"]),    # prefix of the claimed name
                               ("main.js", a, []), ("main.js", b, ["main.js"])):                  # domain.js is not main.js
        got = names_missing(label, where)
        if got != want:
            problems.append(f"{label} in {os.path.basename(where)}/: missing {got}, expected {want}")
    return problems


def unlisted_extensions(tmp):
    """Any text file is searched for names, whatever its extension; a binary file is reported as not read."""
    tree(tmp, {"mod.psm1": "function Get-UserThing { param($UserName) }\n", "app.vb": "Sub RunJob_Now()\n"})
    with open(os.path.join(tmp, "blob.bin"), "wb") as fh:
        fh.write(bytes([0, 1, 2]))
    res = facts.check(mermaid.parse('flowchart TB\n A["UserThing"]\n B["RunJob_Now"]\n'), [tmp])
    problems = []
    if res[1]:
        problems.append(f"names in .psm1/.vb files reported missing: {res[1]}")
    if res[4] != ["blob.bin"]:
        problems.append(f"skipped files: {res[4]}, expected ['blob.bin']")
    return problems


def container_titles(tmp):
    """A route through a container's title text is counted; the build then routes edges round titles where that stays straight."""
    title, beside = (60, 40, 80, 30), (150, 40, 80, 30)
    seen = metrics.evaluate({0: [(100, 0), (100, 100)]}, {}, [("a", "b", False)], [], 0, (200, 200), None, {"g": title, "h": beside})["title_hits"]
    problems = [] if seen == 1 else [f"a route through a title: title_hits {seen}, expected 1"]
    for what, spec in (("an edge entering a container", """flowchart TD
 S([start])
 subgraph G [A rather long container title here]
  X[step one]
  Y{check?}
  X --> Y
 end
 S --> X
 Y -->|no| Z([done])
"""), ("a loop on a node of a container", """flowchart LR
 subgraph G [Group]
  A[one]
  B[two]
 end
 A --> A
""")):
        model = mermaid.parse(spec)
        emit.build(model)
        report = model.layout_report
        if report["title_hits"] or report["detour"] or report["jogs"] > 1:         # the one kink allowed is Y -> Z, beside the diamond
            problems.append(f"{what}: {report['title_hits']} titles struck, {report['jogs']} kinks, {report['detour']} detours")
    return problems


def hugging_counted(tmp):
    """A route closer than metrics.CLEARANCE to a node or a container's title text, but not through it, is a near miss, and the build line lists it."""
    node, title = (100, 100, 100, 60), (60, 40, 80, 30)
    problems = []
    for what, ends, line, titles, want in (                                   # -> (node_hits, title_hits, near)
            ("2 px under a node", ("a", "b"), [(50, 162), (250, 162)], None, (0, 0, 1)),
            ("along a node's border", ("a", "b"), [(50, 160), (250, 160)], None, (0, 0, 1)),
            ("2 px inside a node's border", ("a", "b"), [(50, 158), (250, 158)], None, (0, 0, 1)),
            ("10 px under a node", ("a", "b"), [(50, 170), (250, 170)], None, (0, 0, 0)),
            ("through a node", ("a", "b"), [(50, 130), (250, 130)], None, (1, 0, 0)),
            ("2 px under the node it starts at", ("n", "b"), [(50, 162), (250, 162)], None, (0, 0, 0)),
            ("2 px beside a title", ("a", "b"), [(142, 0), (142, 100)], {"g": title}, (0, 0, 1)),
            ("through a title", ("a", "b"), [(100, 0), (100, 100)], {"g": title}, (0, 1, 0)),
            ("20 px beside a title", ("a", "b"), [(160, 0), (160, 100)], {"g": title}, (0, 0, 0)),
            ("2 px beside the title of the container it ends at", ("a", "g"), [(142, 0), (142, 100)], {"g": title}, (0, 0, 0))):
        r = metrics.evaluate({0: line}, {"n": node}, [(*ends, False)], ["n"] if titles is None else [], 0, (300, 300), None, titles)
        if (r["node_hits"], r["title_hits"], r["near"]) != want:
            problems.append(f"{what}: (node_hits, title_hits, near) {(r['node_hits'], r['title_hits'], r['near'])}, expected {want}")
    clean = metrics.evaluate({0: [(50, 170), (250, 170)]}, {"n": node}, [("a", "b", False)], ["n"], 0, (300, 300))
    hug = metrics.evaluate({0: [(50, 162), (250, 162)]}, {"n": node}, [("a", "b", False)], ["n"], 0, (300, 300), label_near=1)
    if hug["label_near"] != 1 or hug["score"] <= clean["score"]:
        problems.append(f"near misses do not cost anything: score {hug['score']} against {clean['score']}")
    found = " | ".join(c2d.layout_findings(hug))
    for phrase in (f"within {metrics.CLEARANCE} px of a node or container title", "hug a node or container header"):
        if phrase not in found:
            problems.append(f"the build line lacks {phrase!r}: {found!r}")
    if c2d.layout_findings(clean):
        problems.append(f"a clean report is listed as {c2d.layout_findings(clean)}")
    return problems


def labels_off_headers(tmp):
    """A label keeps off the title bar of a container and a few px clear of it and of nodes; where it cannot, it is counted."""
    problems = []
    edges, boxes = [("a", "b", False)], {"a": (80, -40, 40, 40), "b": (80, 400, 40, 40), "n": (110, 61, 50, 30)}
    long, short = {0: [(100, 0), (100, 400)]}, {0: [(100, 0), (100, 100)]}          # a short edge has room for one spot only: the middle
    for what, line, obstacles, bars, want in (                                          # -> (moved off the middle, touching, hugging)
            ("a bar across the middle", long, [], [(0, 190, 200, 30)], (True, 0, 0)),
            ("a bar 1.5 px from the middle", long, [], [(0, 211, 200, 30)], (True, 0, 0)),
            ("a bar along the whole edge", long, [], [(0, 0, 200, 400)], (None, 1, 0)),
            ("a bar across the only spot", short, [], [(0, 45, 200, 30)], (False, 1, 0)),
            ("a bar 1.5 px from the only spot", short, [], [(0, 61, 200, 30)], (False, 0, 1)),
            ("a bar 10 px from the only spot", short, [], [(0, 70, 200, 30)], (False, 0, 0)),
            ("a node 1.5 px from the only spot", short, ["n"], [], (False, 0, 1))):
        pos, touching, hugging = labels.place(line, edges, ["yes"], boxes, obstacles, bars)
        got = (pos[0] != 0 if want[0] is not None else None, touching, hugging)
        if got != want:
            problems.append(f"{what}: (moved, touching, hugging) {got}, expected {want}")
    spec = "flowchart TD\n S([start]) -->|go| X\n subgraph G [Group]\n  X[step one]\n end\n"                  # the label sits where the edge crosses the title bar
    model, calls, real = mermaid.parse(spec), [], labels.place
    labels.place = lambda *args: calls.append((args, real(*args))) or calls[-1][1]
    try:
        emit.build(model)
    finally:
        labels.place = real
    for (lines, _, texts, _, _, bars), (pos, touching, _) in calls:
        if len(bars) != 1:
            problems.append(f"emit passes {len(bars)} title bars to the label placement, expected 1")
        for idx, rel in pos.items():
            on_bar = any(labels._overlap(labels.label_rect(texts[idx], lines[idx], (rel + 1) / 2), bar) for bar in bars)
            if on_bar and not touching:
                problems.append("a label lies on a title bar and is not counted")
    if model.layout_report["label_conflicts"] or model.layout_report["label_near"]:
        problems.append(f"an edge into a container: {model.layout_report['label_conflicts']} labels touch and {model.layout_report['label_near']} hug a title bar")
    return problems


def title_hugs_rerouted(tmp):
    """An edge that runs along a container's title text, closer than metrics.CLEARANCE, gets a second route clear of it, as one that strikes through does."""
    boxes = {"S": (200, 0, 120, 40), "G": (100, 100, 300, 200), "X": (130, 160, 240, 50)}
    title = (200, 100, 57, 30)                                                    # the straight edge S -> X runs 3 px from its right end
    problems = []
    for tidy, want in ((False, 1), (True, 0)):
        plan = routing.route_edges(boxes, [("S", "X", False)], {}, "TB", parents={"X": "G"}, tidy=tidy, titles={"G": title})
        line = labels.polyline(plan[0], boxes["S"], boxes["X"])
        got, bends = metrics.near_misses(line, [title], 0), metrics.bends_and_jogs(line)
        if got != want or metrics.titles_struck(line, [title]) or bends != (0, 0):
            problems.append(f"{'with' if tidy else 'without'} the second route: {got} near misses, {metrics.titles_struck(line, [title])} strikes, (bends, kinks) {bends}; expected {want}, 0, (0, 0)")
    return problems


def inline_exits(tmp):
    """Two exits of a decision on opposite sides at one height are counted; staggered or same-side exits are not."""
    box = (100, 100, 100, 60)
    got = [metrics._inline_exits(box, exits) for exits in ([(105, 130), (195, 131)], [(105, 120), (195, 150)], [(105, 130), (110, 131)], [(150, 100), (150, 160)])]
    problems = [] if got == [1, 0, 0, 1] else [f"inline pairs {got}, expected [1, 0, 0, 1]"]
    chain = {0: [(150, 0), (150, 100)], 1: [(150, 160), (150, 260)]}                # in at the top, out at the bottom: the ordinary way through a decision
    through = metrics.evaluate(chain, {"d": box}, [("a", "d", False), ("d", "b", False)], [], 0, (300, 300), None, (), ["d"])["inline"]
    return problems + ([] if through == 0 else [f"a decision with one entry and one exit in line: inline {through}, expected 0"])


DECISIONS = {
    "an edge leaving a decision inside a container": """flowchart TD
 S([start])
 subgraph G [A rather long container title here]
  X[step one]
  Y{check?}
  X --> Y
 end
 S --> X
 Y -->|no| Z([done])
""",
    "three exits": """flowchart TD
 A[Review] --> B{Decision}
 B -->|approved| C[Ship]
 B -->|aborted| D[Cancel]
 B -->|retry| E[Again]
""",
    "four exits": """flowchart TD
 A[Review] --> B{Decision}
 B -->|a| C[One]
 B -->|b| D[Two]
 B -->|c| E[Three]
 B -->|d| F[Four]
""",
    "three entries": """flowchart TD
 A[x] --> D{d}
 B[y] --> D
 C[z] --> D
 D -->|yes| E[e]
 D -->|no| F[f]
""",
    "a retry loop with a third exit": """flowchart TD
 A[start] --> B[work]
 B --> C{ok?}
 C -->|yes| D[done]
 C -->|no| B
 C -->|abort| E([stop])
""",
    "decisions in a chain": """flowchart TD
 A([in]) --> B{one?}
 B -->|yes| C{two?}
 B -->|no| X([x])
 C -->|yes| D{three?}
 C -->|no| Y([y])
 D -->|yes| Z([z])
 D -->|no| W([w])
""",
    "loops round decisions, left to right": """flowchart LR
 A[read] --> B{valid?}
 B -->|yes| C[save]
 B -->|no| D[log]
 D --> A
 C --> E{more?}
 E -->|yes| A
 E -->|no| F([end])
""",
}
RETRY = """flowchart {}
 A[start] --> B[work]
 B --> C{{ok?}}
 C -->|yes| D[done]
 C -->|no| B
"""
LOOP = """flowchart {}
 A[a] --> B{{again?}}
 B -->|loop| B
 B -->|done| C[c]
"""
ARRIVAL = {"TD": (0.5, 0), "BT": (0.5, 1), "LR": (0, 0.5), "RL": (1, 0.5)}      # where the way in meets a decision, whichever way the flow runs
for _d in ARRIVAL:
    DECISIONS[f"a retry loop, {_d}"] = RETRY.format(_d)
    DECISIONS[f"a loop on a decision, {_d}"] = LOOP.format(_d)
VERTICES = {(0.5, 0), (0.5, 1), (0, 0.5), (1, 0.5)}


def decision_anchors(tmp, spec):
    """-> ([(decision id, 'exit'|'entry', (fx, fy))], layout report): where every edge attaches to a decision in the drawing of a spec."""
    model = mermaid.parse(spec)
    diagram, _ = emit.build(model)
    cells = list(ET.parse(diagram.save(os.path.join(tmp, "decisions.drawio"))).getroot().iter("mxCell"))
    diamonds = {c.get("id") for c in cells if c.get("vertex") and "rhombus" in c.get("style", "")}
    out = []
    for c in cells:
        if not c.get("edge"):
            continue
        style = dict(kv.split("=", 1) for kv in c.get("style", "").split(";") if "=" in kv)
        for end, node in (("exit", c.get("source")), ("entry", c.get("target"))):
            if node in diamonds:
                out.append((node, end, (round(float(style[end + "X"]), 3), round(float(style[end + "Y"]), 3))))
    return out, model.layout_report


def vertex_fans(tmp):
    """Edges leaving one vertex of a decision share its stub on purpose and fan out after it; an arrival lying on top of a departure is not that."""
    box = (100, 100, 100, 60)
    fan = {0: [(150, 160), (150, 200), (250, 200), (250, 260)], 1: [(150, 160), (150, 230), (60, 230), (60, 260)]}
    clash = {0: [(150, 160), (150, 260)], 1: [(150, 300), (150, 160)]}
    got = [metrics.evaluate(lines, {"d": box}, edges, [], 0, (400, 400), None, (), ["d"])["shared"]
           for lines, edges in ((fan, [("d", "a", False), ("d", "b", False)]), (clash, [("d", "a", False), ("c", "d", False)]))]
    plain = metrics.evaluate(fan, {"d": box}, [("d", "a", False), ("d", "b", False)], [], 0, (400, 400))["shared"]       # no decision: the same lines are two edges on top of each other
    return [] if got == [0, 1] and plain == 1 else [f"shared runs: fan {got[0]}, arrival on a departure {got[1]}, fan at a plain node {plain}; expected 0, 1, 1"]


def decision_vertices(tmp):
    """An edge attaches to a decision at one of its four vertices, never part-way along a side; no vertex serves both an arrival and a departure
    (the two would read as one line through the corner); and such a decision leaves no exits in line, edges on top of each other, or edges through nodes."""
    problems = []
    for what, spec in DECISIONS.items():
        anchors, report = decision_anchors(tmp, spec)
        bad = {k: report[k] for k in ("inline", "shared", "node_hits") if report[k]}      # a fan out from a vertex is not a run on top of another
        if bad:
            problems.append(f"{what}: {bad}")
        off = sorted({a for _, _, a in anchors} - VERTICES)
        if off:
            problems.append(f"{what}: anchors off the vertices {off}")
        sides = {}
        for node, end, a in anchors:
            sides.setdefault((node, a), set()).add(end)
        mixed = sorted(k for k, ends in sides.items() if len(ends) > 1)
        if mixed:
            problems.append(f"{what}: an arrival and a departure share a vertex {mixed}")
        if what.startswith("a retry loop,") and sides.get(("C", ARRIVAL[what[-2:]])) != {"entry"}:        # the way in keeps its vertex, the way back leaves by a side
            problems.append(f"{what}: the vertex the flow arrives at serves {sides.get(('C', ARRIVAL[what[-2:]]))}, expected only the arrival")
    return problems


def sequence_title_legend(tmp):
    """`title` and `%% legend:` are drawn for sequence diagrams too, and a flowchart title keeps its edges glued."""
    problems = []
    for spec, want in (("%% legend: red=error path\nsequenceDiagram\n title Login flow\n A->>B: hi\n", ("Login flow", "error path")),
                       ("flowchart TD\n title Pipeline\n A --> B\n", ("Pipeline",))):
        d, _ = emit.build(mermaid.parse(spec))
        body = open(d.save(os.path.join(tmp, "t.drawio")), encoding="utf-8").read()
        problems += [f"{w!r} missing from the drawing of {spec.splitlines()[0]}" for w in want if w not in body]
    return problems


def canvas_proportions(tmp):
    """The build line gives the aspect ratio and the share of the canvas the nodes cover; a strip long enough to be scaled down gets a hint
    naming a fix that fits it, and a short strip or an ordinary proportion gets none."""
    problems = []
    got = (metrics.ratio(3381, 1001), metrics.aspect_text(3381, 1001), metrics.aspect_text(1001, 3381), metrics.aspect_text(100, 100),
           metrics.fill([(0, 0, 100, 50), (200, 0, 100, 50)], (400, 100)))
    if got != (3.4, "3.4:1", "1:3.4", "1.0:1", 0.25):
        problems.append(f"ratio, aspect_text and fill give {got}")
    chain = lambda direction, n: f"flowchart {direction}\n" + "".join(f" N{i} --> N{i + 1}\n" for i in range(n))
    cases = (("a long chain left to right", chain("LR", 12), "wide strip", "switch to TD"),
             ("a long chain top-down", chain("TD", 16), "tall strip", "switch to LR"),
             ("a wide fan top-down", "flowchart TD\n" + "".join(f" hub --> n{i}\n" for i in range(14)), "wide strip", "group nodes in subgraphs"),
             ("a sequence of ten participants", "sequenceDiagram\n" + "".join(f" participant P{i}\n" for i in range(10)) + " P0->>P9: hi\n", "wide strip", "drop participants"),
             ("two nodes, 2.8:1 but small", chain("LR", 1), None, None),
             ("an ordinary proportion", SAMPLES["calls"], None, None))
    for k, (what, spec, strip, fix) in enumerate(cases):
        (mmd,) = tree(tmp, {f"case{k}/x.mmd": spec})
        model = mermaid.parse(spec)
        _, (w, h) = emit.build(model)
        text, _ = build_output(mmd)
        if f"{int(w)}x{int(h)} px, {metrics.aspect_text(w, h)}" not in text:
            problems.append(f"{what}: the built line lacks {int(w)}x{int(h)} px, {metrics.aspect_text(w, h)}")
        found = re.search(r"^shape: (.*)$", text, re.M)
        hint = found.group(1) if found else None
        if strip is None and hint is not None:
            problems.append(f"{what}: unexpected hint {hint!r}")
        elif strip is not None and not (hint and strip in hint and fix in hint):
            problems.append(f"{what}: hint {hint!r}, expected one with {strip!r} and {fix!r}")
        if ("nodes cover" in text) != (model.kind != "sequence"):
            problems.append(f"{what}: the share of the canvas the nodes cover is {'missing from' if model.kind != 'sequence' else 'reported for'} the built line")
    two = mermaid.parse(chain("LR", 1))                                                  # the share is the drawn nodes' area over the drawn canvas
    diagram, (w, h) = emit.build(two)
    cells = ET.parse(diagram.save(os.path.join(tmp, "two.drawio"))).getroot().iter("mxGeometry")
    area = sum(float(g.get("width")) * float(g.get("height")) for g in cells if g.get("width")) / (w * h)
    if abs(two.layout_report["fill"] - area) > 1e-6:
        problems.append(f"two nodes: fill {two.layout_report['fill']:.4f}, the drawing has {area:.4f}")
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "examples")
    for name in ("import-graph", "build-pipeline"):                                      # shipped examples read well: no hint
        path = os.path.join(root, name, name + ".mmd")
        if os.path.exists(path):
            model = mermaid.parse(c2d.read_spec(path))
            _, (w, h) = emit.build(model)
            if c2d.strip_hint(model, w, h):
                problems.append(f"examples/{name}: {c2d.strip_hint(model, w, h)}")
    return problems


def import_arrows(tmp):
    """A solid arrow between two files is judged by those files' own imports, wherever a same-named file lives."""
    tree(tmp, {"src/one/a.js": "", "src/b.js": "", "src/c.js": "", "src/d.js": "", "src/two/a.js": "import '../b.js';\nimport '../c.js';\nimport '../d.js';\n"})
    src = os.path.join(tmp, "src")
    check = lambda spec: facts.check_edges(mermaid.parse("flowchart TB\n" + spec), [src])
    one = 'A["src/one/a.js"]'
    two = 'A["src/two/a.js"]'
    problems = []
    for name, got, want in (
            ("explicit path, no import behind it", check(f' {one} --> B["src/b.js"]\n A --> C["src/c.js"]\n'), (2, [("A", "B"), ("A", "C")], 0)),
            ("a single arrow is judged too", check(f' {one} --> B["src/b.js"]\n'), (1, [("A", "B")], 0)),
            ("explicit path, real imports", check(f' {two} --> B["src/b.js"]\n A --> C["src/c.js"]\n'), (2, [], 0)),
            ("bare file name that fits two files", check(' A["a.js"] --> B["b.js"]\n A --> C["c.js"]\n'), (0, [], 2)),
            ("group stands for its members", check(f' subgraph G ["core"]\n  B["src/b.js"]\n  C["src/c.js"]\n end\n {two} --> G\n A --> D["src/d.js"]\n'), (2, [], 0))):
        if got != want:
            problems.append(f"{name}: {got}, expected {want}")
    pkgs = {f"{p}/{f}": text for p in ("pkg1", "pkg2") for f, text in (("__init__.py", ""), ("a.py", "from . import b\n"), ("b.py", ""))}
    root = os.path.join(tmp, "pkgs")
    tree(root, pkgs)                                              # what deps draws: bare file names inside a "dir/" subgraph
    spec = deps.mermaid(deps.scan(deps.collect([root], [])), srcs=[root])
    got = facts.check_edges(mermaid.parse(spec), [root])
    if got != (4, [], 0):
        problems.append(f"deps output with two packages of the same file names: {got}, expected (4, [], 0)")
    return problems


def distinct_ids(tmp):
    """Files and directories whose names differ only in - / _ keep separate nodes and groups through the Mermaid round trip."""
    tree(tmp, {"src/foo-bar.js": "import './foo_bar.js';\n", "src/foo_bar.js": "", "x/a_b.js": "import '../x_a/b.js';\n", "x_a/b.js": "",
               "my-dir/p.js": "", "my_dir/q.js": ""})
    graph = deps.scan(deps.collect([tmp], []))
    problems = []
    for groups in (False, True):
        model = mermaid.parse(deps.mermaid(graph, groups=groups, drop_entry_edges=False))
        ends = {(model.nodes[e.src].label, model.nodes[e.tgt].label) for e in model.edges}
        parent = {n.label: model.groups[n.parent].label.rstrip("/").rsplit("/", 1)[-1] for n in model.nodes.values() if n.parent}
        for what, got, want in (("errors", model.errors, []), ("nodes", len(model.nodes), 6), ("groups", len(model.groups), 5 if groups else 0),
                                ("edges", ends, {("foo-bar.js", "foo_bar.js"), ("a_b.js", "b.js")}),
                                ("directory of p.js and q.js", (parent.get("p.js"), parent.get("q.js")), ("my-dir", "my_dir") if groups else (None, None))):
            if got != want:
                problems.append(f"{'with' if groups else 'without'} groups, {what}: {got}, expected {want}")
    return problems


def python_import_statements(tmp):
    """However an import statement is written, it links the same modules; text that only looks like one does not."""
    variants = {"single line": "from . import b, c\n",
                "parenthesized": "from . import (\n    b,\n    c,\n)\n",
                "aliases, comments, trailing comma": "from . import (\n    b as bb,  # first\n    c,  # second\n)\n",
                "backslash continuation": "from . import b, \\\n    c\n",
                "inside a function": "def f():\n    from . import (\n        b,\n        c,\n    )\n",
                "not Python 3 syntax": 'print "hi"\nfrom . import b, c\n'}
    problems, edges = [], {}
    for name, text in {**variants, "named in a docstring and a comment": '"""from . import b"""\n# from . import c\n'}.items():
        key = re.sub(r"\W", "_", name)
        tree(tmp, {f"{key}/pkg/__init__.py": "", f"{key}/pkg/b.py": "", f"{key}/pkg/c.py": "", f"{key}/pkg/a.py": text})
        graph = deps.scan(deps.collect([os.path.join(tmp, key, "pkg")], []))
        a = os.path.join(tmp, key, "pkg", "a.py")
        edges[name] = sorted(os.path.relpath(t, os.path.join(tmp, key)).replace(os.sep, "/") for t in graph[a])
        if name == "parenthesized":
            kept = deps.reach(graph, a, 1)
            if not {os.path.join(os.path.dirname(a), m) for m in ("b.py", "c.py")} <= set(kept):
                problems.append(f"--reach 1 dropped a module imported by the parenthesized form: {sorted(kept)}")
    for name in variants:
        if edges[name] != edges["single line"]:
            problems.append(f"{name}: {edges[name]}, the single-line form gives {edges['single line']}")
    if not {"pkg/b.py", "pkg/c.py"} <= set(edges["single line"]):
        problems.append(f"single line: {edges['single line']} lacks b.py or c.py")
    if edges["named in a docstring and a comment"]:
        problems.append(f"text that is not an import linked {edges['named in a docstring and a comment']}")
    return problems


def python_import_roots(tmp):
    """In-project absolute imports link the same files whether the scan paths are written relative or absolute."""
    tree(tmp, {"pkg/__init__.py": "", "pkg/b.py": "", "pkg/utils.py": "", "pkg/sub/__init__.py": "", "pkg/sub/c.py": "", "pkg/sub/d.py": "", "utils.py": "",
               "pkg/a.py": "from pkg import b\nimport pkg.sub.c\nfrom pkg.sub import d\nimport os, requests\n",
               "pkg/n.py": "import utils\n", "scripts/run.py": "import helper\n", "scripts/helper.py": ""})
    rel = lambda p: (os.path.relpath(p, tmp) if os.path.isabs(p) else p).replace(os.sep, "/")
    scan = lambda *paths: deps.scan(deps.collect(list(paths), []))
    named = lambda g: {rel(f): sorted(rel(t) for t in ts) for f, ts in g.items()}
    want = {"pkg/a.py": ["pkg/__init__.py", "pkg/b.py", "pkg/sub/__init__.py", "pkg/sub/c.py", "pkg/sub/d.py"],     # os and requests are not project files
            "pkg/n.py": ["pkg/utils.py"],                                                                         # the nearest utils.py, not the one at the top
            "scripts/run.py": ["scripts/helper.py"]}                                                              # a script imports its neighbour
    problems, here = [], os.getcwd()
    os.chdir(tmp)
    try:
        relative = {"whole project": named(scan(".")), "package": named(scan("pkg"))}
    finally:
        os.chdir(here)
    absolute = {"whole project": named(scan(tmp)), "package": named(scan(os.path.join(tmp, "pkg")))}
    for spelling, graphs in (("relative", relative), ("absolute", absolute)):
        for what, got in graphs.items():
            for f, targets in want.items():
                if f in got and got[f] != targets:
                    problems.append(f"{spelling} paths, {what}: {f} -> {got[f]}, expected {targets}")
    for what in relative:
        if relative[what] != absolute[what]:
            problems.append(f"{what}: relative and absolute paths give different graphs")
    g = scan(os.path.join(tmp, "pkg"))
    a = os.path.join(tmp, "pkg", "a.py")
    if os.path.join(tmp, "pkg", "b.py") not in deps.reach(g, a, 1):
        problems.append("--reach 1 from a module dropped a package-qualified import")
    return problems


REGRESSIONS = [
    ("build: more than ten names missing from the source", many_missing_names),
    ("names: whole identifiers and file names only", whole_name_matching),
    ("names: files with any extension are read", unlisted_extensions),
    ("layout: edges keep clear of container titles, and strikes are counted", container_titles),
    ("layout: edges and labels hugging a node or container title are counted", hugging_counted),
    ("layout: labels keep off container title bars and clear of nodes", labels_off_headers),
    ("layout: an edge along a container title gets a second route clear of it", title_hugs_rerouted),
    ("layout: decision exits in line across a diamond are counted", inline_exits),
    ("layout: edges attach to a decision at its vertices, arrivals apart from departures", decision_vertices),
    ("layout: edges sharing a decision's vertex fan out from it without counting as drawn on top of each other", vertex_fans),
    ("title and legend drawn for every diagram type", sequence_title_legend),
    ("build: aspect ratio and fill reported, a hint for a long strip", canvas_proportions),
    ("imports: arrows checked against the file they name", import_arrows),
    ("deps: distinct ids for files and directories named alike", distinct_ids),
    ("deps: Python import statements in any layout", python_import_statements),
    ("deps: Python imports resolve for relative and absolute scan paths", python_import_roots),
]


def main():
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in SAMPLES.items():
            model = mermaid.parse(text)
            if model.errors:
                print(f"FAIL {name}: spec errors {model.errors}")
                failed += 1
                continue
            diagram, _ = emit.build(model)
            path = diagram.save(os.path.join(tmp, name + ".drawio"))
            errors, warnings, nv, ne = validate(path)
            body = open(path, encoding="utf-8").read()
            problems = errors + warnings
            if name == "flow" and "List&amp;lt;Order&amp;gt;" not in body:
                problems.append("label with < was not double-escaped")
            if ne and 'source="' not in body:
                problems.append("edges are not glued")
            if name == "fan":                                              # edges leaving one node sideways share one trunk per side
                trunks = {y for y in re.findall(r'<mxPoint x="[-\d.]+" y="([-\d.]+)"', body)}
                if len(trunks) != 1:
                    problems.append(f"fan: {len(trunks)} different trunk heights, expected 1")
            report = getattr(model, "layout_report", None)                 # a router change must not bring the wobble back
            if report and (report["jogs"] > 1 or report["shared"] or report["node_hits"] or report["title_hits"] or report["near"] or report["label_near"]):          # a step the two nodes' extents leave no room to avoid may remain
                problems.append(f"layout: {report['jogs']} kinks, {report['shared']} shared runs, {report['node_hits']} edges through nodes, {report['title_hits']} through container titles, "
                                f"{report['near']} edges and {report['label_near']} labels hugging a node or title")
            print(f"{'FAIL' if problems else 'ok  '} {name}: {nv} shapes, {ne} edges" + (f"  {problems[:3]}" if problems else ""))
            failed += bool(problems)
    example = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "examples", "import-graph", "import-graph.mmd")
    if os.path.exists(example):                                     # the shipped example must stay free of crossings and kinks
        model = mermaid.parse(c2d.read_spec(example))
        emit.build(model)
        report = model.layout_report
        bad = [f"{k}={report[k]}" for k in ("crossings", "jogs", "shared", "node_hits", "near", "label_near") if report[k]]
        print(f"{'FAIL' if bad else 'ok  '} examples/import-graph: {report['bends']} bends" + (f"  {bad}" if bad else ""))
        failed += bool(bad)
    with tempfile.TemporaryDirectory() as tmp:                      # a spec saved by Windows PowerShell starts with a BOM
        for label, prefix in (("with BOM", b"\xef\xbb\xbf"), ("without BOM", b"")):
            path = os.path.join(tmp, "spec.mmd")
            with open(path, "wb") as f:
                f.write(prefix + SAMPLES["state"].encode("utf-8"))
            errors = mermaid.parse(c2d.read_spec(path)).errors
            print(f"{'FAIL' if errors else 'ok  '} spec file {label}" + (f"  {errors[:2]}" if errors else ""))
            failed += bool(errors)
    model = mermaid.parse("flowchart LR\n  map --> filter\n  filter --> join\n")        # ids draw.io cannot export unless renamed
    diagram, _ = emit.build(model)
    with tempfile.TemporaryDirectory() as tmp:
        path = diagram.save(os.path.join(tmp, "ids.drawio"))
        errors, warnings, nv, ne = validate(path)
        ids = set(re.findall(r'<mxCell id="([^"]+)"', open(path, encoding="utf-8").read()))
    bad = sorted(ids & drawio_lib.JS_MEMBERS)
    print(f"{'FAIL' if bad or errors else 'ok  '} ids that clash with JavaScript members are renamed" + (f"  {bad or errors[:2]}" if bad or errors else ""))
    failed += bool(bad or errors)
    doc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md"), encoding="utf-8").read()
    for block in re.findall(r"```\n(.*?)```", doc, re.S):          # the examples in SKILL.md must keep parsing
        if block.split()[0] in ("flowchart", "graph", "stateDiagram-v2", "sequenceDiagram", "classDiagram", "erDiagram"):
            model = mermaid.parse(block)
            print(f"{'FAIL' if model.errors else 'ok  '} SKILL.md example {block.split()[0]}" + (f"  {model.errors[:2]}" if model.errors else ""))
            failed += bool(model.errors)
    for name, test in REGRESSIONS:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                problems = test(tmp)
            except Exception as e:                                  # a crash is a failure of that check, not of the run
                problems = [f"{type(e).__name__}: {e}"]
        print(f"{'FAIL' if problems else 'ok  '} {name}" + (f"  {problems[:3]}" if problems else ""))
        failed += bool(problems)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
