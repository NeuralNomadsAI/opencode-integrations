// The add-on in a background Blender >= 5.3 (its own tests: host methods and Lab tools, the typed edit layer,
// the operations of 0.4.0 and the defects fixed in it), and the plugin's tools acting on that Blender's
// document. Skipped unless BLENDER_TEST_BIN names Blender.

import assert from "node:assert/strict"
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { describe, it } from "node:test"

import { requestHost } from "../plugin/src/kit.ts"
import { blenderBin, installAddon, installTestAddon, isolatedRoot, runBlender } from "./helpers/blender.ts"
import { announcement, loadPlugin } from "./helpers/plugin.ts"

const skip = blenderBin ? false : "BLENDER_TEST_BIN is not set"
const folder = join(import.meta.dirname, "blender")

type Result = { passed: string[]; failed: Array<{ name: string; error: string; trace: string }> }

// Runs one of the add-on's test scripts in a private background Blender; returns its RESULT line.
async function hostScript<T extends Result>(script: string, args: (scratch: string) => string[], timeout = 240_000) {
  const iso = isolatedRoot("blender-host-")
  try {
    installTestAddon(iso)
    const scratch = join(iso.root, "scratch")
    mkdirSync(scratch)
    const run = runBlender(iso, join(folder, script), args(scratch), {}, { timeout })
    await run.exited
    const lines = run.output().split("\n")
    for (const line of lines.filter((entry) => entry.startsWith("MEASURE "))) console.log(line)
    const line = lines.find((entry) => entry.startsWith("RESULT "))
    assert.ok(line, run.output().slice(-3000))
    const result = JSON.parse(line.slice("RESULT ".length)) as T
    assert.deepEqual(result.failed, [], JSON.stringify(result.failed, null, 1))
    return result
  } finally {
    iso.cleanup()
  }
}

describe("OpenCode Link in Blender (background)", { skip }, () => {
  it("passes the host tests and runs every bundled Lab tool", async () => {
    // Its renders and imports take minutes on a runner without a GPU.
    const result = await hostScript<Result & { coverage: Record<string, string>; lab: Record<string, string> }>(
      "host_tests.py",
      (scratch) => [scratch],
      600_000,
    )
    assert.equal(result.passed.length, 26)
    assert.equal(Object.keys(result.coverage).length, 87)
    // Every bundled Lab tool ran by name; those needing a window refused background mode cleanly.
    const labTools = JSON.parse(
      readFileSync(join(import.meta.dirname, "..", "host", "opencode_link", "lab", "tools.json"), "utf8"),
    ).tools as Array<{ name: string; interface: boolean }>
    assert.deepEqual(Object.keys(result.lab).sort(), labTools.map((tool) => tool.name).sort())
    for (const tool of labTools)
      assert.equal(result.lab[tool.name], tool.interface ? "refused in background (needs a window)" : "ran")
  })

  it("passes the typed layer's tests: one undo step, before and after, a failure restored field by field", async () => {
    assert.equal((await hostScript("thin_tests.py", () => [folder])).passed.length, 10)
  })

  it("passes the tests of the 0.4.0 operations and of the 0.3.9 fixes", async () => {
    const ops = join(import.meta.dirname, "..", "host", "ops.json")
    assert.equal((await hostScript("ops040_tests.py", (scratch) => [scratch, ops])).passed.length, 9)
    assert.equal((await hostScript("defects_tests.py", (scratch) => [scratch])).passed.length, 6)
  })

  it("the plugin's tools act on the attached Blender document", async () => {
    const iso = isolatedRoot("blender-tools-")
    const stopFile = join(iso.root, "stop")
    installAddon(iso)
    const run = runBlender(iso, join(folder, "serve.py"), [
      "--listen-only",
      "--seconds",
      "180",
      "--stop-file",
      stopFile,
    ])
    const plugin = await loadPlugin()
    try {
      let line: string | undefined
      for (let attempt = 0; attempt < 600 && !line; attempt += 1) {
        line = run
          .output()
          .split("\n")
          .find((entry) => entry.startsWith("ENDPOINT "))
        if (!line) await new Promise((resolve) => setTimeout(resolve, 100))
      }
      assert.ok(line, run.output().slice(-2000))
      const { port, token, script_handle } = JSON.parse(line.slice("ENDPOINT ".length))
      const hello = (await requestHost({ host: "127.0.0.1", port, token }, "hello", {})) as {
        capabilities: { methods: string[] }
      }
      for (const method of ["scene", "apply", "execute_code", "lab_tool"])
        assert.ok(hello.capabilities.methods.includes(method), `hello lists ${method}`)
      assert.deepEqual(await plugin.rpc("advertise", announcement({ port, token }, script_handle)), {
        outcome: "accepted",
      })
      const json = (result: { content?: unknown }) => JSON.parse(String(result.content))
      // The only offered Blender attaches itself at the first call.
      const scene = json(await plugin.tool("blender_context"))
      assert.equal(typeof scene.revision, "number")
      assert.ok(plugin.registry.binding("ses_test"))
      const applied = json(
        await plugin.tool("blender_apply", {
          if_revision: scene.revision,
          command_id: "e2e-1",
          summary: "street",
          operations: [
            { op: "create_object", kind: "empty", name: "Lamp", as: "lamp", location: [-6, -4, 0] },
            {
              op: "create_object",
              kind: "mesh",
              primitive: "cylinder",
              name: "Pole",
              as: "pole",
              parent: "$lamp",
              location: [-6, -4, 1.5],
            },
            { op: "set_material", ref: "$pole", material: "Metal", color: [0.6, 0.6, 0.62], metallic: 1 },
            { op: "duplicate", ref: "$lamp", suffix: "_2", offset: [4, 0, 0], linked: true },
            { op: "create_object", kind: "text", name: "Title", body: "HIGH NOON", size: 1.5, extrude: 0.1 },
            { op: "scene_settings", frame_start: 1, frame_end: 60, fps: 24 },
          ],
        }),
      )
      assert.equal(applied.status, "applied", JSON.stringify(applied).slice(0, 2000))
      assert.deepEqual(
        applied.created.map((entry: { name: string }) => entry.name),
        ["Lamp", "Pole", "Metal", "Lamp_2", "Pole_2", "Title"],
      )
      const after = json(await plugin.tool("blender_context", { match: "Pole*" }))
      assert.equal(after.scene.frames[1], 60)
      assert.deepEqual(
        after.objects.map((object: { name: string }) => object.name),
        ["Pole", "Pole_2"],
      )
      const inspected = json(await plugin.tool("blender_inspect", { type: "TextCurve", match: "align*" }))
      assert.match(JSON.stringify(inspected), /align_x/)
      const saved = join(iso.root, "street.blend")
      json(await plugin.tool("blender_save", { path: saved, copy: true }))
      assert.ok(existsSync(saved))
      assert.deepEqual(plugin.permissions, [], "attaching the only Blender and the fixed programs ask nothing")
    } finally {
      writeFileSync(stopFile, "")
      await run.exited
      await plugin.cleanup()
      iso.cleanup()
    }
  })
})
