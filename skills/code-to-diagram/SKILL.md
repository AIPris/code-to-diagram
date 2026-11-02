---
name: code-to-diagram
description: Turn code into a diagram - an editable draw.io file (.drawio) plus a PNG. Use whenever the user wants to visualize code or a system - class, ER, state, sequence diagrams, flowcharts, call graphs, import/dependency graphs, architecture overviews - or says "diagram this", "draw a flowchart of...", "show me how X works visually", or mentions draw.io/drawio, even without saying draw.io. Also use to update, redo or extend an existing diagram of a codebase.
---

# Code to Diagram

You write a short Mermaid spec. One command lays it out, writes the `.drawio` (edges glued to shapes, real containers, plain XML the user can keep editing in draw.io), checks it and renders a verified PNG. Never write XML or coordinates yourself: that is slow, and the script does it better.

`SKILL_DIR` = the "Base directory for this skill" shown when this skill loaded. Run the scripts by that absolute path (`python` or `python3`, 3.9+); the project may have its own, different `scripts/render_png.sh`.

## Workflow

1. **Scope.** Take the diagram type and the code from the request; ask only if either is truly unclear (unattended: pick the likeliest reading and state it at the end). Draw the smallest slice that answers the question: one flow, one module, the hubs. What the request names (error paths, all inputs, every outcome) is part of the question and is never cut for size.
   **Checklist:** before gathering facts, list what the request asks the diagram to show (paths, outcomes, inputs, outputs, error cases), and for each item the `file:line` where the code does it. Fill the list while gathering; an item with no `file:line` is not drawn. Past ~25 nodes a diagram stops being readable: group with subgraphs, drop leaves, or split in two.
2. **Gather facts cheaply.** Grep/Read only what the diagram will claim (signatures, branches, schema, call order); do not read whole files for what one grep answers. **Imports and module dependencies are not read at all, they are scanned:**
   `python "$SKILL_DIR/scripts/c2d.py" deps <dir-or-file>... [--entry main.js --reach 1] [--exclude text] > diagrams/<slug>/<slug>.mmd`
   (JS/TS/Python; groups by directory, colours hubs and leaves, collapses an entry file that imports most of the rest; `--entry` takes a file name or the end of its path; `--exclude test` leaves out tests and tooling). Edit that file instead of retyping it.
3. **Write the spec** (draw every checklist item; an optional `title Text` line is drawn as a heading in every diagram type) to `diagrams/<slug>/<slug>.mmd` (kebab-case slug, under the working directory). First lines: `%% src: <files or dirs the facts come from>`, then the diagram.
4. **Build:** `python "$SKILL_DIR/scripts/c2d.py" build diagrams/<slug>/<slug>.mmd`
   - `SPEC ERRORS` lists unsupported or malformed lines (nothing is built): fix them, rebuild.
   - `structure` warnings: fix the real ones.
   - `legend: node fills with no legend entry`: nodes are drawn in a colour the `%% legend:` does not explain (decisions are yellow and plain nodes blue unless classed): add an entry for each colour, or give those nodes the colour of an entry. Only checked when the legend has colour entries; start, end and note shapes are exempt.
   - `layout: sequence;` a sequence diagram is measured too: texts (message labels, notes, frame conditions) lying on one another or on a message line, and notes that hide the lifeline of a participant they do not name (shorten the text, or put the note over the participants). Each header is grouped with its lifeline, and `alt`/`opt`/`loop`/`break` are UML frames (`umlFrame`) with the condition beside the tab.
   - `names ... NOT FOUND in source`: a label does not exist in the `%% src:` files; misspelled or invented? Check and correct (a deliberate pseudo-name is fine).
   - `imports: N solid arrows ... have NO import behind them`: those arrows claim an import that the code does not contain; fix them or draw them dashed.
   - `names: note - N files not read`: binary or very large files were skipped; every other file is searched, whatever its extension.
   - `imports: N solid arrows NOT CHECKED`: a file name in them fits several source files (two `index.js`); write its directory in the label (`src/one/index.js`) to have the arrow checked.
   - The `built` line ends with the picture's proportions (`3.4:1` wide, `1:2.8` tall) and, for every type but sequence diagrams, the share of it the nodes cover (`nodes cover 9%`: the rest is empty room, and the text shrinks with it). `shape:` appears when the picture is a strip, more than 2.5:1 either way and over ~1500 px long (a short chain of three nodes is a strip that still reads fine): fitted to a screen its text becomes too small, so follow its advice (switch direction, group nodes in subgraphs that stack across the short side, drop leaves, split the diagram).
   - `layout:` is measured, not guessed: edges running through nodes, edges striking through a container title (the build routes edges and self-loops round titles where it can do so without a kink or detour; one that is still counted is one it could not: shorten the title or move the node it enters), edges within 4 px of a node or a container title they do not belong to (hugging it, so that it looks like the line starts there; the build gives such an edge another route where it can), decisions whose two exits line up across the diamond, edge crossings, kinks (a sideways step of under 24 px in a line meant to be straight), detours (several edges that run out past their target and back), two edges drawn on top of each other, labels lying on lines, nodes or container title bars (labels are placed clear of all three; one that is still counted had no free spot on its edge: shorten it), labels within 4 px of a node or title bar, and the bends of all edges together. `no overlaps` means nothing to fix; a `dense:` hint says what to cut. Many bends per edge or crossings mean too many links between distant nodes: collapse or drop the hub, or group.
5. **Look once.** Read the PNG named after `png:` (only when it says verified complete; never a PNG from an earlier run). Judge what a script cannot: readable, the layout tells the story. Fix by editing the spec (shorter labels, regroup, change direction, cut nodes) and rebuild; two rebuilds are the default; allow a third when the open issues are known and the spec can fix them. Then report what is left. Layout fixes by hand in the XML are overwritten by the next build.
6. **Tick off the checklist:** each item is drawn or deliberately left out; name the left-out ones and why in the report.
7. **Report** briefly: the `.drawio`, `.png` and `.mmd` paths, what the diagram shows, what you left out, assumptions.

## Spec: the Mermaid subset that is supported

- **flowchart TD|LR** (TD = top-down; state, class and ER diagrams choose top-down or left-to-right themselves unless the spec says `direction LR`). Nodes: `A[text]` box, `A([text])` start/end, `A{text}` decision, `A((text))` circle, `A[(text)]` database, `A[/text/]` I/O, `A(text)`. Edges: `-->`, `---`, `-.->`, `==>`, `-->|label|`, `-- label -->`, `A & B --> C`. `subgraph id [Title]` ... `end` (nestable; an edge may end at a subgraph id). Colour: `classDef name fill:#dae8fc,stroke:#6c8ebf,stroke-width:3px`, `A:::name`, `class A,B name`; the names `blue green red yellow purple gray white` work without classDef.
- Line break inside a label: `<br/>`. Ids: letters, digits, `_` only. Quote labels that contain brackets, parentheses or `|`: `A["parse(text) | none"]`.
- Comment directives: `%% src: src/ "my repo/lib.js"` (files or dirs the labels are checked against, quote paths with spaces; always give it), `%% legend: hub=imported by 4+ files; leaf=imports nothing` (class or colour name = meaning; an arrow style too, `-->`, `-.->` or `==>` = meaning, drawn with a sample line, e.g. `-.->=calls`; drawn as a legend box, in sequence diagrams too; only when colour or line style carries meaning).
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
- Sequence diagrams: `alt`/`else` when a step has alternative outcomes, `opt` for a step that may be skipped, `loop` for repetition. `break [cond]` means the rest of the enclosing fragment is skipped when `cond` holds (an early exit): use it for that alone, and do not follow it with an `alt` that retells the same failure.
- Labels at most ~30 characters, names exactly as in the code; detail goes into class members or rows, not into long labels.
- At most 3 colours, each meaning something; name classes by meaning (`hub`, `error`) and add the `%% legend:`.
- Pipelines and long chains read better `LR`; hierarchies `TB`. A chain longer than ~12 steps: split by phase into subgraphs or use `LR`.
- Dense graph (many crossing edges in the PNG): drop edges to a node everything uses, or group into subgraphs and link the groups.
- Lines come out straight when linked nodes sit on consecutive levels and overlap in width; an edge that skips levels runs in its own lane and needs a turn or two. A node that links to many far-apart nodes is what makes a diagram messy: collapse it (`deps --entry`), or give its targets one subgraph and link the subgraph.
- A node with three or more unlabelled links to one side is drawn as a tree: one stem out of the node, one shared trunk, a drop into each target (arrows from the same node lying on that trunk are by design, not a defect). Put a label on an edge only when the label says something (a decision's `yes`/`no`): a labelled edge keeps its own line so the label belongs to one arrow, and a fan of labelled edges draws as separate lines again.
- A decision `{}` takes its edges at its four corners, never part-way along a side (a self-loop squeezed between neighbours excepted). Edges that leave through one corner (or arrive at one) share its first stretch and fan out after it, by design, not a defect; where a side corner is free, a way back against the flow leaves by it instead of running on top of the way in. What the build still counts is two exits at opposite corners (left and right, or top and bottom), which read as one line through the diamond: give the decision a main path down and let the other branches leave to one side.
## Updating

- Folder has a `.mmd`: edit it and rebuild. The previous `.drawio`/`.png` are copied to `history/` automatically when they are from an earlier session or were changed since the last build (the user edited them in draw.io); pass `--archive` when the user wants the old version kept for comparison. If the folder has a `description.md`, keep it true.
- Only a `.drawio` exists (hand-made or from an older version of this skill): `python "$SKILL_DIR/scripts/c2d.py" digest <file.drawio>` lists what it shows; write a spec from that and the code, rebuild. Tell the user their manual layout does not carry over (the old file stays in `history/`).
- The user hand-edited the `.drawio` and only wants a small change: edit the XML in place instead (`references/xml.md`), check it with `c2d.py check <file>`, re-render with `c2d.py png <file>`.
