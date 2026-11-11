// Fixed read-only script run inside Affinity for affinity_context. It only reads the
// document model and prints one JSON line; the limits bound its size.

export type ContextLimits = { document?: string; depth: number; nodes: number }

export function contextScript(limits: ContextLimits): string {
  return `'use strict';
const LIMITS = ${JSON.stringify(limits)};
const { Document } = require('/document.js');
const { Application } = require('/application.js');
const safe = (read, fallback = null) => { try { const value = read(); return value === undefined ? fallback : value; } catch (e) { return fallback; } };
const box = (node) => safe(() => { const b = node.spreadBaseBox; return { x: Math.round(b.x * 100) / 100, y: Math.round(b.y * 100) / 100, width: Math.round(b.width * 100) / 100, height: Math.round(b.height * 100) / 100 }; });
const kind = (node) => String(node).replace(/^\\[object (.*)\\]$/, '$1');
const ref = (node) => safe(() => String(node.nodeId));
const all = safe(() => Document.all, []);
const current = safe(() => Document.current);
const target = LIMITS.document ? all.find((d) => String(d.sessionUuid) === LIMITS.document) : current;
const out = {
  app: safe(() => String(Application.version)),
  documents: all.slice(0, 32).map((d) => ({ document: String(d.sessionUuid), title: safe(() => String(d.title)), current: !!current && String(current.sessionUuid) === String(d.sessionUuid), unsaved: safe(() => !!d.isDirty) })),
};
if (LIMITS.document && !target) out.error = 'document_not_found';
if (target) {
  let budget = LIMITS.nodes;
  let truncated = false;
  const visit = (node, depth) => {
    if (budget <= 0) { truncated = true; return null; }
    budget -= 1;
    const entry = { ref: ref(node), type: kind(node), name: safe(() => String(node.description)) };
    const userName = safe(() => node.userDescription);
    if (userName) entry.userName = String(userName);
    entry.box = box(node);
    if (safe(() => node.isVisible) === false) entry.hidden = true;
    if (safe(() => node.isLocked) === true) entry.locked = true;
    const children = safe(() => [...node.children], []);
    if (children.length > 0) {
      if (depth >= LIMITS.depth) entry.childCount = children.length;
      else entry.children = children.map((child) => visit(child, depth + 1)).filter(Boolean);
    }
    return entry;
  };
  const spread = safe(() => target.currentSpread);
  const selection = safe(() => [...target.selection.nodes], []);
  out.document = {
    document: String(target.sessionUuid),
    revision: safe(() => { const h = target.history; return 'h' + h.position + '-' + h.size; }, 'unknown'),
    title: safe(() => String(target.title)),
    dpi: safe(() => target.dpi),
    sizePixels: safe(() => { const s = target.sizePixels; return { width: s.width, height: s.height }; }),
    spreadCount: safe(() => target.spreadCount),
    pageCount: safe(() => target.pageCount),
    currentSpread: spread ? { extents: safe(() => { const e = spread.getSpreadExtents(); return { x: e.x, y: e.y, width: e.width, height: e.height }; }), children: safe(() => [...spread.children], []).map((n) => visit(n, 1)).filter(Boolean) } : null,
    selection: selection.slice(0, 200).map((n) => ({ ref: ref(n), type: kind(n), name: safe(() => String(n.description)) })),
  };
  if (truncated) out.document.truncated = true;
}
console.log(JSON.stringify(out));
`
}
