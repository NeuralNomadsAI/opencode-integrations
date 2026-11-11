// Validation of affinity_apply plans. Everything the Affinity program receives has been checked
// here first: types, bounds, known operations and shapes; refs are then resolved inside Affinity.

import { SHAPES } from "./apply-script.ts"

export const MAX_OPERATIONS = 50
const MAX_REFS = 200
const REF = /^[0-9A-Za-z_-]{1,40}$/
const COLOUR = /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/
const DOCUMENT = /^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$/
const REVISION = /^h-?\d{1,9}--?\d{1,9}$|^unknown$/
const COMMAND_ID = /^[A-Za-z0-9._:-]{1,80}$/

type Json = Record<string, unknown>

class PlanError extends Error {}

const fail = (path: string, message: string): never => {
  throw new PlanError(`${path}: ${message}`)
}

function record(value: unknown, path: string, keys: string[], required: string[] = []): Json {
  if (typeof value !== "object" || value === null || Array.isArray(value)) fail(path, "expected an object")
  const object = value as Json
  for (const key of Object.keys(object)) if (!keys.includes(key)) fail(path, `unknown field ${key}`)
  for (const key of required) if (object[key] === undefined) fail(path, `missing ${key}`)
  return object
}

function number(value: unknown, path: string, min: number, max: number) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max)
    fail(path, `expected a number from ${min} to ${max}`)
  return value as number
}

function text(value: unknown, path: string, max: number, pattern?: RegExp) {
  if (typeof value !== "string" || value.length === 0 || value.length > max || (pattern && !pattern.test(value)))
    fail(path, "invalid string")
  return value as string
}

function oneOf<T extends string>(value: unknown, path: string, options: readonly T[]) {
  if (!options.includes(value as T)) fail(path, `expected one of ${options.join(", ")}`)
  return value as T
}

function refs(value: unknown, path: string, min = 1) {
  if (!Array.isArray(value) || value.length < min || value.length > MAX_REFS)
    fail(path, `expected ${min} to ${MAX_REFS} refs`)
  const list = (value as unknown[]).map((ref, i) => text(ref, `${path}[${i}]`, 40, REF))
  if (new Set(list).size !== list.length) fail(path, "duplicate ref")
  return list
}

function box(value: unknown, path: string) {
  const b = record(value, path, ["x", "y", "width", "height"], ["x", "y", "width", "height"])
  return {
    x: number(b.x, `${path}.x`, -1e6, 1e6),
    y: number(b.y, `${path}.y`, -1e6, 1e6),
    width: number(b.width, `${path}.width`, 0.01, 1e6),
    height: number(b.height, `${path}.height`, 0.01, 1e6),
  }
}

function fill(value: unknown, path: string) {
  if (value === "none") return "none"
  const f = record(value, path, ["colour", "gradient"])
  if ((f.colour === undefined) === (f.gradient === undefined)) fail(path, 'expected "none", colour or gradient')
  if (f.colour !== undefined) return { colour: text(f.colour, `${path}.colour`, 9, COLOUR) }
  const g = record(f.gradient, `${path}.gradient`, ["stops", "angle", "type"], ["stops"])
  if (!Array.isArray(g.stops) || g.stops.length < 2 || g.stops.length > 16)
    fail(`${path}.gradient.stops`, "expected 2 to 16 stops")
  return {
    gradient: {
      stops: (g.stops as unknown[]).map((stop, i) => {
        const s = record(stop, `${path}.gradient.stops[${i}]`, ["colour", "position"], ["colour", "position"])
        return {
          colour: text(s.colour, `${path}.gradient.stops[${i}].colour`, 9, COLOUR),
          position: number(s.position, `${path}.gradient.stops[${i}].position`, 0, 1),
        }
      }),
      ...(g.angle !== undefined ? { angle: number(g.angle, `${path}.gradient.angle`, -360, 360) } : {}),
      ...(g.type !== undefined ? { type: oneOf(g.type, `${path}.gradient.type`, ["linear", "radial"] as const) } : {}),
    },
  }
}

function operation(value: unknown, i: number): Json {
  const path = `operations[${i}]`
  const op = oneOf((value as Json | null)?.op, `${path}.op`, OPERATIONS)
  switch (op) {
    case "create_shape": {
      const o = record(value, path, ["op", "shape", "box", "fill"], ["shape", "box"])
      const b = box(o.box, `${path}.box`)
      return {
        op,
        shape: oneOf(o.shape, `${path}.shape`, Object.keys(SHAPES)),
        box: b,
        ...(o.fill !== undefined ? { fill: fill(o.fill, `${path}.fill`) } : {}),
      }
    }
    case "create_text": {
      const o = record(value, path, ["op", "text", "position", "size", "colour"], ["text", "position", "size"])
      const p = record(o.position, `${path}.position`, ["x", "y"], ["x", "y"])
      return {
        op,
        text: text(o.text, `${path}.text`, 10_000),
        position: { x: number(p.x, `${path}.position.x`, -1e6, 1e6), y: number(p.y, `${path}.position.y`, -1e6, 1e6) },
        size: number(o.size, `${path}.size`, 0.1, 10_000),
        ...(o.colour !== undefined ? { fill: { colour: text(o.colour, `${path}.colour`, 9, COLOUR) } } : {}),
      }
    }
    case "set_fill": {
      const o = record(value, path, ["op", "refs", "fill"], ["refs", "fill"])
      return { op, refs: refs(o.refs, `${path}.refs`), fill: fill(o.fill, `${path}.fill`) }
    }
    case "set_stroke": {
      const o = record(value, path, ["op", "refs", "colour", "width"], ["refs"])
      if (o.colour === undefined && o.width === undefined) fail(path, "expected colour or width")
      return {
        op,
        refs: refs(o.refs, `${path}.refs`),
        ...(o.colour !== undefined
          ? { colour: o.colour === "none" ? "none" : text(o.colour, `${path}.colour`, 9, COLOUR) }
          : {}),
        ...(o.width !== undefined ? { width: number(o.width, `${path}.width`, 0, 1_000) } : {}),
      }
    }
    case "set_opacity": {
      const o = record(value, path, ["op", "refs", "opacity"], ["refs", "opacity"])
      return { op, refs: refs(o.refs, `${path}.refs`), opacity: number(o.opacity, `${path}.opacity`, 0, 1) }
    }
    case "move": {
      const o = record(value, path, ["op", "refs", "dx", "dy"], ["refs"])
      return {
        op,
        refs: refs(o.refs, `${path}.refs`),
        dx: number(o.dx ?? 0, `${path}.dx`, -1e6, 1e6),
        dy: number(o.dy ?? 0, `${path}.dy`, -1e6, 1e6),
      }
    }
    case "set_box": {
      const o = record(value, path, ["op", "ref", "box"], ["ref", "box"])
      return { op, ref: text(o.ref, `${path}.ref`, 40, REF), box: box(o.box, `${path}.box`) }
    }
    case "rotate": {
      const o = record(value, path, ["op", "refs", "degrees"], ["refs", "degrees"])
      return { op, refs: refs(o.refs, `${path}.refs`), degrees: number(o.degrees, `${path}.degrees`, -3600, 3600) }
    }
    case "align": {
      const o = record(value, path, ["op", "refs", "horizontal", "vertical", "to"], ["refs"])
      if (o.horizontal === undefined && o.vertical === undefined) fail(path, "expected horizontal or vertical")
      const to = oneOf(o.to ?? "selection", `${path}.to`, ["page", "selection"] as const)
      return {
        op,
        refs: refs(o.refs, `${path}.refs`, to === "selection" ? 2 : 1),
        to,
        ...(o.horizontal !== undefined
          ? { horizontal: oneOf(o.horizontal, `${path}.horizontal`, ["left", "center", "right"] as const) }
          : {}),
        ...(o.vertical !== undefined
          ? { vertical: oneOf(o.vertical, `${path}.vertical`, ["top", "middle", "bottom"] as const) }
          : {}),
      }
    }
    case "rename": {
      const o = record(value, path, ["op", "refs", "name"], ["refs", "name"])
      return { op, refs: refs(o.refs, `${path}.refs`), name: text(o.name, `${path}.name`, 200) }
    }
    case "set_visible": {
      const o = record(value, path, ["op", "refs", "visible"], ["refs", "visible"])
      if (typeof o.visible !== "boolean") fail(`${path}.visible`, "expected a boolean")
      return { op, refs: refs(o.refs, `${path}.refs`), visible: o.visible }
    }
    case "set_text": {
      const o = record(value, path, ["op", "ref", "text"], ["ref", "text"])
      return { op, ref: text(o.ref, `${path}.ref`, 40, REF), text: text(o.text, `${path}.text`, 10_000) }
    }
    case "boolean": {
      const o = record(value, path, ["op", "refs", "mode"], ["refs", "mode"])
      return {
        op,
        refs: refs(o.refs, `${path}.refs`, 2),
        mode: oneOf(o.mode, `${path}.mode`, ["union", "subtract", "intersect", "xor"] as const),
      }
    }
    case "arrange": {
      const o = record(value, path, ["op", "refs", "to", "target"], ["refs", "to"])
      const to = oneOf(o.to, `${path}.to`, ["front", "back", "above", "below"] as const)
      const list = refs(o.refs, `${path}.refs`)
      if (to === "above" || to === "below") {
        const target = text(o.target, `${path}.target`, 40, REF)
        if (list.includes(target)) fail(`${path}.target`, "must not be one of refs")
        return { op, refs: list, to, target }
      }
      if (o.target !== undefined) fail(`${path}.target`, "only with above or below")
      return { op, refs: list, to }
    }
    case "group":
    case "ungroup":
    case "delete":
    case "select": {
      const o = record(value, path, ["op", "refs"], ["refs"])
      return { op, refs: refs(o.refs, `${path}.refs`) }
    }
  }
}

export const OPERATIONS = [
  "create_shape",
  "create_text",
  "set_fill",
  "set_stroke",
  "set_opacity",
  "move",
  "set_box",
  "rotate",
  "align",
  "rename",
  "set_visible",
  "set_text",
  "group",
  "ungroup",
  "boolean",
  "arrange",
  "delete",
  "select",
] as const

export type ApplyInput = { plan: Json; commandId?: string }

export function validateApply(input: Json): ApplyInput {
  const document = input.document === undefined ? undefined : text(input.document, "document", 36, DOCUMENT)
  const ifRevision = input.if_revision === undefined ? undefined : text(input.if_revision, "if_revision", 24, REVISION)
  const commandId = input.command_id === undefined ? undefined : text(input.command_id, "command_id", 80, COMMAND_ID)
  if (!Array.isArray(input.operations) || input.operations.length === 0 || input.operations.length > MAX_OPERATIONS)
    fail("operations", `expected 1 to ${MAX_OPERATIONS} operations`)
  const operations = (input.operations as unknown[]).map(operation)
  let expect: Json[] = []
  if (input.expect !== undefined) {
    if (!Array.isArray(input.expect) || input.expect.length > MAX_OPERATIONS)
      fail("expect", "expected at most 50 clauses")
    expect = (input.expect as unknown[]).map((value, i) => {
      const c = record(value, `expect[${i}]`, ["ref", "exists", "name", "type"], ["ref"])
      if (c.exists !== undefined && typeof c.exists !== "boolean") fail(`expect[${i}].exists`, "expected a boolean")
      return {
        ref: text(c.ref, `expect[${i}].ref`, 40, REF),
        ...(c.exists !== undefined ? { exists: c.exists } : {}),
        ...(c.name !== undefined ? { name: text(c.name, `expect[${i}].name`, 200) } : {}),
        ...(c.type !== undefined ? { type: text(c.type, `expect[${i}].type`, 60, /^[A-Za-z]+$/) } : {}),
      }
    })
  }
  return {
    plan: {
      ...(document ? { document } : {}),
      ...(ifRevision ? { if_revision: ifRevision } : {}),
      operations,
      expect,
    },
    ...(commandId ? { commandId } : {}),
  }
}

// Remembers applied command IDs so a replayed call applies nothing and returns the first result.
export function createCommandLog(limit = 200) {
  const results = new Map<string, string>()
  return {
    get: (id: string) => results.get(id),
    set: (id: string, result: string) => {
      results.delete(id)
      results.set(id, result)
      while (results.size > limit) results.delete(results.keys().next().value as string)
    },
  }
}
