#!/usr/bin/env python3
"""Import scanner: turn the real imports of a codebase into a Mermaid flowchart, so nobody transcribes them by hand.

    python deps.py <dir-or-file>... [--entry FILE] [--exclude SUBSTR]... [--no-groups] [--no-drop-entry-edges]
                   [--reach N] [--dir TB|LR] > diagrams/<slug>/<slug>.mmd

Reads JS/TS (import / export ... from / require / import()) and Python (import / from ... import) files, resolves
relative and in-project imports to files, ignores packages. Output is Mermaid with one subgraph per directory,
classDef hub (imported by 4+ files) and leaf (imports nothing), and a `%% src:` line that `c2d.py build` uses to
check names against the source.
"""
import argparse
import os
import re
import sys

JS_EXT = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv", "coverage"}
JS_IMPORT = re.compile(r"""(?:\bimport\s+(?:[^'";]*?\s+from\s+)?|\bexport\s+[^'";]*?\s+from\s+|\brequire\s*\(\s*|\bimport\s*\(\s*)['"]([^'"]+)['"]""")
PY_FROM = re.compile(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+(.+)$", re.M)
PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+(?:\s*,\s*[\w.]+)*)", re.M)


def collect(paths, exclude):
    files = []
    for p in paths:
        if os.path.isfile(p):
            files.append(os.path.normpath(p))
            continue
        for root, dirs, names in os.walk(p):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for n in sorted(names):
                f = os.path.normpath(os.path.join(root, n))
                if n.endswith(JS_EXT + (".py",)) and not n.endswith(".d.ts") and not any(x in f.replace("\\", "/") for x in exclude):
                    files.append(f)
    return files


def resolve_js(frm, spec, known):
    if not spec.startswith("."):
        return None
    base = os.path.normpath(os.path.join(os.path.dirname(frm), spec))
    for cand in [base] + [base + e for e in JS_EXT] + [os.path.join(base, "index" + e) for e in JS_EXT]:
        if cand in known:
            return cand
    return None


def resolve_py(frm, dots, mod, names, known):
    base = os.path.dirname(frm)
    if dots:
        for _ in range(len(dots) - 1):
            base = os.path.dirname(base)
        roots = [base]
    else:
        roots = sorted({os.path.dirname(k) for k in known} | {"."}, key=len)[:1] + [base]
    out = []
    for root in roots:
        parts = mod.split(".") if mod else []
        stem = os.path.normpath(os.path.join(root, *parts)) if parts else os.path.normpath(root)
        for cand in (stem + ".py", os.path.join(stem, "__init__.py")):
            if cand in known:
                out.append(cand)
                break
        for n in names:                                       # from pkg import submodule
            cand = os.path.normpath(os.path.join(stem, n + ".py"))
            if cand in known:
                out.append(cand)
    return out


def scan(files):
    known = set(files)
    edges = {f: [] for f in files}
    for f in files:
        try:
            text = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        found = []
        if f.endswith(".py"):
            for dots, mod, rest in PY_FROM.findall(text):
                names = [n.strip().split(" as ")[0] for n in rest.replace("(", "").replace(")", "").split(",") if n.strip()]
                found += resolve_py(f, dots, mod, names, known)
            for group in PY_IMPORT.findall(text):
                for mod in re.split(r"\s*,\s*", group):
                    found += resolve_py(f, "", mod, [], known)
        else:
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            text = re.sub(r"(?m)^\s*//.*$", "", text)
            for spec in JS_IMPORT.findall(text):
                t = resolve_js(f, spec, known)
                if t:
                    found.append(t)
        edges[f] = [t for t in dict.fromkeys(found) if t != f]
    return edges


def find_entry(name, files):
    """The scanned file --entry names: the exact path, else the one file whose path ends with it ("c2d.py", "scripts/c2d.py")."""
    want = os.path.normpath(name)
    if want in files:
        return want
    hits = [f for f in files if f == want or f.endswith(os.sep + want)]
    if len(hits) != 1:
        sys.exit(f"--entry {name}: " + (f"matches {len(hits)} scanned files ({', '.join(hits[:4])}); give more of the path" if hits else "matches no scanned file"))
    return hits[0]


def reach(edges, entry, hops):
    """Entry plus everything within `hops` imports of it, with the imports among those files."""
    seen, frontier = {entry}, [entry]
    for _ in range(hops):
        frontier = [t for f in frontier for t in edges.get(f, []) if t not in seen]
        seen |= set(frontier)
    return {f: [t for t in ts if t in seen] for f, ts in edges.items() if f in seen}


def mermaid(edges, entry=None, groups=True, drop_entry_edges=None, direction="TB", srcs=(), full=None):
    fan = ""
    if entry in edges:
        others = len(edges) - 1
        if drop_entry_edges is None:                       # automatic: an entry that links to most nodes only adds noise
            drop_entry_edges = others >= 8 and len(edges[entry]) >= 0.6 * others
        if drop_entry_edges:
            fan = f"imports {'all ' if len(edges[entry]) == others else ''}{len(edges[entry])}"
            edges = dict(edges)
            edges[entry] = []
    files = list(edges)
    full = full if full is not None else edges            # a file is a leaf only if it imports nothing in the whole scan
    indeg = {f: 0 for f in files}
    for f, ts in edges.items():
        for t in ts:
            indeg[t] = indeg.get(t, 0) + 1
    ident = {f: re.sub(r"\W", "_", f) for f in files}
    label = lambda f: os.path.basename(f)
    quote = lambda p: f'"{p}"' if " " in p else p
    lines = [f"%% src: {' '.join(quote(p.replace(os.sep, '/')) for p in srcs)}", f"flowchart {direction}"]

    def emit_node(f, pad="    "):
        cls = "entry" if f == entry else "hub" if indeg[f] >= 4 else "leaf" if not full.get(f) else "mod"
        lines.append(f'{pad}{ident[f]}["{label(f)}"]:::{cls}')

    if fan:                                                    # entry -> one box that holds everything it imports
        lines.append(f'  subgraph all_ ["{len(files) - 1} modules imported by {label(entry)}"]')
        by_dir = {}
        for f in files:
            if f != entry:
                by_dir.setdefault(os.path.dirname(f) if groups else "", []).append(f)
        for d, fs in by_dir.items():
            nested = d and len(by_dir) > 1
            if nested:
                lines.append(f'    subgraph {re.sub(chr(92) + "W", "_", d)}_g ["{d.replace(os.sep, "/")}/"]')
            alone = [f for f in fs if not edges[f] and not indeg[f]]
            for f in fs:
                if f not in alone:
                    emit_node(f, "      " if nested else "    ")
            if alone:
                lines.append(f'    {"  " if nested else ""}subgraph alone_{len(lines)} ["no imports among the modules shown ({len(alone)})"]')
                for f in alone:
                    emit_node(f, "      " if not nested else "        ")
                lines.append(f'    {"  " if nested else ""}end')
            if nested:
                lines.append("    end")
        lines.append("  end")
        emit_node(entry, "  ")
        lines.append(f'  {ident[entry]} -->|"{fan}"| all_')
    else:
        by_dir = {}
        for f in files:
            by_dir.setdefault(os.path.dirname(f) if groups else "", []).append(f)
        for d, fs in by_dir.items():
            if d and len(by_dir) > 1:
                lines.append(f'  subgraph {re.sub(chr(92) + "W", "_", d)}_g ["{d.replace(os.sep, "/")}/"]')
            for f in fs:
                emit_node(f)
            if d and len(by_dir) > 1:
                lines.append("  end")
    for f, ts in edges.items():
        for t in ts:
            lines.append(f"  {ident[f]} --> {ident[t]}")
    lines += ["  classDef entry fill:#e1d5e7,stroke:#9673a6", "  classDef hub fill:#fff2cc,stroke:#d6b656",
              "  classDef leaf fill:#d5e8d4,stroke:#82b366", "  classDef mod fill:#dae8fc,stroke:#6c8ebf",
              "%% legend: entry=entry point; hub=imported by 4+ of the files shown; leaf=imports nothing else in the scanned code; mod=other module"]
    return "\n".join(lines) + "\n"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--entry", help="entry file: highlighted, and the root for --reach")
    ap.add_argument("--exclude", action="append", default=[], help="skip files whose path contains this text (repeatable)")
    ap.add_argument("--no-groups", action="store_true", help="no subgraph per directory")
    ap.add_argument("--drop-entry-edges", action=argparse.BooleanOptionalAction, default=None,
                    help="omit the entry's own imports (default: automatic when it imports 60%%+ of the files shown)")
    ap.add_argument("--reach", type=int, metavar="N", help="keep only --entry and files within N import hops of it (1 = what it imports directly)")
    ap.add_argument("--dir", default="TB", choices=["LR", "TB", "RL", "BT"])
    a = ap.parse_args(argv)
    files = collect(a.paths, a.exclude)
    if not files:
        sys.exit("no .js/.ts/.py files found")
    sys.stdout.reconfigure(encoding="utf-8")
    full = scan(files)
    entry = find_entry(a.entry, full) if a.entry else None
    edges = reach(full, entry, a.reach) if a.reach and entry else full
    sys.stdout.write(mermaid(edges, entry, not a.no_groups, a.drop_entry_edges, a.dir, a.paths, full))


if __name__ == "__main__":
    main(sys.argv[1:])
