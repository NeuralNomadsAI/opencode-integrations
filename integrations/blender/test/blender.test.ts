// Host tests in a background Blender 5.2 (AC-B1 to AC-B6, AC-B12, AC-B15 host side) and the plugin's
// tools acting on that Blender's document (AC-B8). Skipped unless BLENDER_TEST_BIN names Blender.

import assert from "node:assert/strict"
import { mkdirSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { describe, it } from "node:test"

import { runAudit } from "../plugin/src/audit.ts"
import { blenderBin, installAddon, isolatedRoot, runBlender } from "./helpers/blender.ts"
import { announcement, loadPlugin } from "./helpers/plugin.ts"

const skip = blenderBin ? false : "BLENDER_TEST_BIN is not set"

type HostResult = {
  passed: string[]
  failed: Array<{ name: string; error: string; trace: string }>
  coverage: Record<string, string>
}

describe("OpenCode Link in Blender (background)", { skip }, () => {
  it("passes the host tests and exercises every read and operation", async () => {
    const iso = isolatedRoot("blender-host-")
    try {
      installAddon(iso)
      const scratch = join(iso.root, "scratch")
      mkdirSync(scratch)
      const run = runBlender(iso, join(import.meta.dirname, "blender", "host_tests.py"), [scratch])
      await run.exited
      const line = run
        .output()
        .split("\n")
        .find((entry) => entry.startsWith("RESULT "))
      assert.ok(line, run.output().slice(-3000))
      const result = JSON.parse(line.slice("RESULT ".length)) as HostResult
      assert.deepEqual(result.failed, [])
      assert.equal(result.passed.length, 19)
      assert.equal(Object.keys(result.coverage).length, 79)
    } finally {
      iso.cleanup()
    }
  })

  it("the plugin's tools act on the attached Blender document", async () => {
    const iso = isolatedRoot("blender-tools-")
    const stopFile = join(iso.root, "stop")
    installAddon(iso)
    const run = runBlender(iso, join(import.meta.dirname, "blender", "serve.py"), [
      "--listen-only",
      "--seconds",
      "120",
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
      assert.deepEqual(await plugin.rpc("advertise", announcement({ port, token }, script_handle)), {
        outcome: "accepted",
      })
      const offerID = plugin.registry.hosts().offers[0]?.offerID as string
      await plugin.tool("blender_attach", { offerID })

      const read = JSON.parse(
        String(
          (await plugin.tool("blender_context", { reads: [{ name: "scene.get_object_info", args: { name: "Cube" } }] }))
            .content,
        ),
      )
      assert.equal(read.script_handle, script_handle)
      assert.equal(read.results[0].result.name, "Cube")
      const applied = JSON.parse(
        String(
          (
            await plugin.tool("blender_apply", {
              script_handle,
              if_revision: read.revision,
              command_id: "tools-1",
              summary: "add a light",
              operations: [{ op: "scene.create_object", args: { primitive: "LIGHT", name: "Key" } }],
              expect: [{ ref: "object:Key", predicate: "absent" }],
            })
          ).content,
        ),
      )
      assert.equal(applied.status, "applied")
      assert.equal(applied.undo_step, "OpenCode: add a light")
      const after = JSON.parse(
        String(
          (await plugin.tool("blender_context", { reads: [{ name: "scene.get_object_info", args: { name: "Key" } }] }))
            .content,
        ),
      )
      assert.equal(after.results[0].result.type, "LIGHT")
      assert.equal(after.revision, applied.revision)
      await assert.rejects(
        plugin.tool("blender_apply", {
          script_handle,
          if_revision: read.revision,
          command_id: "tools-2",
          operations: [{ op: "scene.delete_object", args: { name: "Key" } }],
        }),
        /changed since your last read/,
      )
    } finally {
      writeFileSync(stopFile, "")
      await run.exited
      await plugin.cleanup()
      iso.cleanup()
    }
  })

  it("blender_audit summarizes a .blend file with a background Blender", async () => {
    const iso = isolatedRoot("blender-audit-")
    try {
      const file = join(iso.root, "audit.blend")
      const script = join(iso.root, "save.py")
      writeFileSync(script, `import bpy\nbpy.ops.wm.save_as_mainfile(filepath=${JSON.stringify(file)})\n`)
      await runBlender(iso, script).exited
      const summary = await runAudit("datablocks", file, { blender: blenderBin as string, env: iso.env })
      assert.equal((summary.counts as Record<string, number>).scenes, 1)
      const missing = await runAudit("missing_files", file, { blender: blenderBin as string, env: iso.env })
      assert.equal(missing.count, 0)
      const libraries = await runAudit("linked_libraries", file, { blender: blenderBin as string, env: iso.env })
      assert.equal(libraries.count, 0)
    } finally {
      iso.cleanup()
    }
  })
})
