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
from drawio_lib import PALETTE, untouched  # noqa: E402
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


def legend_note(model):
    """The `legend:` line for node fills that the `%% legend:` does not explain; '' when it explains all of them or there is none."""
    gaps = emit.legend_gaps(model)
    if not gaps:
        return ""
    names = {fill.lower(): name for name, (fill, _) in PALETTE.items()}
    shown = lambda ids: ", ".join((model.nodes[i].label.splitlines() or [i])[0][:24] for i in ids[:3]) + (", ..." if len(ids) > 3 else "")
    return ("node fills with no legend entry: " + ", ".join(f"{names.get(fill, fill)} ({shown(ids)})" for fill, ids in gaps.items())
            + "; add a `colour=meaning` entry for each to `%% legend:`, or colour those nodes like an entry")


def strip_hint(model, w, h):
    """What to change when the canvas is a strip (metrics.STRIP) long enough to be scaled down to fit a screen; '' otherwise.
    A short strip (a chain of three nodes) stays legible as it is."""
    if metrics.ratio(w, h) <= metrics.STRIP or max(w, h) <= render.SHARP:
        return ""
    wide = w >= h
    if model.kind == "sequence":
        fix = "drop participants or messages, or split the diagram by phase"
    else:
        along = getattr(model, "layout_report", {}).get("direction") in ("LR", "RL")      # the flow runs along the long side
        fix = (f"switch to {'TD' if wide else 'LR'}, " if along == wide else "") + "group nodes in subgraphs, drop leaf nodes, or split the diagram"
    return f"{metrics.aspect_text(w, h)} is a {'wide' if wide else 'tall'} strip, its text shrinks when the image is fitted to a screen; {fix}"


def layout_findings(lr):
    """The defects a layout report (metrics.evaluate) counts, as the phrases the build line lists; [] when there is nothing to fix."""
    found = [f"{lr['node_hits']} edge(s) run through a node" if lr["node_hits"] else "",
             f"{lr['title_hits']} edge(s) strike through a container title" if lr["title_hits"] else "",
             f"{lr['near']} edge(s) pass within {metrics.CLEARANCE} px of a node or container title" if lr["near"] else "",
             f"{lr['inline']} decision(s) with two edges leaving in line on opposite sides" if lr["inline"] else "",
             f"{lr['crossings']} edge crossings" if lr["crossings"] > 3 else "",
             f"{lr['jogs']} kink(s) (a sideways step under {metrics.JOG} px)" if lr["jogs"] else "",
             f"{lr['detour']} edges go out past their target and back" if lr["detour"] > 2 else "",
             f"{lr['shared']} run(s) of two edges on top of each other" if lr["shared"] else "",
             f"{lr['label_conflicts']} label(s) touch a line, node or container header" if lr["label_conflicts"] else "",
             f"{lr['label_near']} label(s) hug a node or container header" if lr["label_near"] else ""]
    return [f for f in found if f]


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
    ne -= sum(1 for key, _ in model.legend if key in mermaid.LEGEND_EDGES)         # a legend's sample lines are not edges of the diagram
    lr =getattr(model, "layout_report", None)
    print(f"built {base}.drawio  [{model.kind}: {len(model.nodes) or len(model.participants)} "
          f"{'participants' if model.kind == 'sequence' else 'nodes'}, "
          f"{ne if model.kind != 'sequence' else sum(1 for i in model.items if i[0] == 'msg')} "
          f"{'edges' if model.kind != 'sequence' else 'messages'}, {len(model.groups)} groups; {int(w)}x{int(h)} px, "
          f"{metrics.aspect_text(w, h)}" + (f", nodes cover {lr['fill']:.0%}" if lr else "") + "]")
    if kept:
        print(f"previous version kept as {kept}.*")
    print(f"structure: {'FAIL' if errors else 'ok'} ({len(errors)} errors, {len(warnings)} warnings)")
    if lr:
        print(f"layout: {lr['direction']}{' (chosen automatically)' if lr['auto'] else ''}; " + (", ".join(layout_findings(lr)) or "no overlaps")
              + f"; {lr['bends']} bends in {ne} edges")
        if lr["node_hits"] or lr["title_hits"] or lr["crossings"] > 12 or lr["label_conflicts"] > 2 or lr["jogs"] > 3 or lr["detour"] > 2 or lr["bends"] > 3 * max(ne, 1):
            print("  dense: shorten labels, drop edges to nodes everything uses, group nodes in subgraphs, or split the diagram")
    if model.kind == "sequence":
        sr = model.sequence_report
        found = [f"{sr['clashes']} text(s) lie on another text or on a message line (shorten them)" if sr["clashes"] else "",
                 f"{sr['note_hits']} note(s) cover the lifeline of a participant they do not name (shorten the note, or put it over the participants)" if sr["note_hits"] else ""]
        print("layout: sequence; " + (", ".join(f for f in found if f) or "no overlaps") + f"; {sr['messages']} messages, {sr['frames']} frames")
    for line in cap([f"  {m}" for m in errors + warnings]):
        print(line)
    note = legend_note(model)
    if note:
        print(f"legend: {note}")
    srcs =[resolve_src(os.path.normpath(p), os.path.dirname(os.path.abspath(mmd))) for p in model.srcs]
    res = facts.check(model, srcs)
    if res is None:
        print("names: NOT CHECKED - " + (f"none of the `%% src:` paths exist from {os.getcwd()}: {' '.join(srcs[:4])} (run build from the source repo's root, or use absolute paths)"
                                         if srcs else "add a `%% src: path ...` line to check labels against the source"))
    else:
        checked, missing, n_files, absent, skipped = res
        if absent:
            print(f"names: note - `%% src:` paths not found, skipped: {' '.join(absent[:4])}")
        if skipped:
            print(f"names: note - {len(skipped)} files not read (binary or over the size limit): {' '.join(skipped[:4])}" + (" ..." if len(skipped) > 4 else ""))
        if missing:
            print(f"names: {len(missing)} of {checked} code names NOT FOUND in {n_files} source files (misspelled or invented?)")
            for line in cap([f"  {tok}   (in {where})" for tok, where in missing], 10):
                print(line)
        else:
            print(f"names: all {checked} code names found in {n_files} source files")
    if model.kind == "flow":
        er = facts.check_edges(model, srcs)
        if er:
            checked, bad, unclear = er
            if bad:
                print(f"imports: {len(bad)} of {checked} solid arrows between source files have NO import behind them (either direction): "
                      + ", ".join(f"{a} -> {b}" for a, b in bad[:5]) + "; fix them, or draw other relations dashed")
            elif checked:
                print(f"imports: all {checked} solid arrows between source files match a real import")
            if unclear:
                print(f"imports: {unclear} solid arrows NOT CHECKED - a file name in them fits several source files; write its directory in the label (src/one/a.js)")
    if max(w, h) > 3500:
        print(f"size: {int(w)}x{int(h)} px is too large to read in one image; drop leaf nodes, wrap groups or split the diagram")
    hint = strip_hint(model, w, h)
    if hint:
        print(f"shape: {hint}")
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
