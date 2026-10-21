# Editing a .drawio file in place

Only needed when the user hand-edited a diagram and wants a small change that must keep their layout. Otherwise change the `.mmd` and rebuild.

The file is uncompressed `mxfile > diagram > mxGraphModel > root`. Cells `id="0"` and `id="1"` are the root and layer; every other cell has `parent="1"` or a container id.

```xml
<mxCell id="svc" value="AuthService" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;" vertex="1" parent="1">
  <mxGeometry x="100" y="100" width="140" height="60" as="geometry"/>
</mxCell>
<mxCell id="e1" value="calls" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=classic;" edge="1" parent="1" source="svc" target="db">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

- **Edges are glued**: `source` and `target` name real cell ids. An edge without them stays behind when someone moves a shape; `c2d.py check` reports it.
- **Containers** (`swimlane` / `container=1`): children use `parent="<container id>"` and coordinates relative to it. Edges stay on `parent="1"`; waypoints (`<Array as="points"><mxPoint x= y=/></Array>`) are then absolute.
- **Escaping** (labels render as HTML): XML-escape `& < > "` and, to show a literal `<` or `>` in the text, escape twice: `List&amp;lt;Order&amp;gt;`. Single-escaped `&lt;Order&gt;` is read as an HTML tag and the rest of the label disappears. Line break: `&lt;br&gt;`.
- Anchors on an edge: `exitX=0.5;exitY=1;entryX=0.5;entryY=0;` (fractions of the shape's box).
- Colours that work together: fill/stroke blue `#dae8fc/#6c8ebf`, green `#d5e8d4/#82b366`, red `#f8cecc/#b85450`, yellow `#fff2cc/#d6b656`, purple `#e1d5e7/#9673a6`, gray `#f5f5f5/#666666`.
- After editing: `python "$SKILL_DIR/scripts/c2d.py" check file.drawio`, then `python "$SKILL_DIR/scripts/c2d.py" png file.drawio` for a verified PNG.
