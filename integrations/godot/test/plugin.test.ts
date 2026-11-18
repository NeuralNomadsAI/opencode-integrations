// OpenCode for Godot 0.1.0 against a fake installed part: the tools are the manifests', the conversation attaches
// itself to the only free editor, the working method joins it, code runs only with permission, the fixed programs
// ask none, and OpenCode Link installs into a Godot project.

import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { describe, it } from "node:test"

import { readStoredZip } from "../../../packages/integration-kit/src/zip.ts"
import { startFakeHost } from "../../../packages/testing/src/index.ts"
import { inspectHost } from "../plugin/src/host-install.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { METHOD_KEY, METHOD_TEXT } from "../plugin/src/method.ts"
import { methodsDir, packageRoot } from "../plugin/src/paths.ts"
import { programCode } from "../plugin/src/programs.ts"
import { announcement, content, loadPlugin, makeProject } from "./helpers.ts"

const version = readPackage().version
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function capabilities(methods: string[]) {
  return {
    methods: [...new Set(["hello", "status", "context", "release", ...methods])],
    operations: [],
    undo: "grouped_best_effort" as const,
    background: false,
  }
}

async function offered(
  methods: Record<string, (params: Record<string, unknown>) => unknown> = {},
  process?: { pid: number; temp_dir: string },
) {
  return await startFakeHost({
    hostVersion: version,
    appVersion: "4.7.2-stable (official)",
    capabilities: capabilities(Object.keys(methods)),
    methods: { context: () => ({ revision: 3, nodes: [] }), ...methods },
    ...(process ? { helloOverride: (hello: Record<string, unknown>) => ({ ...hello, process }) } : {}),
  })
}

describe("OpenCode for Godot 0.1.0", () => {
  it("registers one tool per manifest, under its manifest name", async () => {
    const plugin = await loadPlugin()
    try {
      const names = readdirSync(join(methodsDir, "tools"))
        .filter((file) => file.endsWith(".json"))
        .map((file) => (JSON.parse(readFileSync(join(methodsDir, "tools", file), "utf8")) as { name: string }).name)
      assert.deepEqual([...plugin.tools.keys()].sort(), names.sort())
      assert.deepEqual(names.sort(), [
        "godot_context",
        "godot_execute_code",
        "godot_hosts",
        "godot_inspect",
        "godot_install_host",
        "godot_run",
        "godot_save",
        "godot_verify",
      ])
      assert.ok(METHOD_TEXT.split("\n").length <= 10)
    } finally {
      await plugin.cleanup()
    }
  })

  it("attaches the only free editor at the first call, with the working method; listing asks no permission", async () => {
    const plugin = await loadPlugin()
    const host = await offered()
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      const listed = content(await plugin.tool("godot_hosts"))
      assert.equal(listed.editors.length, 1)
      assert.equal(listed.attached_to, null)
      const read = content(await plugin.tool("godot_context"))
      assert.equal(read.revision, 3)
      assert.equal(read.attached_to.label, "Godot: Trial — main.tscn")
      assert.deepEqual(plugin.methods, [{ sessionID: "ses_test", key: METHOD_KEY }])
      assert.deepEqual(plugin.permissions, [])
      await assert.rejects(plugin.tool("godot_context", {}, "ses_other"), /attached to another conversation/)
      const shared = content(await plugin.tool("godot_hosts", { attach: listed.editors[0].offerID }, "ses_other"))
      assert.match(shared.shared, /Another conversation is attached to this Godot too/)
      assert.deepEqual(plugin.asked, [
        {
          action: "godot_attach",
          resources: ["Godot: Trial — main.tscn"],
          metadata: { label: "Godot: Trial — main.tscn" },
        },
      ])
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  // The dock attaches its conversation through the attachHost RPC, next to a CodeNomad conversation attached to the same
  // project: both stay attached, and either one's detach leaves the other (no lock).
  it("the dock's conversation and another one are attached to the same editor together", async () => {
    const plugin = await loadPlugin()
    const host = await offered()
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      const offerID = content(await plugin.tool("godot_hosts")).editors[0].offerID
      assert.equal(content(await plugin.tool("godot_hosts", { attach: offerID })).attached, "attached")
      assert.deepEqual(await plugin.rpc("attachHost", { offerID, sessionID: "ses_other" }), { outcome: "attached" })
      assert.equal(content(await plugin.tool("godot_context", {}, "ses_other")).revision, 3)
      assert.deepEqual(await plugin.rpc("detachHost", { sessionID: "ses_other" }), { outcome: "detached" })
      assert.equal(content(await plugin.tool("godot_context")).attached_to.offerID, offerID)
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  it("two editors with the same label are said; attaching one of them asks godot_attach", async () => {
    let deny = true
    const plugin = await loadPlugin({
      answer: () => {
        if (deny) throw new Error("Permission denied")
      },
    })
    const first = await offered()
    const second = await offered()
    try {
      await plugin.rpc("advertise", announcement(first.endpoint, first.scriptHandle, "first"))
      assert.equal(content(await plugin.tool("godot_hosts")).same_label, undefined)
      await plugin.rpc("advertise", announcement(second.endpoint, second.scriptHandle, "second"))
      const listed = content(await plugin.tool("godot_hosts"))
      assert.match(listed.same_label, /Several Godot editors show the same project and scene/)
      await assert.rejects(plugin.tool("godot_context"), /godot_hosts attach/)
      await assert.rejects(plugin.tool("godot_hosts", { attach: listed.editors[1].offerID }), /Permission denied/)
      assert.equal(((await plugin.rpc("bindingRead", { sessionID: "ses_test" })) as { state: string }).state, "none")
      deny = false
      await plugin.tool("godot_hosts", { attach: listed.editors[1].offerID })
      assert.equal(content(await plugin.tool("godot_context")).attached_to.offerID, listed.editors[1].offerID)
    } finally {
      await plugin.cleanup()
      await first.close()
      await second.close()
    }
  })

  it("says how to offer a project when none is", async () => {
    const plugin = await loadPlugin()
    try {
      await assert.rejects(
        plugin.tool("godot_context"),
        /No Godot editor is offered: open the project in Godot 4\.7 or later with OpenCode Link installed in it \(godot_install_host\)/,
      )
    } finally {
      await plugin.cleanup()
    }
  })

  it("execute_code asks godot_execute_code with the code for each run, reads a script file as is", async () => {
    const plugin = await loadPlugin()
    const dir = mkdtempSync(join(tmpdir(), "godot-script-"))
    const runs: Array<Record<string, unknown>> = []
    const host = await offered({
      execute_code: (params) => {
        runs.push(params)
        return { status: "executed", result: { ok: 1 }, changed: true, undo_step: "OpenCode: code" }
      },
    })
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      const code = "result.count = scene.get_child_count()"
      const out = content(await plugin.tool("godot_execute_code", { code, read_only: true }))
      assert.deepEqual([out.status, out.result], ["executed", { ok: 1 }])
      assert.deepEqual(plugin.asked, [{ action: "godot_execute_code", resources: ["*"], metadata: { code } }])
      assert.deepEqual(runs[0], { session_id: "ses_test", code, read_only: true, script_handle: host.scriptHandle })
      const file = join(dir, "build.gd")
      writeFileSync(file, '﻿func run():\n\tprint("é")\n')
      await plugin.tool("godot_execute_code", { path: file })
      assert.equal(runs[1]?.code, 'func run():\n\tprint("é")\n')
      assert.equal(runs[1]?.filename, file)
      assert.deepEqual(plugin.asked[1], {
        action: "godot_execute_code",
        resources: [file],
        metadata: { path: file, code: runs[1]?.code },
      })
      await assert.rejects(plugin.tool("godot_execute_code", { code: "x", path: file }), /code or path, not both/)
      assert.equal(runs.length, 2)
    } finally {
      await plugin.cleanup()
      await host.close()
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it("execute_code is refused before any permission when the editor does not list it", async () => {
    const plugin = await loadPlugin()
    const host = await offered()
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      await assert.rejects(plugin.tool("godot_execute_code", { code: "pass" }), /Code execution is not available/)
      assert.deepEqual(plugin.permissions, [])
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  it("fixed programs take parameters as data; executing criteria asks permission", async () => {
    const plugin = await loadPlugin()
    const runs: Array<Record<string, unknown>> = []
    const host = await offered({
      execute_code: (params) => {
        runs.push(params)
        return { status: "executed", result: { passed: true, path: "res://main.tscn", copy: false } }
      },
    })
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      await plugin.tool("godot_inspect", { search: "rigid body" })
      const criteria = [{ expr: 'node("Box").position.x', op: ">", value: 0 }]
      assert.equal(content(await plugin.tool("godot_verify", { criteria })).passed, true)
      assert.match(content(await plugin.tool("godot_save")).check, /godot_verify with saved: true/)
      assert.deepEqual(plugin.permissions, ["godot_execute_code"])
      assert.deepEqual(
        runs.map((run) => [run.code, run.params, run.read_only]),
        [
          [programCode("inspect"), { search: "rigid body", limit: 40, depth: 6 }, true],
          [programCode("verify"), { criteria, errors: true }, true],
          [programCode("save"), { copy: false, overwrite: false }, true],
        ],
      )
      await assert.rejects(plugin.tool("godot_inspect", {}), /Give search, class, node and\/or tree/)
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  it("a refusal reads the hint for its code with Godot's own text and data", async () => {
    const plugin = await loadPlugin()
    const host = await offered({
      release: () => {
        throw {
          code: "stale_revision",
          message: "Read the scene again (godot_context) and release with its revision.",
          data: { current_revision: 7, reason: "revision_conflict" },
        }
      },
    })
    try {
      await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
      await assert.rejects(
        plugin.tool("godot_context", { release_quarantine: 2 }),
        /^Error: The scene changed since your last read: call godot_context and use its revision\. Read the scene again \(godot_context\) and release with its revision\. \{"current_revision":7,"reason":"revision_conflict"\}$/,
      )
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })

  it("a call that outlasts its wait says Godot still runs it (status off the main thread)", async () => {
    const temp = mkdtempSync(join(tmpdir(), "godot_temp-"))
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
        plugin.tool("godot_execute_code", { code: "OS.delay_msec(5000)", timeout_s: 1 }),
        /Godot is still running execute_code \(1\.2 s so far\): it goes on in Godot, nothing is lost/,
      )
    } finally {
      await plugin.cleanup()
      await host.close()
      rmSync(temp, { recursive: true, force: true })
    }
  })

  it("installs OpenCode Link into the conversation's project, idempotently; never over another add-on", async () => {
    const project = makeProject("Install")
    const plugin = await loadPlugin({ directory: project })
    const elsewhere = await loadPlugin({ directory: tmpdir() })
    try {
      const marker = readPackage().opencodeIntegration
      const host = marker.host as NonNullable<typeof marker.host>
      const archive = readFileSync(join(packageRoot, host.payload))
      assert.equal(createHash("sha256").update(archive).digest("hex"), host.sha256)
      const names = readStoredZip(archive).map((entry) => entry.name)
      for (const file of ["plugin.cfg", "plugin.gd", "link.gd", "service.gd", "runner.gd", "hints.gd", "dock.gd"])
        assert.ok(names.includes(`addons/opencode_link/${file}`), file)
      assert.ok(names.includes("addons/opencode_link/sidecar.cjs"))

      assert.equal(((await plugin.rpc("status")) as { host: { state: string } }).host.state, "absent")
      assert.deepEqual(inspectHost(project), { kind: "not-found" })
      assert.deepEqual(await plugin.rpc("installHost"), {
        outcome: "installed",
        detail:
          "Restart this project's Godot editor if it is open: OpenCode Link offers the project to OpenCode when the editor starts.",
      })
      assert.deepEqual(inspectHost(project), { kind: "found", version })
      assert.match(readFileSync(join(project, "project.godot"), "utf8"), /^config\/name="Install"$/m)
      assert.match(
        readFileSync(join(project, "project.godot"), "utf8"),
        /\[editor_plugins\]\n\nenabled=PackedStringArray\("res:\/\/addons\/opencode_link\/plugin\.cfg"\)\n$/,
      )
      assert.equal(((await plugin.rpc("status")) as { host: { state: string } }).host.state, "unknown")
      assert.deepEqual(await plugin.rpc("installHost"), { outcome: "unchanged" })
      assert.deepEqual(readdirSync(join(project, "addons")), ["opencode_link"])

      // An older version is replaced whole.
      const cfg = join(project, "addons", "opencode_link", "plugin.cfg")
      writeFileSync(cfg, readFileSync(cfg, "utf8").replace(/version="[^"]+"/, 'version="0.0.9"'))
      writeFileSync(join(project, "addons", "opencode_link", "stale.gd"), "")
      assert.equal(((await plugin.rpc("status")) as { host: { state: string } }).host.state, "outdated")
      assert.equal(((await plugin.rpc("installHost")) as { outcome: string }).outcome, "updated")
      assert.equal(existsSync(join(project, "addons", "opencode_link", "stale.gd")), false)

      // Another add-on of the same folder name is left untouched.
      rmSync(join(project, "addons", "opencode_link"), { recursive: true })
      mkdirSync(join(project, "addons", "opencode_link"))
      writeFileSync(cfg, '[plugin]\nname="Someone else"\n')
      assert.equal(((await plugin.rpc("installHost")) as { outcome: string }).outcome, "refused")
      assert.equal(readFileSync(cfg, "utf8"), '[plugin]\nname="Someone else"\n')

      // A conversation outside any Godot project: nothing to install into.
      assert.equal(((await elsewhere.rpc("installHost")) as { outcome: string }).outcome, "refused")
      assert.equal(((await elsewhere.rpc("status")) as { host: { state: string } }).host.state, "unknown")
    } finally {
      await plugin.cleanup()
      await elsewhere.cleanup()
      rmSync(project, { recursive: true, force: true })
    }
  })

  it("godot_install_host asks its permission for the project it names; a WSL daemon refuses", async () => {
    const project = makeProject("Named")
    const plugin = await loadPlugin({ directory: tmpdir() })
    const wsl = await loadPlugin({ directory: project, wsl: () => true })
    try {
      await assert.rejects(plugin.tool("godot_install_host", { project: tmpdir() }), /not a Godot project folder/)
      assert.equal(content(await plugin.tool("godot_install_host", { project })).outcome, "installed")
      assert.deepEqual(plugin.asked, [{ action: "godot_install_host", resources: [project] }])
      rmSync(join(project, "addons"), { recursive: true })
      assert.equal(((await wsl.rpc("status")) as { host: { state: string } }).host.state, "unsupported")
      assert.equal(((await wsl.rpc("installHost")) as { outcome: string }).outcome, "refused")
      assert.equal(existsSync(join(project, "addons")), false)
    } finally {
      await plugin.cleanup()
      await wsl.cleanup()
      rmSync(project, { recursive: true, force: true })
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
      assert.deepEqual(await plugin.rpc("advertise", { ...announced, label: "Godot: a/b" }), { outcome: "refused" })
      assert.deepEqual(await plugin.rpc("advertise", announced), { outcome: "accepted" })
      // The label follows the edited scene: a renewal with the new label keeps the offer.
      const offerID = plugin.registry.hosts().offers[0]?.offerID as string
      assert.deepEqual(await plugin.rpc("advertise", { ...announced, label: "Godot: Trial — level.tscn" }), {
        outcome: "renewed",
      })
      assert.deepEqual(
        plugin.registry.hosts().offers.map((offer) => [offer.offerID, offer.label]),
        [[offerID, "Godot: Trial — level.tscn"]],
      )
      assert.deepEqual(await plugin.rpc("attachHost", { offerID, sessionID: "ses_foreign" }), { outcome: "refused" })
      assert.deepEqual(await plugin.rpc("attachHost", { offerID, sessionID: "ses_test" }), { outcome: "attached" })
      plugin.sessions.set("ses_test", { id: "ses_test", location: { directory: join(tmpdir(), "foreign") } })
      assert.deepEqual(await plugin.rpc("bindingRead", { sessionID: "ses_test" }), { state: "unknown" })
      plugin.sessions.set("ses_test", { id: "ses_test", location: { ...plugin.location } })
      assert.deepEqual(await plugin.rpc("detachHost", { sessionID: "ses_test" }), { outcome: "detached" })
      now += 15_001
      assert.deepEqual(await plugin.rpc("hosts"), { offers: [] })
      assert.ok(plugin.events.every(({ data }) => JSON.stringify(data) === "{}"))
    } finally {
      await plugin.cleanup()
      await host.close()
    }
  })
})
