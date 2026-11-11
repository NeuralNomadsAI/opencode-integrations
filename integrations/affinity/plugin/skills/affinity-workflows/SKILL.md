---

name: affinity-workflows
description: Inspect, script and verify Affinity documents safely.
autoinvoke: false
---

# OpenCode for Affinity

The `affinity_*` tools talk to the MCP server embedded in Affinity by Canva (Settings → Scripts →
Model Context Protocol). If a tool reports that Affinity is not reachable, ask the user to open
Affinity and enable its MCP server; never guess another port or start Affinity yourself.

## Inspect first

1. Call `affinity_context` to see open documents, the current spread's object tree and the
   selection. Each object has a `ref` (its `nodeId`), a type, a name and a box in pixels.
2. Use `document` (session UUID) when several documents are open; never assume the current one.
3. Read `affinity_docs` before scripting: the `preamble`, then the modules you need
   (`document.js`, `nodes.js`, `commands.js`, `shapes.js`, `fills.js`, `colours.js`…), an example
   from `examples/`, and `ts/...d.ts` for exact native signatures. Search `affinity_hints` first
   when stuck.

## Change with `affinity_apply` first

- Use `affinity_apply` for everything it covers: shapes, text, fills and gradients, strokes,
  opacity, moves, resizes, rotations, alignment, names, visibility, text replacement, groups,
  boolean operations, deletion and selection. One call is one undo step, checked before anything
  changes.
- Pass `if_revision` (from `affinity_context`), `expect` clauses for the refs you rely on and a fresh
  `command_id`. On `stale_revision`, re-read `affinity_context` and rebuild the plan; never retry blindly.
- Refs of objects created by a plan come back in its result; use them in the next call.
- New objects land at Affinity's insertion point, sometimes behind backgrounds: after creating,
  render, and `arrange` them `to: "front"` (or above a ref) if they are hidden.
- If a call reports that the change "may be applied", re-read `affinity_context` before anything else.

## Scripts for the rest, still one undo step

Use `affinity_execute_script` only for what `affinity_apply` cannot express (raster filters,
adjustments, layer effects, pages, export…). It asks the user's permission every time.

- Write plain, directly executable JavaScript; print results with `console.log`.
- Build changes as commands (`AddChildNodesCommandBuilder`, `DocumentCommand.*`) and group them
  with `CompoundCommandBuilder` so the user can undo them in one step.
- Target objects by `ref`: find the node whose `nodeId` matches before changing it, and stop if it
  is missing.
- Work in a new document (`Document.create(NewDocumentOptions.createDefault())`) unless the user
  named an existing one. Never save, export or close documents without being asked.
- After every change, call `affinity_render` (or `selection: true`) and look at the image;
  re-read `affinity_context` to confirm boxes and names.

## Known SDK pitfalls

- `ShapeNodeDefinition.createDefault()` has no fill or stroke: the shape exists but is invisible.
  Use `ShapeNodeDefinition.create(shape, rectangle, brushFill)` with a `Colour` or `FillDescriptor`.
- `Document.create()` needs `NewDocumentOptions.createDefault()`; the default A4 document is
  2480 × 3508 px at 300 dpi.
- `DocumentCommand.createSetBrushFill` keeps the previous fill's transform unless
  `{ copyTransform: false }` is passed; fill transforms of existing objects use their local frame
  (`node.baseBox`), not spread coordinates.
- `DocumentCommand.createSetText` appends unless the selection holds a `TextSelection` over the whole
  `node.storyInterface.storyRange` (`Selection.createEmpty`, `addNode`, then `addSubSelectionForNode`).
- Gradients: `FillDescriptor.create(GradientFill.create(Gradient.create(stops), GradientFillType.Linear), false, transform)`
  with `scaleWithObject = false` and an absolute spread transform mapping the unit segment (0,0)→(1,0)
  onto the gradient line, e.g. `Transform.createTranslate(x, y).multiply(Transform.createRotate(Math.PI / 2)).multiply(Transform.createScale(h, h))`
  for a vertical gradient. With unit (object-relative) coordinates the gradient collapses to one colour
  without any error.
- `Font.create(family, weight, italic)` can fail with `INVALID_ARGS`; list fonts with
  `document.getFontNames()` or keep the default font.
- Art text is positioned by its baseline origin; read `node.spreadBaseBox` after creation to align it.

## Permissions

`affinity_execute_script` runs arbitrary SDK code and always asks the named permission
`affinity_execute_code`. Affinity's own settings (filesystem, network, AI) still apply: a
`NOT_ALLOWED` result means the user restricted it there; do not try to work around it.
`affinity_hints` sends the query to Canva's shared hint service.
