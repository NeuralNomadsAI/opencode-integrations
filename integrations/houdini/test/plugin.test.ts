// OpenCode for Houdini 0.3.0 against a fake installed part: the conversation attaches itself to the only free
// Houdini, the working method joins it, code runs only with permission, and a Houdini that crashes, closes or
// stays busy is said as such.

import assert from "node:assert/strict"
import { mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { describe, it } from "node:test"

import { startFakeHost } from "../../../packages/testing/src/index.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { METHOD_KEY } from "../plugin/src/method.ts"
import { announcement, content, loadPlugin } from "./helpers.ts"

const version = readPackage().version
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function capabilities(methods: string[]) {
  return {
    methods: [...new Set(["hello", "context", "apply", "status", "release", ...methods])],
    operations: [],
    undo: "grouped_best_effort" as const,
    background: false,
  }
}

async function offered(
  methods: Record<string, (params: Record<string, unknown>) => unknown> = {},
  process?: { pid: number; temp_dir: string },
) {
  const host = await startFakeHost({
    hostVersion: version,
    appVersion: "22.0.429",
    capabilities: capabilities(Object.keys(methods)),
    methods: { context: () => ({ revision: 3, entities: [] }), ...methods },
    ...(process
      ? { helloOverride: (hello: Record<string, unknown>) => ({ ...hello, process, document: "shot.hipnc" }) }
      : {}),
  })
  return host
}

describe("OpenCode for Houdini 0.3.0", () => {
  it("attaches the only free Houdini at the first call, with the working method; listing asks no permission", async () => {
    const plugin = await loadPlugin()
    const host = await offered()
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      const listed = content(await plugin.tool("houdini_hosts"))
      assert.equal(listed.houdinis.length, 1)
      assert.equal(listed.attached_to, null)
      assert.deepEqual(plugin.permissions, [])
      const read = content(await plugin.tool("houdini_context"))
      assert.equal(read.revision, 3)
      assert.equal(read.attached_to.label, "Houdini: test.hipnc")
      assert.deepEqual(plugin.methods, [{ sessionID: "ses_test", key: METHOD_KEY }])
      // Another conversation does not take the Houdini this one uses: it is told who holds it.
      await assert.rejects(plugin.tool("houdini_context", {}, "ses_other"), /attached to another conversation/)
      const shared = content(await plugin.tool("houdini_hosts", { attach: listed.houdinis[0].offerID }, "ses_other"))
      assert.match(shared.shared, /Another conversation is attached to this Houdini too/)
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  it("says how to offer a Houdini when none is", async () => {
    const plugin = await loadPlugin()
    try {
      await assert.rejects(
        plugin.tool("houdini_context"),
        /No Houdini is offered: start Houdini 21 or later with OpenCode Link installed/,
      )
    } finally {
      await plugin.cleanup()
    }
  })

  it("execute_code asks permission for each run, reads a script file as is, and keeps the host's hint", async () => {
    const answers: string[] = []
    const plugin = await loadPlugin({ answer: (action) => void answers.push(action) })
    const dir = mkdtempSync(join(tmpdir(), "houdini-script-"))
    const runs: Array<Record<string, unknown>> = []
    const host = await offered({
      execute_code: (params) => {
        runs.push(params)
        return { status: "executed", result: { ok: 1 }, changed: true, undo_step: "OpenCode: code" }
      },
    })
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      const out = content(await plugin.tool("houdini_execute_code", { code: "result = {'ok': 1}" }))
      assert.deepEqual([out.status, out.undo_step], ["executed", "OpenCode: code"])
      assert.deepEqual(answers, ["houdini_execute_code"])
      const file = join(dir, "build.py")
      writeFileSync(file, "﻿geo = hou.node('/obj').createNode('geo')\nprint(\"é\")\n")
      await plugin.tool("houdini_execute_code", { path: file })
      assert.equal(runs[1]?.code, "geo = hou.node('/obj').createNode('geo')\nprint(\"é\")\n")
      assert.equal(runs[1]?.filename, file)
      assert.equal(runs[1]?.session_id, "ses_test")
      assert.deepEqual(answers, ["houdini_execute_code", "houdini_execute_code"])
    } finally {
      await plugin.cleanup()
      await host.close()
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it("a refusal reads the hint for its code with Houdini's own text and data", async () => {
    const plugin = await loadPlugin()
    const host = await offered({
      release: () => {
        throw {
          code: "stale_revision",
          message: "Read the scene again (houdini_context) and release with its revision.",
          data: { current_revision: 7, reason: "revision_conflict" },
        }
      },
    })
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      await assert.rejects(
        plugin.tool("houdini_context", { release_quarantine: 2 }),
        /^Error: The scene changed since your last read: call houdini_context and use its revision\. Read the scene again \(houdini_context\) and release with its revision\. \{"current_revision":7,"reason":"revision_conflict"\}$/,
      )
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  it("a Houdini that crashes during a call is said within 2 s with its crash report (0.3.0)", async () => {
    const temp = mkdtempSync(join(tmpdir(), "houdini_temp-"))
    const plugin = await loadPlugin()
    // The crashed Houdini's connection stays open: the call would wait its whole timeout.
    const host = await offered({ execute_code: () => new Promise(() => {}) }, { pid: process.pid, temp_dir: temp })
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      // Another Houdini's report in the same folder is not this one's.
      writeFileSync(join(temp, "crash.other.Admin_1_log.txt"), "")
      setTimeout(() => writeFileSync(join(temp, `crash.shot.Admin_${process.pid}_log.txt`), "Houdini crashed"), 300)
      const started = Date.now()
      await assert.rejects(
        plugin.tool("houdini_execute_code", { code: "hou.node('/obj').cook()", timeout_s: 60 }),
        /^Error: Houdini crashed \(Houdini: test\.hipnc\): crash report .*crash\.shot\.Admin_\d+_log\.txt\. Its process \d+ is still open/,
      )
      assert.ok(Date.now() - started < 4_000, `said after ${Date.now() - started} ms`)
      // Houdini 22 also saves the scene at the crash (crash.<file>.<user>_<pid>.hipnc): the way to recover it.
      writeFileSync(join(temp, `crash.shot.Admin_${process.pid}.hipnc`), "scene")
      rmSync(join(temp, `crash.shot.Admin_${process.pid}_log.txt`))
      await assert.rejects(
        plugin.tool("houdini_execute_code", { code: "hou.node('/obj').cook()", timeout_s: 60 }),
        /crash report .*crash\.shot\.Admin_\d+\.hipnc\. Its process \d+ is still open .* Houdini saved the scene as it was at the crash in that file/,
      )
    } finally {
      await plugin.cleanup()
      await host.close()
      rmSync(temp, { recursive: true, force: true })
    }
  })

  it("a call that outlasts its wait says Houdini still runs it (status off the main thread)", async () => {
    const temp = mkdtempSync(join(tmpdir(), "houdini_temp-"))
    const plugin = await loadPlugin()
    const host = await offered(
      {
        execute_code: () => sleep(5_000).then(() => ({ status: "executed" })),
        status: () => ({ busy: true, running: "execute_code", seconds: 1.2, queued: 0 }),
      },
      { pid: process.pid, temp_dir: temp },
    )
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      await assert.rejects(
        plugin.tool("houdini_execute_code", { code: "import time; time.sleep(5)", timeout_s: 1 }),
        /Houdini is still running execute_code \(1\.2 s so far\): it goes on in Houdini, nothing is lost/,
      )
    } finally {
      await plugin.cleanup()
      await host.close()
      rmSync(temp, { recursive: true, force: true })
    }
  })
})
