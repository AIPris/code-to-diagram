#!/usr/bin/env python3
"""Regression check for the skill's own scripts: python selftest.py  (no draw.io needed, writes to a temp dir).

Builds one small sample of every diagram type, validates the XML, and checks a few properties that earlier versions
got wrong: edges glued to shapes, labels with "<" survive, no edge or label ends up pointing at a missing shape.
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import c2d  # noqa: E402
import emit  # noqa: E402
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
            print(f"{'FAIL' if problems else 'ok  '} {name}: {nv} shapes, {ne} edges" + (f"  {problems[:3]}" if problems else ""))
            failed += bool(problems)
    with tempfile.TemporaryDirectory() as tmp:                      # a spec saved by Windows PowerShell starts with a BOM
        for label, prefix in (("with BOM", b"\xef\xbb\xbf"), ("without BOM", b"")):
            path = os.path.join(tmp, "spec.mmd")
            with open(path, "wb") as f:
                f.write(prefix + SAMPLES["state"].encode("utf-8"))
            errors = mermaid.parse(c2d.read_spec(path)).errors
            print(f"{'FAIL' if errors else 'ok  '} spec file {label}" + (f"  {errors[:2]}" if errors else ""))
            failed += bool(errors)
    doc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md"), encoding="utf-8").read()
    for block in re.findall(r"```\n(.*?)```", doc, re.S):          # the examples in SKILL.md must keep parsing
        if block.split()[0] in ("flowchart", "graph", "stateDiagram-v2", "sequenceDiagram", "classDiagram", "erDiagram"):
            model = mermaid.parse(block)
            print(f"{'FAIL' if model.errors else 'ok  '} SKILL.md example {block.split()[0]}" + (f"  {model.errors[:2]}" if model.errors else ""))
            failed += bool(model.errors)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
