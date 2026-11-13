// Validation of blender_apply batches before they reach Blender: known operations, closed fields, finite bounded
// numbers, bounded names, aliases bound before use. They are OpenCode Link's edit operations (host/ops.json:
// fields, kinds, limits and rules; a test keeps this table equal to it); the add-on checks them again and resolves
// names and expect clauses against the document before changing anything.
export const MAX_OPERATIONS = 50
type Json = Record<string, unknown>

class PlanError extends Error {}
const fail = (path: string, message: string): never => {
  throw new PlanError(`${path}: ${message}`)
}

const MAX_FRAME = 1_048_574
const ALIAS = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/
const COMMAND_ID = /^[A-Za-z0-9._:-]{1,80}$/

type Kind =
  | "ref"
  | "ref_or_null"
  | "name"
  | "alias"
  | "text"
  | "number"
  | "positive"
  | "nonnegative"
  | "index"
  | "frame"
  | "whole_frame"
  | "frames"
  | "bool"
  | "vec3"
  | "rgb"
  | "scale"
  | "rotation"
  | "target"
  | "refs"
  | "names"
  | "data_path"
  | "value"
  | "paths"
  | { enum: readonly string[] }

const F = (kind: Kind, required = false) => ({ kind, required })
// Whose animation an RNA path is keyed on (ops.json).
const OWNERS = { enum: ["object", "data", "shape_keys"] } as const
const ROT = F("rotation")
export const OPERATIONS: Record<string, Record<string, { kind: Kind; required: boolean }>> = {
  set_transform: { ref: F("ref", true), location: F("vec3"), rotation: ROT, scale: F("scale"), delta: F("bool") },
  fit_height: {
    ref: F("ref", true),
    height: F("number"),
    ground: F("bool"),
    ground_z: F("number"),
    children: F("bool"),
    scale_axes: F({ enum: ["z", "uniform"] }),
  },
  pose_bone: { ref: F("ref", true), bone: F("name", true), location: F("vec3"), rotation: ROT, scale: F("scale") },
  aim_bone: {
    ref: F("ref", true),
    bone: F("name", true),
    target: F("target", true),
    axis: F({ enum: ["Y", "-Y", "X", "-X", "Z", "-Z"] }),
    tolerance_deg: F("number"),
  },
  insert_key: {
    ref: F("ref", true),
    bone: F("name"),
    paths: F("paths"),
    path: F("data_path"),
    index: F("index"),
    owner: F(OWNERS),
    value: F("value"),
    frame: F("frame", true),
    interpolation: F({ enum: ["CONSTANT", "LINEAR", "BEZIER"] }),
  },
  delete_keys: {
    ref: F("ref", true),
    bone: F("name"),
    paths: F("paths"),
    path: F("data_path"),
    index: F("index"),
    owner: F(OWNERS),
    frames: F("frames"),
  },
  create_object: {
    kind: F({ enum: ["empty", "camera", "light", "mesh", "text"] }, true),
    name: F("name", true),
    as: F("alias"),
    primitive: F({ enum: ["cube", "plane", "uv_sphere", "cylinder", "cone"] }),
    size: F("positive"),
    depth: F("positive"),
    light_type: F({ enum: ["POINT", "SUN", "SPOT", "AREA"] }),
    energy: F("number"),
    lens: F("number"),
    body: F("text"),
    extrude: F("nonnegative"),
    align_x: F({ enum: ["LEFT", "CENTER", "RIGHT", "JUSTIFY", "FLUSH"] }),
    align_y: F({ enum: ["TOP", "TOP_BASELINE", "CENTER", "BOTTOM_BASELINE", "BOTTOM"] }),
    location: F("vec3"),
    rotation: ROT,
    collection: F("name"),
    parent: F("ref"),
  },
  rename: { ref: F("ref", true), name: F("name", true) },
  delete: { refs: F("refs"), collections: F("names") },
  duplicate: {
    ref: F("ref", true),
    as: F("alias"),
    name: F("name"),
    suffix: F("name"),
    offset: F("vec3"),
    linked: F("bool"),
    children: F("bool"),
  },
  set_material: {
    ref: F("ref", true),
    material: F("name", true),
    color: F("rgb"),
    roughness: F("nonnegative"),
    metallic: F("nonnegative"),
    slot: F("index"),
  },
  scene_settings: {
    frame_start: F("whole_frame"),
    frame_end: F("whole_frame"),
    frame_current: F("whole_frame"),
    fps: F("positive"),
  },
  shift_keys: {
    ref: F("ref", true),
    bone: F("name"),
    paths: F("paths"),
    frames: F("frames"),
    offset: F("number"),
    delete: F("bool"),
  },
  link: { ref: F("ref", true), collection: F("name", true) },
  unlink: { ref: F("ref", true), collection: F("name", true) },
  create_collection: { name: F("name", true), parent: F("name") },
  set_parent: { ref: F("ref", true), parent: F("ref_or_null"), bone: F("name"), keep_transform: F("bool") },
  add_marker: { name: F("name", true), frame: F("frame", true), camera: F("ref") },
  set_camera: { ref: F("ref", true) },
  add_constraint: {
    ref: F("ref", true),
    type: F(
      { enum: ["TRACK_TO", "DAMPED_TRACK", "COPY_LOCATION", "COPY_ROTATION", "COPY_TRANSFORMS", "CHILD_OF"] },
      true,
    ),
    target: F("ref", true),
    subtarget: F("name"),
    name: F("name"),
    influence: F("nonnegative"),
  },
  switch_parent: { ref: F("ref", true), parent: F("ref"), bone: F("name"), frame: F("whole_frame", true) },
}
// create_object's fields that apply to one kind only (ops.json rules).
const KIND_FIELDS: Record<string, string[]> = {
  primitive: ["mesh"],
  size: ["mesh", "text"],
  depth: ["mesh"],
  light_type: ["light"],
  energy: ["light"],
  lens: ["camera"],
  body: ["text"],
  extrude: ["text"],
  align_x: ["text"],
  align_y: ["text"],
}

function num(v: unknown, path: string, min = -Number.MAX_VALUE, max = Number.MAX_VALUE) {
  if (typeof v !== "number" || !Number.isFinite(v) || v < min || v > max)
    fail(path, min === -Number.MAX_VALUE ? "expected a finite number" : `expected a number from ${min} to ${max}`)
  return v as number
}
function vec(v: unknown, path: string, n: number, min?: number) {
  if (!Array.isArray(v) || v.length !== n) fail(path, `expected ${n} numbers`)
  return (v as unknown[]).map((x, i) => num(x, `${path}[${i}]`, min))
}
// A name: 1 to 255 UTF-8 bytes, no NUL (Blender's limit, as ops.json states it).
function str(v: unknown, path: string, pattern?: RegExp) {
  if (
    typeof v !== "string" ||
    !v ||
    v.includes("\0") ||
    (pattern ? !pattern.test(v) : Buffer.byteLength(v, "utf8") > 255)
  )
    fail(path, "invalid name")
  return v as string
}
function ref(v: unknown, path: string) {
  const s = str(v, path)
  if (s.startsWith("$") && !ALIAS.test(s.slice(1))) fail(path, "invalid alias")
  return s
}
function record(v: unknown, path: string, keys: string[]): Json {
  if (typeof v !== "object" || v === null || Array.isArray(v)) fail(path, "expected an object")
  for (const k of Object.keys(v as Json)) if (!keys.includes(k)) fail(path, `unknown field ${k}`)
  return v as Json
}

function value(kind: Kind, v: unknown, path: string): unknown {
  if (typeof kind === "object") {
    if (!kind.enum.includes(v as string)) fail(path, `expected one of ${kind.enum.join(", ")}`)
    return v
  }
  switch (kind) {
    case "ref":
      return ref(v, path)
    case "ref_or_null":
      // null clears the parent, as omitting it does (sent omitted: the add-on's parent is a ref)
      return v === null ? undefined : ref(v, path)
    case "name":
      return str(v, path)
    case "alias":
      return str(v, path, ALIAS)
    case "text":
      if (typeof v !== "string" || !v || v.includes("\0")) fail(path, "expected a non-empty text")
      return v
    case "number":
      return num(v, path)
    case "positive":
      if (num(v, path) <= 0) fail(path, "expected a number above 0")
      return v
    case "nonnegative":
      return num(v, path, 0)
    case "index":
      if (!Number.isInteger(v) || (v as number) < 0) fail(path, "expected an integer from 0")
      return v
    case "frame":
      return num(v, path, -MAX_FRAME, MAX_FRAME)
    case "whole_frame":
      if (!Number.isInteger(num(v, path, -MAX_FRAME, MAX_FRAME))) fail(path, "expected a whole frame")
      return v
    case "frames": {
      const [a, b] = vec(v, path, 2).map((x, i) => num(x, `${path}[${i}]`, -MAX_FRAME, MAX_FRAME)) as [number, number]
      if (a > b) fail(path, "expected [first, last] with first at most last")
      return [a, b]
    }
    case "rgb":
      return vec(v, path, 3, 0)
    case "bool":
      if (typeof v !== "boolean") fail(path, "expected a boolean")
      return v
    case "vec3":
      return vec(v, path, 3)
    case "scale":
      return typeof v === "number" ? num(v, path) : vec(v, path, 3)
    case "rotation": {
      const r = record(v, path, ["euler_deg", "order", "quaternion"])
      if ((r.euler_deg === undefined) === (r.quaternion === undefined)) fail(path, "expected euler_deg or quaternion")
      if (r.quaternion !== undefined) return { quaternion: vec(r.quaternion, `${path}.quaternion`, 4) }
      const order = r.order ?? "XYZ"
      if (!["XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"].includes(order as string)) fail(`${path}.order`, "invalid order")
      return { euler_deg: vec(r.euler_deg, `${path}.euler_deg`, 3), order }
    }
    case "target": {
      const t = record(v, path, ["point", "object", "bone", "direction"])
      const n = ["point", "object", "direction"].filter((k) => t[k] !== undefined).length
      if (n !== 1) fail(path, "expected exactly one of point, object, direction")
      if (t.point !== undefined) return { point: vec(t.point, `${path}.point`, 3) }
      if (t.direction !== undefined) return { direction: vec(t.direction, `${path}.direction`, 3) }
      return {
        object: ref(t.object, `${path}.object`),
        ...(t.bone !== undefined ? { bone: str(t.bone, `${path}.bone`) } : {}),
      }
    }
    case "refs": {
      if (!Array.isArray(v) || v.length < 1) fail(path, "expected at least one ref")
      const list = (v as unknown[]).map((x, i) => ref(x, `${path}[${i}]`))
      if (new Set(list).size !== list.length) fail(path, "duplicate ref")
      return list
    }
    case "names": {
      if (!Array.isArray(v) || v.length < 1) fail(path, "expected at least one name")
      const list = (v as unknown[]).map((x, i) => str(x, `${path}[${i}]`))
      if (new Set(list).size !== list.length) fail(path, "duplicate name")
      return list
    }
    case "data_path":
      if (typeof v !== "string" || !v || v.includes("\0") || v.length > 1024 || v.startsWith("."))
        fail(path, "expected an RNA path of 1 to 1024 characters")
      return v
    case "value":
      if (typeof v === "boolean") return v
      if (Array.isArray(v)) {
        if (v.length < 1 || v.length > 32) fail(path, "expected 1 to 32 numbers")
        return v.map((x, i) => num(x, `${path}[${i}]`))
      }
      return num(v, path)
    case "paths": {
      if (!Array.isArray(v) || v.length < 1) fail(path, "expected location, rotation and/or scale")
      for (const p of v as unknown[])
        if (!["location", "rotation", "scale"].includes(p as string))
          fail(path, "expected location, rotation and/or scale")
      return [...new Set(v as string[])]
    }
  }
}

// ops.json's rules across fields (the add-on checks them again).
function rules(op: string, o: Json, path: string) {
  const has = (...keys: string[]) => keys.some((k) => o[k] !== undefined)
  if ((op === "set_transform" || op === "pose_bone") && !has("location", "rotation", "scale"))
    fail(path, "give location, rotation or scale")
  if (op === "fit_height") {
    if (o.height === undefined && o.ground === false) fail(path, "give height or ground true")
    if (o.height !== undefined && (o.height as number) <= 0) fail(`${path}.height`, "height above 0")
  }
  if (op === "create_object") {
    for (const [field, kinds] of Object.entries(KIND_FIELDS))
      if (o[field] !== undefined && !kinds.includes(o.kind as string))
        fail(`${path}.${field}`, `applies to ${kinds.join(" or ")} only`)
    if (o.kind === "text" && o.body === undefined) fail(path, "a text needs body")
    if (o.depth !== undefined && o.primitive !== "cylinder" && o.primitive !== "cone")
      fail(`${path}.depth`, "applies to a cylinder or cone")
  }
  if (op === "scene_settings") {
    if (!has("frame_start", "frame_end", "frame_current", "fps")) fail(path, "give at least one field")
    if (o.frame_start !== undefined && o.frame_end !== undefined && (o.frame_start as number) > (o.frame_end as number))
      fail(path, "frame_start after frame_end")
  }
  if (op === "shift_keys") {
    const shifting = o.offset !== undefined && o.offset !== 0
    if (shifting === (o.delete === true) || (o.offset !== undefined && o.delete === true))
      fail(path, "give offset (not 0) or delete true, not both")
  }
  if (op === "set_parent" && o.bone !== undefined && o.parent === undefined) fail(path, "bone needs parent")
  if (op === "delete" && !has("refs", "collections")) fail(path, "give refs or collections")
  if (op === "insert_key" || op === "delete_keys") {
    if (has("paths") === has("path")) fail(path, "give paths or path (one of them)")
    for (const key of op === "insert_key" ? ["value", "index"] : ["index"])
      if (o[key] !== undefined && o.path === undefined) fail(`${path}.${key}`, "goes with path")
    if (o.owner !== undefined && o.owner !== "object" && (o.path === undefined || o.bone !== undefined))
      fail(`${path}.owner`, "goes with path, without bone")
  }
  if (op === "add_constraint" && (o.influence as number | undefined) !== undefined && (o.influence as number) > 1)
    fail(`${path}.influence`, "expected 0 to 1")
  if (op === "switch_parent" && o.bone !== undefined && o.parent === undefined) fail(path, "bone needs parent")
}

export function validatePlan(input: Json) {
  const ops = input.operations
  if (!Array.isArray(ops) || ops.length < 1 || ops.length > MAX_OPERATIONS)
    fail("operations", `expected 1 to ${MAX_OPERATIONS} operations`)
  const aliases = new Set<string>()
  const operations = (ops as unknown[]).map((raw, i) => {
    const path = `operations[${i}]`
    const op = (raw as Json | null)?.op
    const spec = typeof op === "string" ? OPERATIONS[op] : undefined
    if (!spec) fail(`${path}.op`, `expected one of ${Object.keys(OPERATIONS).join(", ")}`)
    const o = record(raw, path, ["op", ...Object.keys(spec as object)])
    const out: Json = { op }
    for (const [key, field] of Object.entries(spec as object) as [string, { kind: Kind; required: boolean }][]) {
      if (o[key] === undefined) {
        if (field.required) fail(path, `missing ${key}`)
        continue
      }
      const checked = value(field.kind, o[key], `${path}.${key}`)
      if (checked !== undefined) out[key] = checked
    }
    const refs: [string, unknown][] = [
      ...["ref", "parent", "camera", "target"].map((k) => [`${path}.${k}`, out[k]] as [string, unknown]),
      [`${path}.target.object`, (out.target as Json | undefined)?.object],
      ...((out.refs as string[] | undefined) ?? []).map((r, k) => [`${path}.refs[${k}]`, r] as [string, unknown]),
    ]
    for (const [at, r] of refs)
      if (typeof r === "string" && r.startsWith("$") && !aliases.has(r.slice(1)))
        fail(at, "alias used before the create_object or duplicate that names it")
    if ((op === "create_object" || op === "duplicate") && out.as) {
      if (aliases.has(out.as as string)) fail(`${path}.as`, "duplicate alias")
      aliases.add(out.as as string)
    }
    rules(op as string, out, path)
    return out
  })
  const expect = input.expect === undefined ? [] : input.expect
  if (!Array.isArray(expect) || expect.length > MAX_OPERATIONS) fail("expect", "expected an array")
  const clauses = (expect as unknown[]).map((c, i) => {
    const r = record(c, `expect[${i}]`, ["ref", "exists", "type", "bone"])
    return {
      ref: str(r.ref, `expect[${i}].ref`),
      ...(r.exists !== undefined ? { exists: value("bool", r.exists, `expect[${i}].exists`) } : {}),
      ...(r.type !== undefined ? { type: str(r.type, `expect[${i}].type`, /^[A-Z_]{2,20}$/) } : {}),
      ...(r.bone !== undefined ? { bone: str(r.bone, `expect[${i}].bone`) } : {}),
    }
  })
  // OpenCode Link's revision: an integer from 0.
  if (input.if_revision !== undefined && !(Number.isInteger(input.if_revision) && (input.if_revision as number) >= 0))
    fail("if_revision", "expected an integer from 0")
  const ifRevision = input.if_revision as number | undefined
  const commandId = input.command_id === undefined ? undefined : str(input.command_id, "command_id", COMMAND_ID)
  const summary = input.summary === undefined ? undefined : str(input.summary, "summary", /^[^\0]{1,60}$/)
  return {
    plan: {
      operations,
      expect: clauses,
      ...(ifRevision !== undefined ? { if_revision: ifRevision } : {}),
      ...(summary ? { summary } : {}),
    },
    commandId,
  }
}

export { PlanError }
