// Units without a host: the plugin's validation of edit batches against the add-on's published operations
// (host/ops.json), the fixed programs, render task verification and the working method.

import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { describe, it } from "node:test"

import { readPackage } from "../plugin/src/integration.ts"
import { METHOD_TEXT, methodAttacher, resetMethodForTests } from "../plugin/src/method.ts"
import { programCode } from "../plugin/src/programs.ts"
import { addTask, taskStatus } from "../plugin/src/tasks.ts"
import { MAX_OPERATIONS, OPERATIONS, validatePlan } from "../plugin/src/validate.ts"
import { PYTHON } from "./helpers/plugin.ts"

const HOST = join(import.meta.dirname, "..", "host")

describe("blender_apply follows OpenCode Link's operations (host/ops.json)", () => {
  type Contract = {
    version: string
    limits: { operations: number }
    ops: Record<string, { args: Record<string, { kind?: string; enum?: string[]; required?: boolean }> }>
  }
  const contract = JSON.parse(readFileSync(join(HOST, "ops.json"), "utf8")) as Contract

  it("ops.json and the README's operation table are generated from the add-on, for this version", () => {
    execFileSync(PYTHON, ["-I", "-B", join(HOST, "ops_json.py"), "--check"], { stdio: "pipe" })
    assert.equal(contract.version, readPackage().version)
  })

  it("has the same operations, fields, required fields, enums and batch limit as ops.json", () => {
    assert.equal(MAX_OPERATIONS, contract.limits.operations)
    assert.deepEqual(Object.keys(OPERATIONS).sort(), Object.keys(contract.ops).sort())
    for (const [op, spec] of Object.entries(contract.ops)) {
      const ours = OPERATIONS[op] as Record<string, { kind: unknown; required: boolean }>
      assert.deepEqual(Object.keys(ours).sort(), Object.keys(spec.args).sort(), `${op} fields`)
      for (const [field, arg] of Object.entries(spec.args)) {
        const mine = ours[field] as { kind: unknown; required: boolean }
        assert.equal(mine.required, arg.required === true, `${op}.${field} required`)
        if (arg.enum) assert.deepEqual(mine.kind, { enum: arg.enum }, `${op}.${field} enum`)
        // A field given null counts as absent (ops.json "null"): set_parent's parent accepts it explicitly.
        else assert.equal(mine.kind === "ref_or_null" ? "ref" : mine.kind, arg.kind, `${op}.${field} kind`)
      }
    }
  })

  it("checks the rules across fields before anything reaches Blender", () => {
    const ok = (operations: unknown[]) => validatePlan({ operations }).plan.operations
    const bad = (operations: unknown[], message: RegExp) => assert.throws(() => validatePlan({ operations }), message)
    assert.equal(
      ok([
        {
          op: "create_object",
          kind: "text",
          name: "Title",
          as: "t",
          body: "Hello",
          size: 0.5,
          extrude: 0.05,
          align_x: "CENTER",
        },
        { op: "duplicate", ref: "$t", as: "copy", suffix: "_b", offset: [0, 2, 0] },
        { op: "set_material", ref: "$copy", material: "Red", color: [0.8, 0.05, 0.05], roughness: 0.4 },
        { op: "scene_settings", frame_start: 1, frame_end: 48, fps: 23.976 },
        { op: "shift_keys", ref: "Cube", frames: [10, 20], offset: 5 },
        { op: "delete", refs: ["$copy"] },
      ]).length,
      6,
    )
    // parent null is sent omitted: omitting it clears the parent.
    assert.deepEqual(ok([{ op: "set_parent", ref: "Head", parent: null }]), [{ op: "set_parent", ref: "Head" }])
    bad([{ op: "fit_height", ref: "Pole", height: 4, scale_axes: "xy" }], /expected one of z, uniform/)
    bad([{ op: "create_object", kind: "text", name: "T" }], /a text needs body/)
    bad([{ op: "create_object", kind: "empty", name: "E", primitive: "cube" }], /primitive: applies to mesh only/)
    bad([{ op: "scene_settings" }], /give at least one field/)
    bad([{ op: "scene_settings", frame_start: 20, frame_end: 10 }], /frame_start after frame_end/)
    bad([{ op: "scene_settings", frame_current: 2.5 }], /whole frame/)
    bad([{ op: "shift_keys", ref: "Cube", offset: 0 }], /offset \(not 0\) or delete true/)
    bad([{ op: "shift_keys", ref: "Cube", offset: 3, delete: true }], /not both/)
    bad([{ op: "shift_keys", ref: "Cube", frames: [20, 10], delete: true }], /first at most last/)
    bad([{ op: "set_material", ref: "Cube", material: "M", color: [-1, 0, 0] }], /color\[0\]/)
    bad([{ op: "set_parent", ref: "Cube", bone: "Arm" }], /bone needs parent/)
    bad([{ op: "delete", refs: ["$nope"] }], /alias used before/)
    bad([{ op: "fit_height", ref: "Pole", height: -1 }], /height above 0/)
    assert.equal(ok([{ op: "rename", ref: "Cube", name: "é".repeat(127) }]).length, 1, "255 UTF-8 bytes")
    bad([{ op: "rename", ref: "Cube", name: "é".repeat(128) }], /invalid name/)
    bad(
      Array.from({ length: 51 }, () => ({ op: "set_camera", ref: "Cam" })),
      /expected 1 to 50 operations/,
    )
    assert.throws(
      () => validatePlan({ operations: [{ op: "set_camera", ref: "C" }], if_revision: -1 }),
      /integer from 0/,
    )
  })
})

describe("fixed programs and render tasks", () => {
  it("parameters travel as JSON data; each render gets its own work folder", () => {
    assert.match(programCode("verify", { frame: 30 }), /^import json\nPARAMS = json\.loads\("\{\\"frame\\":30\}"\)\n/)
    const render = programCode("render", { output: "x.png" })
    assert.match(render, /\nJOB = "/)
    assert.match(render, /uuid\.uuid4\(\)\.hex/)
    assert.match(render, /os\.makedirs\(work\)\n/, "an existing folder is an error, never reused")
  })

  it("never reports done when expected files are missing, nor for another task's output", () => {
    const dir = mkdtempSync(join(tmpdir(), "blender-verify-"))
    const frames = [1, 2, 3].map((frame) => join(dir, `shot_${String(frame).padStart(4, "0")}.png`))
    writeFileSync(frames[0] as string, "png")
    writeFileSync(frames[2] as string, "png")
    const log = join(dir, "frames.log")
    writeFileSync(log, 'OPENCODE_DONE {"bytes":6}\n')
    const video = join(dir, "final.mp4")
    const videoLog = join(dir, "video.log")
    writeFileSync(videoLog, `OPENCODE_DONE ${JSON.stringify({ output: join(dir, "still.png") })}\n`)
    writeFileSync(video, "mp4")
    const base = { pid: 2 ** 22 + 7, work: dir, start: 1, end: 3, scene: "Scene", source: "auto" }
    const partial = taskStatus(
      addTask({ ...base, log, kind: "frames", output: join(dir, "shot_####.png"), targets: frames, frames }),
    )
    assert.equal(partial.state, "failed")
    assert.match(String(partial.error), /1 of 3 expected file\(s\) are missing or empty: shot_0002\.png/)
    const crossed = taskStatus(
      addTask({ ...base, log: videoLog, kind: "video", output: video, targets: [video], frames: [] }),
    )
    assert.equal(crossed.state, "failed")
    assert.match(String(crossed.error), /reports another output/)
    writeFileSync(frames[1] as string, "png")
    assert.equal(
      taskStatus(addTask({ ...base, log, kind: "frames", output: join(dir, "shot_####.png"), targets: frames, frames }))
        .state,
      "done",
    )
  })
})

describe("the working method", () => {
  it("is short, joins a conversation once, and never fails a Blender call", async () => {
    assert.ok(METHOD_TEXT.split("\n").length <= 10 && METHOD_TEXT.length < 1_000)
    resetMethodForTests()
    let calls = 0
    const failing = methodAttacher(async () => {
      calls += 1
      throw new Error("no instructions here")
    })
    await failing("ses_a")
    await failing("ses_a")
    assert.equal(calls, 2, "tried again after a failure")
    const puts: string[] = []
    const working = methodAttacher(async (entry) => void puts.push(entry.sessionID))
    await working("ses_b")
    await working("ses_b")
    await methodAttacher(undefined)("ses_c")
    assert.deepEqual(puts, ["ses_b"])
  })
})
