// Fixed program run inside Affinity for affinity_apply. The plan is validated by apply.ts and
// embedded as a JSON literal; the program never evaluates plan strings as code.
// Phases: resolve refs and check expectations, build every command (no change yet), execute them
// as one compound command (one undo step), then verify and report.

export const SHAPES = {
  rectangle: "ShapeRectangle",
  ellipse: "ShapeEllipse",
  triangle: "ShapeTriangle",
  diamond: "ShapeDiamond",
  star: "ShapeStar",
  polygon: "ShapePolygon",
  trapezoid: "ShapeTrapezoid",
  heart: "ShapeHeart",
  cloud: "ShapeCloud",
  cog: "ShapeCog",
  arrow: "ShapeArrow",
  tear: "ShapeTear",
  crescent: "ShapeCrescent",
  spiral: "ShapeSpiral",
  pie: "ShapePie",
  segment: "ShapeSegment",
  callout_rectangle: "ShapeCalloutRectangle",
  callout_ellipse: "ShapeCalloutEllipse",
} as const

export function applyScript(plan: unknown): string {
  return `'use strict';
const PLAN = ${JSON.stringify(plan)};
const SHAPES = ${JSON.stringify(SHAPES)};
const { Document } = require('/document.js');
const { AddChildNodesCommandBuilder, CompoundCommandBuilder, DocumentCommand } = require('/commands.js');
const { Rectangle, Transform } = require('/geometry.js');
const { ShapeNodeDefinition, ArtTextNodeDefinition } = require('/nodes.js');
const ShapesModule = require('/shapes.js');
const { Selection, TextSelection } = require('/selections.js');
const { Colour, Gradient, ColourStop } = require('/colours.js');
const { FillDescriptor, GradientFill, GradientFillType } = require('/fills.js');
const { LineStyle } = require('/linestyle.js');
const { StoryBuilder } = require('/storybuilder.js');
const { GlyphAtts } = require('/glyphatts.js');
const print = (value) => console.log(JSON.stringify(value));
class PlanError extends Error {}
const fail = (message) => { throw new PlanError(message); };
const safe = (read, fallback = null) => { try { const v = read(); return v === undefined ? fallback : v; } catch (e) { return fallback; } };
const round = (n) => Math.round(n * 100) / 100;
const boxOf = (node) => safe(() => { const b = node.spreadBaseBox; return { x: b.x, y: b.y, width: b.width, height: b.height }; });
const rounded = (b) => b && { x: round(b.x), y: round(b.y), width: round(b.width), height: round(b.height) };
const kind = (node) => String(node).replace(/^\\[object (.*)\\]$/, '$1');
const revisionOf = (doc) => safe(() => { const h = doc.history; return 'h' + h.position + '-' + h.size; }, 'unknown');

function main() {
  const all = safe(() => Document.all, []);
  const doc = PLAN.document ? all.find((d) => String(d.sessionUuid) === PLAN.document) : safe(() => Document.current);
  if (!doc) fail(PLAN.document ? 'document_not_found' : 'no_document_open');
  const revision = revisionOf(doc);
  if (PLAN.if_revision && PLAN.if_revision !== revision) fail('stale_revision: the document is at ' + revision);

  const nodes = new Map();
  let visited = 0;
  const index = (node) => {
    if (++visited > 50000) fail('document_too_large');
    const id = safe(() => String(node.nodeId));
    if (id) nodes.set(id, node);
    for (const child of safe(() => [...node.children], [])) index(child);
  };
  for (const spread of safe(() => [...doc.spreads], [])) index(spread);
  const before = new Set(nodes.keys());

  for (const clause of PLAN.expect || []) {
    const node = nodes.get(clause.ref);
    if (clause.exists === false) { if (node) fail('expectation_failed: ' + clause.ref + ' exists'); continue; }
    if (!node) fail('expectation_failed: ' + clause.ref + ' is missing');
    if (clause.name !== undefined && safe(() => String(node.description)) !== clause.name) fail('expectation_failed: ' + clause.ref + ' name differs');
    if (clause.type !== undefined && kind(node) !== clause.type) fail('expectation_failed: ' + clause.ref + ' type differs');
  }

  // Geometry known to the plan, updated as operations are planned; null once unknown.
  const boxes = new Map();
  const consumed = new Map();
  const touched = new Set();
  const node = (ref, i) => {
    if (consumed.has(ref)) fail('operation ' + i + ': ' + ref + ' was ' + consumed.get(ref) + ' by an earlier operation');
    const found = nodes.get(ref);
    if (!found) fail('operation ' + i + ': unknown ref ' + ref);
    if (safe(() => found.isLocked) === true) fail('operation ' + i + ': ' + ref + ' is locked');
    touched.add(ref);
    return found;
  };
  const many = (refs, i) => refs.map((ref) => node(ref, i));
  const box = (ref, i) => {
    node(ref, i);
    if (!boxes.has(ref)) boxes.set(ref, boxOf(nodes.get(ref)));
    const b = boxes.get(ref);
    if (!b) fail('operation ' + i + ': the box of ' + ref + ' is unknown after a rotation');
    return b;
  };
  const colour = (hex) => {
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})?$/i.exec(hex);
    return Colour.createRGBA8({ r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16), alpha: m[4] ? parseInt(m[4], 16) : 255 });
  };
  const fill = (spec, b) => {
    if (spec === 'none') return FillDescriptor.createNone();
    if (spec.colour) return FillDescriptor.createSolid(colour(spec.colour));
    const g = spec.gradient;
    const gradient = Gradient.create(g.stops.map((s) => new ColourStop(colour(s.colour), s.position)));
    const type = g.type === 'radial' ? GradientFillType.Radial : GradientFillType.Linear;
    let transform;
    if (type === GradientFillType.Radial) {
      const r = Math.max(b.width, b.height) / 2;
      transform = Transform.createTranslate(b.x + b.width / 2, b.y + b.height / 2).multiply(Transform.createScale(r, r));
    } else {
      // Unit segment (0,0)->(1,0) mapped across the box at the given angle (0 = left to right, 90 = top to bottom).
      const a = ((g.angle || 0) * Math.PI) / 180;
      const length = Math.abs(b.width * Math.cos(a)) + Math.abs(b.height * Math.sin(a));
      const cx = b.x + b.width / 2, cy = b.y + b.height / 2;
      transform = Transform.createTranslate(cx - (Math.cos(a) * length) / 2, cy - (Math.sin(a) * length) / 2)
        .multiply(Transform.createRotate(a)).multiply(Transform.createScale(length, length));
    }
    return FillDescriptor.create(GradientFill.create(gradient, type), false, transform);
  };
  const rect = (b) => new Rectangle(b.x, b.y, b.width, b.height);
  const select = (list) => Selection.create(doc, list);

  const commands = [];
  const additions = AddChildNodesCommandBuilder.create();
  let added = 0;
  const strokeCommands = (selection, spec) => {
    const out = [];
    if (spec.colour !== undefined) out.push(DocumentCommand.createSetPenFill(selection, spec.colour === 'none' ? FillDescriptor.createNone() : FillDescriptor.createSolid(colour(spec.colour))));
    if (spec.width !== undefined) out.push(DocumentCommand.createSetLineStyle(selection, LineStyle.createDefaultWithWeight(spec.width)));
    return out;
  };

  PLAN.operations.forEach((op, i) => {
    switch (op.op) {
      case 'create_shape': {
        const shape = ShapesModule[SHAPES[op.shape]].create();
        const definition = ShapeNodeDefinition.create(shape, rect(op.box), op.fill ? fill(op.fill, op.box) : FillDescriptor.createSolid(colour('#808080')));
        additions.addNode(definition);
        added += 1;
        break;
      }
      case 'create_text': {
        const sb = StoryBuilder.create();
        sb.setToArtisticTextDefaultStyle(doc.dpi, doc.rasterFormat);
        const atts = GlyphAtts.create();
        atts.height = op.size;
        atts.brushFill = op.fill && op.fill.colour ? FillDescriptor.createSolid(colour(op.fill.colour)) : FillDescriptor.createSolid(colour('#000000'));
        sb.setGlyphAtts(atts);
        sb.addText(op.text);
        additions.addNode(ArtTextNodeDefinition.createFromStoryBuilder({ x: op.position.x, y: op.position.y }, sb));
        added += 1;
        break;
      }
      case 'set_fill':
        // Fill transforms live in the object's local frame (baseBox), which moves and rotations keep.
        for (const ref of op.refs) {
          const target = node(ref, i);
          const local = safe(() => { const b = target.baseBox; return { x: b.x, y: b.y, width: b.width, height: b.height }; });
          if (!local && op.fill !== 'none' && !op.fill.colour) fail('operation ' + i + ': the local box of ' + ref + ' is unavailable');
          // copyTransform defaults to true and would keep the previous fill's transform.
          commands.push(DocumentCommand.createSetBrushFill(select([target]), fill(op.fill, local), { copyTransform: false }));
        }
        break;
      case 'set_stroke':
        commands.push(...strokeCommands(select(many(op.refs, i)), op));
        break;
      case 'set_opacity':
        commands.push(DocumentCommand.createSetOpacity(select(many(op.refs, i)), op.opacity));
        break;
      case 'move':
        for (const ref of op.refs) {
          const b = box(ref, i);
          commands.push(DocumentCommand.createTransform(select([node(ref, i)]), Transform.createTranslate(op.dx, op.dy)));
          boxes.set(ref, { ...b, x: b.x + op.dx, y: b.y + op.dy });
        }
        break;
      case 'set_box': {
        const b = box(op.ref, i);
        if (b.width <= 0 || b.height <= 0) fail('operation ' + i + ': ' + op.ref + ' has an empty box');
        const t = op.box;
        const xf = Transform.createTranslate(t.x, t.y).multiply(Transform.createScale(t.width / b.width, t.height / b.height)).multiply(Transform.createTranslate(-b.x, -b.y));
        commands.push(DocumentCommand.createTransform(select([node(op.ref, i)]), xf));
        boxes.set(op.ref, { ...t });
        break;
      }
      case 'rotate':
        for (const ref of op.refs) {
          const b = box(ref, i);
          const cx = b.x + b.width / 2, cy = b.y + b.height / 2;
          const xf = Transform.createTranslate(cx, cy).multiply(Transform.createRotate((op.degrees * Math.PI) / 180)).multiply(Transform.createTranslate(-cx, -cy));
          commands.push(DocumentCommand.createTransform(select([node(ref, i)]), xf));
          boxes.set(ref, null);
        }
        break;
      case 'align': {
        const items = op.refs.map((ref) => ({ ref, b: box(ref, i) }));
        let target;
        if (op.to === 'page') {
          const spread = safe(() => doc.currentSpread);
          target = safe(() => { const e = spread.getSpreadExtents(); return { x: e.x, y: e.y, width: e.width, height: e.height }; });
          if (!target) fail('operation ' + i + ': page extents are unavailable');
        } else {
          const x = Math.min(...items.map((it) => it.b.x)), y = Math.min(...items.map((it) => it.b.y));
          const r = Math.max(...items.map((it) => it.b.x + it.b.width)), bt = Math.max(...items.map((it) => it.b.y + it.b.height));
          target = { x, y, width: r - x, height: bt - y };
        }
        for (const { ref, b } of items) {
          const dx = op.horizontal === 'left' ? target.x - b.x : op.horizontal === 'center' ? target.x + (target.width - b.width) / 2 - b.x : op.horizontal === 'right' ? target.x + target.width - b.width - b.x : 0;
          const dy = op.vertical === 'top' ? target.y - b.y : op.vertical === 'middle' ? target.y + (target.height - b.height) / 2 - b.y : op.vertical === 'bottom' ? target.y + target.height - b.height - b.y : 0;
          if (dx === 0 && dy === 0) continue;
          commands.push(DocumentCommand.createTransform(select([node(ref, i)]), Transform.createTranslate(dx, dy)));
          boxes.set(ref, { ...b, x: b.x + dx, y: b.y + dy });
        }
        break;
      }
      case 'rename':
        commands.push(DocumentCommand.createSetDescription(select(many(op.refs, i)), op.name));
        break;
      case 'set_visible':
        commands.push(DocumentCommand.createSetVisibility(select(many(op.refs, i)), op.visible));
        break;
      case 'set_text': {
        const target = node(op.ref, i);
        if (!/Text/.test(kind(target))) fail('operation ' + i + ': ' + op.ref + ' is not text');
        // Without a text sub-selection over the whole story, SetText appends instead of replacing.
        const range = safe(() => target.storyInterface.storyRange);
        if (!range) fail('operation ' + i + ': the text of ' + op.ref + ' is unavailable');
        const selection = Selection.createEmpty(doc);
        selection.addNode(target);
        selection.addSubSelectionForNode(target, TextSelection.create([{ begin: range.begin, end: range.end }]));
        commands.push(DocumentCommand.createSetText(selection, op.text));
        break;
      }
      case 'group':
        commands.push(DocumentCommand.createGroupNodes(select(many(op.refs, i))));
        for (const ref of op.refs) consumed.set(ref, 'grouped');
        break;
      case 'ungroup':
        commands.push(DocumentCommand.createUngroupNodes(select(many(op.refs, i))));
        for (const ref of op.refs) consumed.set(ref, 'ungrouped');
        break;
      case 'boolean': {
        const factory = { union: 'createBoolOpUnion', subtract: 'createBoolOpSubtract', intersect: 'createBoolOpIntersect', xor: 'createBoolOpXor' }[op.mode];
        commands.push(DocumentCommand[factory](select(many(op.refs, i))));
        for (const ref of op.refs) consumed.set(ref, 'combined');
        break;
      }
      case 'delete':
        commands.push(DocumentCommand.createDeleteSelection(select(many(op.refs, i))));
        for (const ref of op.refs) consumed.set(ref, 'deleted');
        break;
      case 'select':
        commands.push(DocumentCommand.createSetSelection(select(many(op.refs, i))));
        break;
      default:
        fail('operation ' + i + ': unknown op');
    }
  });

  // Creations first (they never depend on edits), then edits in plan order: one compound, one undo step.
  const all_ = [];
  if (added > 0) all_.push(additions.createCommand(false));
  all_.push(...commands);
  if (all_.length === 0) return print({ applied: false, reason: 'nothing_to_do', revision });
  const compound = CompoundCommandBuilder.create();
  for (const command of all_) compound.addCommand(command);
  const historyBefore = safe(() => doc.history.position);
  const command = compound.createCommand();
  executed = true;
  doc.executeCommand(command);

  nodes.clear(); visited = 0;
  for (const spread of safe(() => [...doc.spreads], [])) index(spread);
  const describe = (ref) => { const n = nodes.get(ref); return { ref, type: kind(n), name: safe(() => String(n.description)), box: rounded(boxOf(n)) }; };
  const created = [...nodes.keys()].filter((ref) => !before.has(ref));
  const removed = [...before].filter((ref) => !nodes.has(ref));
  const historyAfter = safe(() => doc.history.position);
  print({
    applied: true,
    document: String(doc.sessionUuid),
    revision: revisionOf(doc),
    undo: historyBefore !== null && historyAfter !== null ? (historyAfter - historyBefore === 1 ? 'one_step' : 'steps:' + (historyAfter - historyBefore)) : 'unknown',
    created: created.slice(0, 200).map(describe),
    changed: [...touched].filter((ref) => nodes.has(ref)).slice(0, 200).map(describe),
    removed: removed.slice(0, 200),
  });
}

// Errors before execution change nothing; after it, the outcome is unknown and said so.
let executed = false;
try { main(); } catch (e) { print({ applied: executed ? 'unknown' : false, error: e instanceof PlanError ? e.message : 'affinity_error: ' + String(e && e.message || e).slice(0, 500) }); }
`
}
