// OpenCode for Gaffer 0.3.0 against a fake installed part: the tools are the manifests', the conversation attaches
// itself to the only free script, the working method joins it, code and changes run only with permission, an
// earlier installation is said, and OpenCode Link installs into Gaffer's startup folder.

import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { describe, it } from "node:test"

import { readStoredZip } from "../../../packages/integration-kit/src/zip.ts"
import { startFakeHost } from "../../../packages/testing/src/index.ts"
import { HEADER, inspectHost, LOADER } from "../plugin/src/host-install.ts"
import { readPackage, resultContent, verifySavedHint } from "../plugin/src/integration.ts"
import { METHOD_KEY } from "../plugin/src/method.ts"
import { methodsDir, packageRoot } from "../plugin/src/paths.ts"
import { announcement, content, isolatedPaths, loadPlugin } from "./helpers.ts"

const version = readPackage().version
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function capabilities(methods: string[]) {
  return {
    methods: [...new Set(["hello", "context", "apply", "status", "release", ...methods])],
    operations: [],
    undo: "transactional" as const,
    background: false,
  }
}

async function offered(
  methods: Record<string, (params: Record<string, unknown>) => unknown> = {},
  process?: { pid: number; temp_dir: string },
) {
  return await startFakeHost({
    hostVersion: version,
    appVersion: "1.6.15.0",
    capabilities: capabilities(Object.keys(methods)),
    methods: { context: () => ({ revision: 3, nodes: [] }), ...methods },
    ...(process ? { helloOverride: (hello: Record<string, unknown>) => ({ ...hello, process }) } : {}),
  })
}

describe("OpenCode for Gaffer 0.3.0", () => {
  it("registers one tool per manifest, under its manifest name", async () => {
    const plugin = await loadPlugin()
    try {
      const names = readdirSync(join(methodsDir, "tools"))
        .filter((file) => file.endsWith(".json"))
        .map((file) => (JSON.parse(readFileSync(join(methodsDir, "tools", file), "utf8")) as { name: string }).name)
      assert.deepEqual([...plugin.tools.keys()].sort(), names.sort())
      for (const name of [
        "gaffer_hosts",
        "gaffer_install_host",
        "gaffer_context",
        "gaffer_apply",
        "gaffer_execute_code",
      ])
        assert.ok(plugin.tools.has(name), name)
    } finally {
      await plugin.cleanup()
    }
  })

  it("attaches the only free script at the first call, with the working method; listing asks no permission", async () => {
    const plugin = await loadPlugin()
    const host = await offered()
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      const listed = content(await plugin.tool("gaffer_hosts"))
      assert.equal(listed.scripts.length, 1)
      assert.equal(listed.attached_to, null)
      assert.deepEqual(plugin.permissions, [])
      const read = content(await plugin.tool("gaffer_context"))
      assert.equal(read.revision, 3)
      assert.equal(read.attached_to.label, "Gaffer: shot.gfr")
      assert.deepEqual(plugin.methods, [{ sessionID: "ses_test", key: METHOD_KEY }])
      assert.deepEqual(plugin.permissions, [])
      // Another conversation does not take the script this one uses: it is told who holds it.
      await assert.rejects(plugin.tool("gaffer_context", {}, "ses_other"), /attached to another conversation/)
      const shared = content(await plugin.tool("gaffer_hosts", { attach: listed.scripts[0].offerID }, "ses_other"))
      assert.match(shared.shared, /Another conversation is attached to this Gaffer too/)
      assert.deepEqual(plugin.asked, [
        { action: "gaffer_attach", resources: ["Gaffer: shot.gfr"], metadata: { label: "Gaffer: shot.gfr" } },
      ])
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  // The panel attaches its conversation through the attachHost RPC: next to a CodeNomad conversation attached to the
  // same script (gaffer_hosts), both stay attached, and either one's detach leaves the other (no lock, as Houdini).
  it("the panel's conversation and another one are attached to the same script together", async () => {
    const plugin = await loadPlugin()
    const host = await offered()
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      const offerID = content(await plugin.tool("gaffer_hosts")).scripts[0].offerID
      assert.equal(content(await plugin.tool("gaffer_hosts", { attach: offerID })).attached, "attached")
      assert.deepEqual(await plugin.rpc("attachHost", { offerID, sessionID: "ses_other" }), { outcome: "attached" })
      const listed = content(await plugin.tool("gaffer_hosts", {}, "ses_other"))
      assert.equal(listed.attached_to.offerID, offerID)
      assert.equal(content(await plugin.tool("gaffer_context")).revision, 3)
      assert.equal(content(await plugin.tool("gaffer_context", {}, "ses_other")).revision, 3)
      // The panel closes: the other conversation keeps the script; and the other way round.
      assert.deepEqual(await plugin.rpc("detachHost", { sessionID: "ses_other" }), { outcome: "detached" })
      assert.equal(content(await plugin.tool("gaffer_context")).attached_to.offerID, offerID)
      assert.deepEqual(await plugin.rpc("attachHost", { offerID, sessionID: "ses_other" }), { outcome: "attached" })
      assert.equal(content(await plugin.tool("gaffer_hosts", { detach: true })).detached, "detached")
      assert.equal(content(await plugin.tool("gaffer_context", {}, "ses_other")).attached_to.offerID, offerID)
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  it("attaching one of several scripts asks gaffer_attach; a refusal attaches nothing", async () => {
    let deny = true
    const plugin = await loadPlugin({
      answer: () => {
        if (deny) throw new Error("Permission denied")
      },
    })
    const first = await offered()
    const second = await offered()
    try {
      await plugin.rpc("advertise", announcement(first.endpoint, first.scriptHandle, "first", "Gaffer: a.gfr"))
      await plugin.rpc("advertise", announcement(second.endpoint, second.scriptHandle, "second", "Gaffer: b.gfr"))
      // Two free scripts: none is chosen for the conversation.
      await assert.rejects(plugin.tool("gaffer_context"), /gaffer_hosts/)
      const offers = content(await plugin.tool("gaffer_hosts")).scripts as Array<{ offerID: string; label: string }>
      const b = offers.find((offer) => offer.label === "Gaffer: b.gfr")?.offerID as string
      await assert.rejects(plugin.tool("gaffer_hosts", { attach: b }), /Permission denied/)
      assert.deepEqual(plugin.permissions, ["gaffer_attach"])
      assert.equal(((await plugin.rpc("bindingRead", { sessionID: "ses_test" })) as { state: string }).state, "none")
      deny = false
      await plugin.tool("gaffer_hosts", { attach: b })
      assert.equal(content(await plugin.tool("gaffer_context")).attached_to.label, "Gaffer: b.gfr")
      assert.deepEqual(plugin.permissions, ["gaffer_attach", "gaffer_attach"])
    } finally {
      await plugin.cleanup()
      await first.close()
      await second.close()
    }
  })

  it("two scripts with the same label are said, not left to look like one", async () => {
    const plugin = await loadPlugin()
    const first = await offered()
    const second = await offered()
    try {
      await plugin.rpc("advertise", announcement(first.endpoint, first.scriptHandle, "first", "Gaffer: shot.gfr"))
      assert.equal(content(await plugin.tool("gaffer_hosts")).same_label, undefined)
      await plugin.rpc("advertise", announcement(second.endpoint, second.scriptHandle, "second", "Gaffer: shot.gfr"))
      const listed = content(await plugin.tool("gaffer_hosts"))
      assert.equal(listed.scripts.length, 2)
      assert.match(listed.same_label, /Several Gaffers have the same file open \(Gaffer: shot\.gfr\)/)
    } finally {
      await plugin.cleanup()
      await first.close()
      await second.close()
    }
  })

  it("says how to offer a script when none is", async () => {
    const plugin = await loadPlugin()
    try {
      await assert.rejects(
        plugin.tool("gaffer_context"),
        /No Gaffer script is offered: start Gaffer 1\.6 or later with OpenCode Link installed \(gaffer_install_host\)/,
      )
    } finally {
      await plugin.cleanup()
    }
  })

  it("execute_code asks gaffer_execute_code with the code for each run, reads a script file as is", async () => {
    const plugin = await loadPlugin()
    const dir = mkdtempSync(join(tmpdir(), "gaffer-script-"))
    const runs: Array<Record<string, unknown>> = []
    const host = await offered({
      execute_code: (params) => {
        runs.push(params)
        return { status: "executed", result: { ok: 1 }, changed: true, saved: runs.length === 2 }
      },
    })
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      const code = "result = {'ok': len(script.children())}"
      const out = content(await plugin.tool("gaffer_execute_code", { code, read_only: true }))
      assert.deepEqual([out.status, out.result], ["executed", { ok: 1 }])
      assert.deepEqual(plugin.asked, [{ action: "gaffer_execute_code", resources: ["*"], metadata: { code } }])
      assert.deepEqual(runs[0], { session_id: "ses_test", code, read_only: true, script_handle: host.scriptHandle })
      const file = join(dir, "build.py")
      writeFileSync(file, '﻿import GafferScene\nscript.addChild(GafferScene.Sphere())\nprint("é")\n')
      const saved = content(await plugin.tool("gaffer_execute_code", { path: file }))
      assert.equal(runs[1]?.code, 'import GafferScene\nscript.addChild(GafferScene.Sphere())\nprint("é")\n')
      assert.equal(runs[1]?.filename, file)
      assert.equal(runs[1]?.read_only, undefined)
      assert.equal(saved.check, verifySavedHint(true))
      assert.deepEqual(plugin.asked[1], {
        action: "gaffer_execute_code",
        resources: [file],
        metadata: { path: file, code: runs[1]?.code },
      })
      await assert.rejects(plugin.tool("gaffer_execute_code", { code: "x", path: file }), /code or path, not both/)
      assert.equal(runs.length, 2)
    } finally {
      await plugin.cleanup()
      await host.close()
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it("execute_code is refused before any permission when the Gaffer does not list it", async () => {
    const plugin = await loadPlugin()
    const host = await offered()
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      await assert.rejects(plugin.tool("gaffer_execute_code", { code: "print(1)" }), /Code execution is not available/)
      assert.deepEqual(plugin.permissions, [])
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  it("apply asks gaffer_write with its operations; a render's previews are attachments", async () => {
    const answers: string[] = []
    let deny = false
    const plugin = await loadPlugin({
      answer: (action) => {
        answers.push(action)
        if (deny) throw new Error("Permission denied")
      },
    })
    const preview = join(tmpdir(), "gaffer-preview.png")
    const applied: Array<Record<string, unknown>> = []
    const host = await offered({
      apply: (params) => {
        applied.push(params)
        return { status: "applied", revision: 4, job: { outputs: [{ name: "beauty", preview_path: preview }] } }
      },
    })
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      const operations = [{ op: "render.start", target: "Render" }]
      const out = (await plugin.tool("gaffer_apply", { if_revision: 3, command_id: "a", operations })) as unknown
      const parts = (out as { content?: unknown }).content ?? out
      assert.ok(Array.isArray(parts), JSON.stringify(out))
      assert.equal((parts as Array<{ type: string }>)[1]?.type, "file")
      assert.ok(!(parts as Array<{ text?: string }>)[0]?.text?.includes(preview))
      assert.deepEqual(plugin.asked, [
        { action: "gaffer_write", resources: ["Gaffer: shot.gfr"], metadata: { operations } },
      ])
      assert.equal(applied[0]?.session_id, "ses_test")
      deny = true
      await assert.rejects(plugin.tool("gaffer_apply", { if_revision: 4, command_id: "b", operations }), /denied/)
      assert.equal(applied.length, 1)
      assert.deepEqual(answers, ["gaffer_write", "gaffer_write"])
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  it("a refusal reads the hint for its code with Gaffer's own text and data", async () => {
    const plugin = await loadPlugin()
    const host = await offered({
      release: () => {
        throw {
          code: "stale_revision",
          message: "Read the script again (gaffer_context) and release with its revision.",
          data: { current_revision: 7, reason: "revision_conflict" },
        }
      },
    })
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      await assert.rejects(
        plugin.tool("gaffer_context", { release_quarantine: 2 }),
        /^Error: The script changed since your last read: call gaffer_context and use its revision\. Read the script again \(gaffer_context\) and release with its revision\. \{"current_revision":7,"reason":"revision_conflict"\}$/,
      )
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  it("a call that outlasts its wait says Gaffer still runs it (status off the main thread)", async () => {
    const temp = mkdtempSync(join(tmpdir(), "gaffer_temp-"))
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
        plugin.tool("gaffer_execute_code", { code: "import time; time.sleep(5)", timeout_s: 1 }),
        /Gaffer is still running execute_code \(1\.2 s so far\): it goes on in Gaffer, nothing is lost/,
      )
    } finally {
      await plugin.cleanup()
      await host.close()
      rmSync(temp, { recursive: true, force: true })
    }
  })

  it("an earlier OpenCode installation in <home>/gaffer is a notice and said by gaffer_hosts", async () => {
    const paths = isolatedPaths()
    const gui = join(paths.home, "gaffer", "startup", "gui")
    mkdirSync(gui, { recursive: true })
    const plugin = await loadPlugin({ paths })
    try {
      assert.deepEqual(((await plugin.rpc("status")) as { notices?: string[] }).notices ?? [], [])
      assert.equal(content(await plugin.tool("gaffer_hosts")).legacy_install, undefined)
      // A startup file of this integration's own is not an earlier installation.
      writeFileSync(join(gui, "openCode.py"), `${HEADER}import runpy\n`)
      assert.deepEqual(((await plugin.rpc("status")) as { notices?: string[] }).notices ?? [], [])
      writeFileSync(join(gui, "openCode.py"), "import GafferOpenCode\n")
      mkdirSync(join(paths.home, "gaffer", "OpenCode"))
      assert.deepEqual(((await plugin.rpc("status")) as { notices?: string[] }).notices, ["legacy_install"])
      const listed = content(await plugin.tool("gaffer_hosts")).legacy_install
      assert.deepEqual(listed.found, [
        { path: join(gui, "openCode.py"), kind: "startup file" },
        { path: join(paths.home, "gaffer", "OpenCode"), kind: "folder" },
      ])
      assert.match(listed.fix, /remove them, then restart Gaffer/)
      rmSync(join(gui, "openCode.py"))
      assert.deepEqual(((await plugin.rpc("status")) as { notices?: string[] }).notices, ["legacy_install"])
    } finally {
      await plugin.cleanup()
      rmSync(paths.home, { recursive: true, force: true })
    }
  })

  it("installs the managed loader and payload into the startup folder, idempotently; never an unmanaged file", async () => {
    const root = mkdtempSync(join(tmpdir(), "gaffer-install-"))
    const startup = join(root, "startup", "gui")
    const state = join(root, "state")
    const plugin = await loadPlugin({ options: { hostStartupDir: startup }, state })
    try {
      const marker = readPackage().opencodeIntegration
      const host = marker.host as NonNullable<typeof marker.host>
      const archive = readFileSync(join(packageRoot, host.payload))
      assert.equal(createHash("sha256").update(archive).digest("hex"), host.sha256)
      const entries = readStoredZip(archive)
      const manifest = entries.find((entry) => entry.name === "manifest.json")?.data.toString() ?? "{}"
      assert.equal(JSON.parse(manifest).version, version)
      assert.ok(entries.some((entry) => entry.name === "sidecar.cjs"))
      assert.ok(entries.some((entry) => entry.name === "startup/gui/openCode.py"))

      assert.deepEqual(inspectHost(startup), { kind: "not-inspected" })
      assert.deepEqual(await plugin.rpc("installHost"), {
        outcome: "installed",
        detail: "Restart Gaffer: OpenCode Link offers its scripts to OpenCode when it starts.",
      })
      const loader = join(startup, LOADER)
      const source = readFileSync(loader, "utf8")
      assert.ok(source.startsWith(HEADER))
      assert.ok(!source.includes(packageRoot.replaceAll("\\", "/")), "the loader starts the copy, not the package")
      assert.ok(existsSync(join(state, "hosts", host.sha256, "startup", "gui", "openCode.py")))
      assert.deepEqual(inspectHost(startup), { kind: "found", version })
      assert.deepEqual(await plugin.rpc("installHost"), { outcome: "unchanged" })
      assert.deepEqual(readdirSync(join(state, "hosts")), [host.sha256])

      writeFileSync(loader, "# the user's own startup file\n")
      assert.deepEqual(inspectHost(startup), { kind: "not-inspected" })
      assert.equal(((await plugin.rpc("installHost")) as { outcome: string }).outcome, "refused")
      assert.equal(readFileSync(loader, "utf8"), "# the user's own startup file\n")
    } finally {
      await plugin.cleanup()
      rmSync(root, { recursive: true, force: true })
    }
  })

  it("gaffer_install_host asks its permission; a WSL daemon refuses before any write", async () => {
    const root = mkdtempSync(join(tmpdir(), "gaffer-install-"))
    const startup = join(root, "startup", "gui")
    const plugin = await loadPlugin({ options: { hostStartupDir: startup }, state: join(root, "state") })
    const wsl = await loadPlugin({ options: { hostStartupDir: startup }, state: join(root, "never"), wsl: () => true })
    try {
      assert.equal(content(await plugin.tool("gaffer_install_host")).outcome, "installed")
      assert.deepEqual(plugin.permissions, ["gaffer_install_host"])
      rmSync(startup, { recursive: true, force: true })
      assert.equal(((await wsl.rpc("status")) as { host: { state: string } }).host.state, "unsupported")
      assert.equal(((await wsl.rpc("installHost")) as { outcome: string }).outcome, "refused")
      assert.equal(existsSync(join(root, "never")), false)
      assert.equal(existsSync(startup), false)
    } finally {
      await plugin.cleanup()
      await wsl.cleanup()
      rmSync(root, { recursive: true, force: true })
    }
  })

  it("attaches only this Location's conversations: ownership, detach, renewal and expiry", async () => {
    let now = 10_000
    const plugin = await loadPlugin({ now: () => now })
    const host = await offered()
    try {
      const announced = announcement(host.endpoint, host.scriptHandle)
      assert.deepEqual(await plugin.rpc("advertise", { ...announced, host: "localhost" }), { outcome: "refused" })
      assert.deepEqual(await plugin.rpc("advertise", { ...announced, token: "0".repeat(64) }), { outcome: "refused" })
      // hello names another script than the announced one: refused.
      assert.deepEqual(await plugin.rpc("advertise", { ...announced, script_handle: "gaffer:other" }), {
        outcome: "refused",
      })
      assert.deepEqual(await plugin.rpc("advertise", announced), { outcome: "accepted" })
      const offerID = plugin.registry.hosts().offers[0]?.offerID as string
      assert.deepEqual(await plugin.rpc("attachHost", { offerID, sessionID: "ses_foreign" }), { outcome: "refused" })
      assert.deepEqual(await plugin.rpc("bindingRead", { sessionID: "ses_foreign" }), { state: "unknown" })
      assert.deepEqual(await plugin.rpc("attachHost", { offerID, sessionID: "ses_test" }), { outcome: "attached" })
      assert.equal(
        ((await plugin.rpc("bindingRead", { sessionID: "ses_test" })) as { state: string }).state,
        "attached",
      )
      // A conversation moved to another Location is no longer this one's.
      plugin.sessions.set("ses_test", { id: "ses_test", location: { directory: join(tmpdir(), "foreign") } })
      assert.deepEqual(await plugin.rpc("bindingRead", { sessionID: "ses_test" }), { state: "unknown" })
      await assert.rejects(plugin.rpc("detachHost", { sessionID: "ses_test" }), /ownership/)
      plugin.sessions.set("ses_test", { id: "ses_test", location: { ...plugin.location } })
      assert.deepEqual(await plugin.rpc("detachHost", { sessionID: "ses_test" }), { outcome: "detached" })
      assert.deepEqual(await plugin.rpc("advertise", announced), { outcome: "renewed" })
      assert.equal(((await plugin.rpc("bindingRead", { sessionID: "ses_test" })) as { state: string }).state, "none")
      now += 15_001
      assert.deepEqual(await plugin.rpc("hosts"), { offers: [] })
      assert.ok(plugin.events.every(({ data }) => JSON.stringify(data) === "{}"))
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  it("render feedback uses attachments and removes temporary image paths from the text", () => {
    const job = { outputs: [{ name: "beauty", preview_path: join(tmpdir(), "preview.png") }, { name: "pending" }] }
    for (const result of [{ job }, { jobs: [job, { outputs: [] }] }, { job, jobs: [job] }]) {
      const parts = resultContent(result)
      assert.ok(Array.isArray(parts))
      assert.equal(parts.length, "job" in result && "jobs" in result ? 3 : 2)
      assert.equal(parts[1]?.type, "file")
      assert.ok(!(parts[0] as { text: string }).text.includes("preview"))
      assert.ok(job.outputs[0]?.preview_path, "the caller's result is left as it was")
    }
    assert.equal(resultContent({ status: "applied" }), '{"status":"applied"}')
  })
})
