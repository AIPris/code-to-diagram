---
name: code-to-diagram
description: Turn code into a diagram - an editable draw.io file (.drawio) plus a PNG. Use whenever the user wants to visualize code or a system - class, ER, state, sequence diagrams, flowcharts, call graphs, import/dependency graphs, architecture overviews - or says "diagram this", "draw a flowchart of...", "show me how X works visually", or mentions draw.io/drawio, even without saying draw.io. Also use to update, redo or extend an existing diagram of a codebase.
---

# Code to Diagram

You write a short Mermaid spec. One command lays it out, writes the `.drawio` (edges glued to shapes, real containers, plain XML the user can keep editing in draw.io), checks it and renders a verified PNG. Never write XML or coordinates yourself: that is slow, and the script does it better.

`SKILL_DIR` = the "Base directory for this skill" shown when this skill loaded. Run the scripts by that absolute path (`python` or `python3`, 3.9+); the project may have its own, different `scripts/render_png.sh`.

## Workflow

1. **Scope.** Take the diagram type and the code from the request; ask only if either is truly unclear (unattended: pick the likeliest reading and state it at the end). Draw the smallest slice that answers the question: one flow, one module, the hubs. Past ~25 nodes a diagram stops being readable: group with subgraphs, drop leaves, or split in two.
2. **Gather facts cheaply.** Grep/Read only what the diagram will claim (signatures, branches, schema, call order); do not read whole files for what one grep answers. **Imports and module dependencies are not read at all, they are scanned:**
   `python "$SKILL_DIR/scripts/c2d.py" deps <dir-or-file>... [--entry main.js --reach 1] [--exclude text] > diagrams/<slug>/<slug>.mmd`
   (JS/TS/Python; groups by directory, colours hubs and leaves, collapses an entry file that imports most of the rest; `--entry` takes a file name or the end of its path; `--exclude test` leaves out tests and tooling). Edit that file instead of retyping it.
3. **Write the spec** to `diagrams/<slug>/<slug>.mmd` (kebab-case slug, under the working directory). First lines: `%% src: <files or dirs the facts come from>`, then the diagram.
4. **Build:** `python "$SKILL_DIR/scripts/c2d.py" build diagrams/<slug>/<slug>.mmd`
   - `SPEC ERRORS` lists unsupported or malformed lines (nothing is built): fix them, rebuild.
   - `structure` warnings: fix the real ones.
   - `names ... NOT FOUND in source`: a label does not exist in the `%% src:` files; misspelled or invented? Check and correct (a deliberate pseudo-name is fine).
   - `imports: N solid arrows ... have NO import behind them`: those arrows claim an import that the code does not contain; fix them or draw them dashed.
   - `imports: N solid arrows NOT CHECKED`: a file name in them fits several source files (two `index.js`); write its directory in the label (`src/one/index.js`) to have the arrow checked.
   - `layout:` is measured, not guessed: edges running through nodes, edge crossings, kinks (a sideways step of under 24 px in a line meant to be straight), detours (several edges that run out past their target and back), two edges drawn on top of each other, labels lying on lines, and the bends of all edges together. `no overlaps` means nothing to fix; a `dense:` hint says what to cut. Many bends per edge or crossings mean too many links between distant nodes: collapse or drop the hub, or group.
5. **Look once.** Read the PNG named after `png:` (only when it says verified complete; never a PNG from an earlier run). Judge what a script cannot: readable, the layout tells the story. Fix by editing the spec (shorter labels, regroup, change direction, cut nodes) and rebuild; at most two rebuilds, then report what is left. Layout fixes by hand in the XML are overwritten by the next build.
6. **Report** briefly: the `.drawio`, `.png` and `.mmd` paths, what the diagram shows, what you left out, assumptions.

## Spec: the Mermaid subset that is supported

- **flowchart TD|LR** (TD = top-down; state, class and ER diagrams choose top-down or left-to-right themselves unless the spec says `direction LR`). Nodes: `A[text]` box, `A([text])` start/end, `A{text}` decision, `A((text))` circle, `A[(text)]` database, `A[/text/]` I/O, `A(text)`. Edges: `-->`, `---`, `-.->`, `==>`, `-->|label|`, `-- label -->`, `A & B --> C`. `subgraph id [Title]` ... `end` (nestable; an edge may end at a subgraph id). Colour: `classDef name fill:#dae8fc,stroke:#6c8ebf,stroke-width:3px`, `A:::name`, `class A,B name`; the names `blue green red yellow purple gray white` work without classDef.
- Line break inside a label: `<br/>`. Ids: letters, digits, `_` only. Quote labels that contain brackets, parentheses or `|`: `A["parse(text) | none"]`.
- Comment directives: `%% src: src/ "my repo/lib.js"` (files or dirs the labels are checked against, quote paths with spaces; always give it), `%% legend: hub=imported by 4+ files; leaf=imports nothing` (class or colour name = meaning, drawn as a legend box; only when colour carries meaning).
- Not supported, reported by line: click/links, themes/init, concurrent states, namespaces.

The other types, one example each (everything else is standard Mermaid):

```
stateDiagram-v2
  [*] --> LOADING
  LOADING --> MENU : boot without level
  state PLAYING {
    [*] --> RUN
  }
  note right of MENU : text
  MENU --> [*]
```
```
sequenceDiagram
  autonumber
  participant L as gameLoop
  L->>S: stepWorld(dt)
  alt player dies
    S-->>L: done
  end
```
```
classDiagram
  class Entity {
    <<interface>>
    +x: number
    +hit(dmg) void
  }
  Body <|-- Entity
  World "1" *-- "*" Entity : entities
```
```
erDiagram
  users ||--o{ posts : writes
  users {
    INTEGER id PK
    TEXT name
  }
```

The scripts are tools, not reading material: you never need to open them (`c2d.py --help` lists the commands).

## Choices that make it read well (layout itself is automatic)

- A solid arrow between nodes that name source files means "imports" and is checked against the code; draw calls, data flow and other relations dashed (`-.->`).
- Flowchart: in each decision write the main path's edge first; it runs straight down and the other branch goes to the side. Label decision edges (`yes`/`no`, or the condition). One terminator per distinct outcome (status code, final state).
- Labels at most ~30 characters, names exactly as in the code; detail goes into class members or rows, not into long labels.
- At most 3 colours, each meaning something; name classes by meaning (`hub`, `error`) and add the `%% legend:`.
- Pipelines and long chains read better `LR`; hierarchies `TB`. A chain longer than ~12 steps: split by phase into subgraphs or use `LR`.
- Dense graph (many crossing edges in the PNG): drop edges to a node everything uses, or group into subgraphs and link the groups.
- Lines come out straight when linked nodes sit on consecutive levels and overlap in width; an edge that skips levels runs in its own lane and needs a turn or two. A node that links to many far-apart nodes is what makes a diagram messy: collapse it (`deps --entry`), or give its targets one subgraph and link the subgraph.
- A node with three or more unlabelled links to one side is drawn as a tree: one stem out of the node, one shared trunk, a drop into each target (arrows from the same node lying on that trunk are by design, not a defect). Put a label on an edge only when the label says something (a decision's `yes`/`no`): a labelled edge keeps its own line so the label belongs to one arrow, and a fan of labelled edges draws as separate lines again.
## Updating

- Folder has a `.mmd`: edit it and rebuild. The previous `.drawio`/`.png` are copied to `history/` automatically when they are from an earlier session or were changed since the last build (the user edited them in draw.io); pass `--archive` when the user wants the old version kept for comparison. If the folder has a `description.md`, keep it true.
- Only a `.drawio` exists (hand-made or from an older version of this skill): `python "$SKILL_DIR/scripts/c2d.py" digest <file.drawio>` lists what it shows; write a spec from that and the code, rebuild. Tell the user their manual layout does not carry over (the old file stays in `history/`).
- The user hand-edited the `.drawio` and only wants a small change: edit the XML in place instead (`references/xml.md`), check it with `c2d.py check <file>`, re-render with `c2d.py png <file>`.
