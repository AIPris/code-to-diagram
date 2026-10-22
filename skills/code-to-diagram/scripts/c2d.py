#!/usr/bin/env python3
"""code-to-diagram: Mermaid-style spec -> laid-out, editable .drawio + verified PNG, in one command.

    python c2d.py build diagrams/<slug>/<slug>.mmd [--no-png] [--archive] [--scale N]
    python c2d.py deps  <dir-or-file>... [--entry F] [--reach N] [--exclude S] ...   (writes Mermaid to stdout)
    python c2d.py check <file.drawio>        validate a hand-edited .drawio
    python c2d.py digest <file.drawio>       list what an existing diagram shows (to rebuild its spec)
    python c2d.py png   <file.drawio> [out.png]

`build` writes <slug>.drawio and <slug>.png next to the .mmd, then prints a short report: spec errors (with line
numbers) stop the build; otherwise layout size, structural warnings, names not found in the `%% src:` files, and the
verified PNG path. Re-running it after editing the .mmd is the update loop. The previous version of a diagram is
copied to history/ before it is replaced when it differs from the new one and either was changed since build wrote it
(a hash stamp inside the file no longer matches: the user edited it in draw.io), is more than 30 minutes old (an
earlier session), or --archive was given.
"""
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import emit  # noqa: E402
import facts  # noqa: E402
import mermaid  # noqa: E402
import metrics  # noqa: E402
import render  # noqa: E402
from drawio_lib import untouched  # noqa: E402
from validate_drawio import validate  # noqa: E402

STALE_SECONDS = 30 * 60
SHOW = 6


def archive(base, new_xml, force):
    """Keep the previous version of a diagram so it can be compared later and hand edits are never lost."""
    old = base + ".drawio"
    if not os.path.exists(old):
        return ""
    current = open(old, encoding="utf-8").read()
    if current == new_xml:
        return ""
    # kept when someone changed the file since build wrote it (a stamp inside it no longer matches), when it is from an
    # earlier session, or when asked; a file straight from the previous build is simply replaced
    if not (force or not untouched(current) or time.time() - os.path.getmtime(old) >= STALE_SECONDS):
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(old)))
    hist = os.path.join(os.path.dirname(base), "history")
    os.makedirs(hist, exist_ok=True)
    name = os.path.basename(base)
    target, n = os.path.join(hist, f"{name}-{stamp}"), 1
    while os.path.exists(target + ".drawio"):         # never overwrite an earlier archive
        n += 1
        target = os.path.join(hist, f"{name}-{stamp}-{n}")
    for ext in (".drawio", ".png"):                   # the .mmd is already the new spec by now
        if os.path.exists(base + ext):
            shutil.copy2(base + ext, target + ext)
    return target


def resolve_src(path, anchor):
    """A `%% src:` path as given, else relative to the .mmd's folder or one of its parents (a diagram usually lives in
    <repo>/diagrams/<slug>/, so a later session started elsewhere still finds the repo's files)."""
    if os.path.exists(path):
        return path
    d = os.path.abspath(anchor)
    for _ in range(6):
        if os.path.exists(os.path.join(d, path)):
            return os.path.join(d, path)
        d = os.path.dirname(d)
    return path


def read_spec(path):
    """The spec text. Windows PowerShell's `>` and Out-File start a file with a UTF-8 BOM, which must not reach line 1."""
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def cap(lines, n=SHOW):
    return lines[:n] + ([f"  ... +{len(lines) - n} more"] if len(lines) > n else [])


def cmd_build(argv):
    args = [a for a in argv if not a.startswith("--")]
    scale = float(argv[argv.index("--scale") + 1]) if "--scale" in argv else 0
    if scale:
        args = [a for a in args if a != argv[argv.index("--scale") + 1]]
    if len(args) != 1 or not args[0].endswith(".mmd"):
        sys.exit("usage: c2d.py build <path/to/name.mmd> [--no-png] [--archive] [--scale N]")
    mmd = args[0]
    base = mmd[:-4]
    model = mermaid.parse(read_spec(mmd))
    if model.errors:
        print(f"SPEC ERRORS in {mmd} (nothing built):")
        for no, text, why in model.errors[:20]:
            print(f"  line {no}: {why}" + (f"\n      {text[:90]}" if text else ""))
        sys.exit(1)
    try:
        diagram, (w, h) = emit.build(model)
    except (ValueError, KeyError) as e:
        sys.exit(f"BUILD ERROR: {e}")
    kept = archive(base, diagram.xml(), "--archive" in argv)
    diagram.save(base + ".drawio")
    errors, warnings, nv, ne = validate(base + ".drawio")
    print(f"built {base}.drawio  [{model.kind}: {len(model.nodes) or len(model.participants)} "
          f"{'participants' if model.kind == 'sequence' else 'nodes'}, "
          f"{ne if model.kind != 'sequence' else sum(1 for i in model.items if i[0] == 'msg')} "
          f"{'edges' if model.kind != 'sequence' else 'messages'}, {len(model.groups)} groups; {int(w)}x{int(h)} px]")
    if kept:
        print(f"previous version kept as {kept}.*")
    print(f"structure: {'FAIL' if errors else 'ok'} ({len(errors)} errors, {len(warnings)} warnings)")
    lr = getattr(model, "layout_report", None)
    if lr:
        bad = [f"{lr['node_hits']} edge(s) run through a node" if lr["node_hits"] else "",
               f"{lr['crossings']} edge crossings" if lr["crossings"] > 3 else "",
               f"{lr['jogs']} kink(s) (a sideways step under {metrics.JOG} px)" if lr["jogs"] else "",
               f"{lr['detour']} edges go out past their target and back" if lr["detour"] > 2 else "",
               f"{lr['shared']} run(s) of two edges on top of each other" if lr["shared"] else "",
               f"{lr['label_conflicts']} label(s) touch a line or node" if lr["label_conflicts"] else ""]
        print(f"layout: {lr['direction']}{' (chosen automatically)' if lr['auto'] else ''}; " + (", ".join(b for b in bad if b) or "no overlaps")
              + f"; {lr['bends']} bends in {ne} edges")
        if lr["node_hits"] or lr["crossings"] > 12 or lr["label_conflicts"] > 2 or lr["jogs"] > 3 or lr["detour"] > 2 or lr["bends"] > 3 * max(ne, 1):
            print("  dense: shorten labels, drop edges to nodes everything uses, group nodes in subgraphs, or split the diagram")
    for line in cap([f"  {m}" for m in errors + warnings]):
        print(line)
    srcs = [resolve_src(os.path.normpath(p), os.path.dirname(os.path.abspath(mmd))) for p in model.srcs]
    res = facts.check(model, srcs)
    if res is None:
        print("names: NOT CHECKED - " + (f"none of the `%% src:` paths exist from {os.getcwd()}: {' '.join(srcs[:4])} (run build from the source repo's root, or use absolute paths)"
                                         if srcs else "add a `%% src: path ...` line to check labels against the source"))
    else:
        checked, missing, n_files, absent = res
        if absent:
            print(f"names: note - `%% src:` paths not found, skipped: {' '.join(absent[:4])}")
        if missing:
            print(f"names: {len(missing)} of {checked} code names NOT FOUND in {n_files} source files (misspelled or invented?)")
            for line in cap([f"  {tok}   (in {where})" for tok, where in missing], 10):
                print(line)
        else:
            print(f"names: all {checked} code names found in {n_files} source files")
    if model.kind == "flow":
        er = facts.check_edges(model, srcs)
        if er:
            checked, bad = er
            if bad:
                print(f"imports: {len(bad)} of {checked} solid arrows between source files have NO import behind them (either direction): "
                      + ", ".join(f"{a} -> {b}" for a, b in bad[:5]) + "; fix them, or draw other relations dashed")
            else:
                print(f"imports: all {checked} solid arrows between source files match a real import")
    if max(w, h) > 3500:
        print(f"size: {int(w)}x{int(h)} px is too large to read in one image; drop leaf nodes, wrap groups or split the diagram")
    if "--no-png" not in argv:
        dims, why = render.render(base + ".drawio", base + ".png", scale or render.choose_scale(w, h))
        print(f"png: {base}.png ({dims[0]}x{dims[1]}, verified complete)" if dims else f"png: NOT CREATED - {why}")
    sys.exit(1 if errors else 0)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("build", "deps", "check", "png", "digest"):
        sys.exit(__doc__)
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "build":
        cmd_build(rest)
    elif cmd == "deps":
        import deps
        deps.main(rest)
    elif cmd == "check":
        from validate_drawio import main as check_main
        sys.exit(check_main(rest[0]))
    elif cmd == "digest":
        import digest_drawio
        digest_drawio.main(rest[0])
    else:
        out = rest[1] if len(rest) > 1 else rest[0][:-len(".drawio")] + ".png" if rest[0].endswith(".drawio") else rest[0] + ".png"
        dims, why = render.render(rest[0], out, 2)
        print(f"{out} ({dims[0]}x{dims[1]}, verified complete)" if dims else f"NOT CREATED - {why}")
        sys.exit(0 if dims else 1)


if __name__ == "__main__":
    main()
