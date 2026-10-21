#!/usr/bin/env python3
"""Fuzz the layout engine: python fuzz.py [runs] [seed]

Builds many random diagrams (nested groups, back edges, long edges, self loops, labels, all directions) and requires the
validator to find no errors: no overlapping shapes, no waypointed edge through an unrelated shape, no dangling edge.
A failure prints the Mermaid source that reproduces it.
"""
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emit  # noqa: E402
import mermaid  # noqa: E402
from validate_drawio import validate  # noqa: E402


def random_spec(rng):
    n = rng.randint(3, 26)
    direction = rng.choice(["TD", "LR", "BT", "RL"])
    lines = [f"flowchart {direction}"]
    groups = [f"g{i}" for i in range(rng.randint(0, 4))]
    members = {g: [] for g in groups}
    ids = [f"n{i}" for i in range(n)]
    shapes = ["[{}]", "({})", "([{}])", "{{{{{}}}}}".replace("{{{{", "{").replace("}}}}", "}"), "[({})]", "(({}))", "[/{}/]"]
    for i in ids:
        g = rng.choice(groups + [None] * 3) if groups else None
        if g:
            members[g].append(i)
    declared = set()

    def node(i):
        label = rng.choice(["short", "a longer label here", "x", "validate input (strict)", "two words", "A very long label that has to wrap over several lines"])
        shape = rng.choice(shapes).format(f'"{label}"') if i not in declared else ""
        declared.add(i)
        return i + shape

    if groups:
        for k, g in enumerate(groups):
            if members[g]:
                lines.append(f'  subgraph {g} ["Group {k}"]')
                lines += [f"    {node(i)}" for i in members[g]]
                lines.append("  end")
    for _ in range(rng.randint(n - 1, n * 2)):
        a, b = rng.choice(ids), rng.choice(ids)
        if a == b and rng.random() > 0.15:
            continue
        label = rng.choice(["", "", "yes", "no", "load error", "restart(): retry, checkpoint"])
        arrow = rng.choice(["-->", "-->", "-.->", "==>", "---"])
        lines.append(f"  {node(a)} {arrow}{f'|{label}|' if label else ''} {node(b)}")
    if groups and rng.random() < 0.4:
        a, g = rng.choice(ids), rng.choice(groups)
        lines.append(f"  {a} --> {g}")
    return "\n".join(lines)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    runs = int(args[0]) if args else 200
    seed = int(args[1]) if len(args) > 1 else 1
    if "--legacy" in sys.argv:                       # the router before it checked its tracks against the nodes
        import layout
        layout._clear = lambda points, boxes: True
    bad = hits = 0
    with tempfile.TemporaryDirectory() as tmp:
        for k in range(runs):
            rng = random.Random(seed * 100000 + k)
            spec = random_spec(rng)
            model = mermaid.parse(spec)
            if model.errors:
                continue
            try:
                diagram, _ = emit.build(model)
                hits += getattr(model, "layout_report", {}).get("node_hits", 0)
                errors, warnings, nv, ne = validate(diagram.save(os.path.join(tmp, "f.drawio")))
            except Exception as e:  # noqa: BLE001
                errors, warnings = [f"crash: {e!r}"], []
            if errors:
                bad += 1
                if bad <= 3:
                    print(f"--- run {k} (seed {seed}): {errors[:3]}\n{spec}\n")
    print(f"{runs - bad}/{runs} random diagrams valid; {hits} edge-through-node incidents in total (the validator sees only pinned routes, this counts all)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
