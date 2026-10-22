"""Mechanical faithfulness check: do the names in the diagram exist in the source?

The usual diagram lie is an invented or misspelled name. That is decidable by script: collect the identifiers the
diagram shows and look each one up in the files named by `%% src:`. It cannot judge arrow direction or completeness;
for imports the `deps` command reads them from the code instead of from the model.
"""
import os
import posixpath
import re

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv", "coverage", ".next"}
TEXT_EXT = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".py", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".cs", ".c", ".h",
            ".cpp", ".hpp", ".swift", ".sql", ".json", ".yml", ".yaml", ".toml", ".md", ".html", ".css", ".sh", ".xml", ".proto"}
CODE_LIKE = re.compile(r"^(?:[a-z]+[A-Z]\w*|[A-Z][a-z0-9]+[A-Z]\w*|\w*_\w*|[A-Z]{4,}\w*|\w+\.(?:js|mjs|ts|tsx|py|json|md|html|css|sql|java|go|rs))$")
IDENT = re.compile(r"[A-Za-z_][\w]*")
WORD = re.compile(r"[A-Za-z_][\w.]*[\w]|[A-Za-z_]")
KEYWORDS = {"PK", "FK", "UK", "static", "void", "null", "true", "false", "NULL", "TRUE", "FALSE"}
MAX_FILE, MAX_TOTAL = 2_000_000, 40_000_000


def corpus(paths):
    """One string with the text of every source file plus their relative paths; (string, number of files)."""
    chunks, total, n = [], 0, 0
    for p in paths:
        walk = [(os.path.dirname(p), [], [os.path.basename(p)])] if os.path.isfile(p) else os.walk(p)
        for root, dirs, names in walk:
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in names:
                f = os.path.join(root, name)
                chunks.append(f.replace("\\", "/"))
                if os.path.splitext(name)[1].lower() in TEXT_EXT and total < MAX_TOTAL:
                    try:
                        if os.path.getsize(f) <= MAX_FILE:
                            chunks.append(open(f, encoding="utf-8", errors="replace").read())
                            total += os.path.getsize(f)
                            n += 1
                    except OSError:
                        pass
    return "\n".join(chunks), n


def claims(model):
    """[(token, where)] the diagram asserts exist in the code."""
    out = []
    strict = model.kind in ("class", "er")

    def add(text, where, every=False):
        for word in WORD.findall(text or ""):
            for part in word.split("."):
                if part in KEYWORDS or len(part) < 2:
                    continue
                if (every and len(part) >= 3 and IDENT.fullmatch(part)) or CODE_LIKE.match(part):
                    out.append((part, where))
            if CODE_LIKE.match(word) and "." in word:               # main.js style names are checked whole
                out.append((word, where))

    for nid, n in model.nodes.items():
        if n.shape in ("note", "start", "end", "fork"):
            continue
        add(n.label, nid, strict)
        for row in n.members + n.methods:
            add(re.sub(r"\[.*?\]|—.*$", "", row), nid, strict)
    for pid, label, _ in model.participants:
        add(label, pid, True)
    for it in model.items:
        if it[0] == "msg":
            add(it[3], f"{it[1]}->{it[2]}")
    for e in model.edges:
        add(e.label, f"{e.src}->{e.tgt}")
    return out


FILE_NAME = re.compile(r"(?:[\w.-]+[/\\])*[\w.-]+\.(?:js|mjs|cjs|jsx|ts|tsx|py)(?![\w])")


def check_edges(model, srcs):
    """Solid arrows between nodes that name source files claim 'imports'. -> (checked, [(from, to)] with no import behind
    them in either direction, arrows not judged because a name fits several scanned files), or None when fewer than two
    arrows can be judged and none was skipped. Dashed arrows are not checked.
    A name is matched by the end of its path: `src/one/a.js` is that file only; a bare `a.js` is read inside the nearest
    enclosing subgraph titled like a directory (what `deps` draws), and is not judged when it still fits several files."""
    import deps
    files = deps.collect([p for p in srcs if os.path.exists(p)], [])
    if not files:
        return None
    graph = deps.scan(files)
    posix = lambda f: f.replace(os.sep, "/")
    by_name = {}
    for f in files:
        by_name.setdefault(os.path.basename(f), set()).add(f)
    children = {}
    for n in model.nodes.values():
        if n.parent:
            children.setdefault(n.parent, []).append(n.id)
    for g in model.groups.values():
        if g.parent:
            children.setdefault(g.parent, []).append(g.id)

    def directory(node):                                   # "src/one/" for a node inside a subgraph of that title
        g = model.groups.get(node.parent)
        while g and not g.label.endswith("/"):
            g = model.groups.get(g.parent)
        return g.label if g else ""

    def files_named(name, prefix):
        name = name.replace("\\", "/")
        for want in ([name] if "/" in name or not prefix else [prefix + name, name]):
            want = posixpath.normpath(want)
            hits = {f for f in by_name.get(posixpath.basename(want), ()) if posix(f) == want or posix(f).endswith("/" + want)}
            if hits:
                return hits
        return set()

    def named(i):                                          # (source files, a name fits several) for a node, or a group by its members
        node = model.nodes.get(i)
        out, vague = set(), False
        if node:
            for name in FILE_NAME.findall(node.label):
                hits = files_named(name, directory(node))
                if len(hits) == 1:
                    out |= hits
                vague |= len(hits) > 1
        for c in children.get(i, []):
            o, v = named(c)
            out, vague = out | o, vague or v
        return out, vague

    checked, bad, unclear = 0, [], 0
    for e in model.edges:
        if e.line != "solid" or e.kind != "flow":
            continue
        (a, va), (b, vb) = named(e.src), named(e.tgt)
        if not (a or va) or not (b or vb):                 # not an arrow between source files
            continue
        if va or vb:
            unclear += 1
        elif a != b:
            checked += 1
            if not any(y in graph.get(x, ()) or x in graph.get(y, ()) for x in a for y in b):
                bad.append((e.src, e.tgt))
    return (checked, bad, unclear) if checked >= 2 or unclear else None


def has_word(text, tok):
    """Is tok in text as a whole name? `load_user` is not in `load_users`, `main.js` is not in `domain.js`."""
    i = text.find(tok)
    while i >= 0:
        j = i + len(tok)
        if not re.search(r"\w", text[i - 1:i] + text[j:j + 1]):          # neither neighbour continues the name
            return True
        i = text.find(tok, i + 1)
    return False


def check(model, srcs):
    """-> (checked, missing [(token, where)], files read, source paths that do not exist), or None when no source path exists."""
    paths = [p for p in srcs if os.path.exists(p)]
    if not paths:
        return None
    text, n = corpus(paths)
    seen, missing = set(), []
    for tok, where in claims(model):
        if tok in seen:
            continue
        seen.add(tok)
        if not has_word(text, tok):
            missing.append((tok, where))
    return len(seen), missing, n, [p for p in srcs if p not in paths]
