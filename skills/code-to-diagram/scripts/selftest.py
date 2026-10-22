#!/usr/bin/env python3
"""Regression check for the skill's own scripts: python selftest.py  (no draw.io needed, writes to a temp dir).

Builds one small sample of every diagram type, validates the XML, and checks a few properties that earlier versions
got wrong: edges glued to shapes, labels with "<" survive, no edge or label ends up pointing at a missing shape, and the
lines come out clean (no kinks, no edges on top of each other, none through a node).
"""
import contextlib
import io
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import c2d  # noqa: E402
import deps  # noqa: E402
import drawio_lib  # noqa: E402
import emit  # noqa: E402
import facts  # noqa: E402
import mermaid  # noqa: E402
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


REGRESSIONS = [
    ("build: more than ten names missing from the source", many_missing_names),
    ("names: whole identifiers and file names only", whole_name_matching),
    ("imports: arrows checked against the file they name", import_arrows),
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
            if report and (report["jogs"] > 1 or report["shared"] or report["node_hits"]):          # a step the two nodes' extents leave no room to avoid may remain
                problems.append(f"layout: {report['jogs']} kinks, {report['shared']} shared runs, {report['node_hits']} edges through nodes")
            print(f"{'FAIL' if problems else 'ok  '} {name}: {nv} shapes, {ne} edges" + (f"  {problems[:3]}" if problems else ""))
            failed += bool(problems)
    example = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "examples", "import-graph", "import-graph.mmd")
    if os.path.exists(example):                                     # the shipped example must stay free of crossings and kinks
        model = mermaid.parse(c2d.read_spec(example))
        emit.build(model)
        report = model.layout_report
        bad = [f"{k}={report[k]}" for k in ("crossings", "jogs", "shared", "node_hits") if report[k]]
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
