// Plugin behaviour without Blender: status derivation, offers and leases with an injected clock,
// advertisement refusals, attachment (explicit and automatic), tools forwarding to a fake host, images as
// attachments, the fixed programs, legacy detection and host installation.

import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs"
import net from "node:net"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, beforeEach, describe, it } from "node:test"

import { startFakeHost } from "../../../packages/conformance/src/index.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { OFFER_LEASE_MS, OFFER_RENEW_MS } from "../plugin/src/kit.ts"
import { METHOD_KEY, METHOD_TEXT, resetMethodForTests } from "../plugin/src/method.ts"
import { announcement, clock, fixturePaths, type LoadedPlugin, loadPlugin } from "./helpers/plugin.ts"

const cleanups: Array<() => Promise<void> | void> = []
after(async () => {
  for (const cleanup of cleanups.reverse()) await cleanup()
})

async function plugin(input: Parameters<typeof loadPlugin>[0] = {}): Promise<LoadedPlugin> {
  const loaded = await loadPlugin(input)
  cleanups.push(() => loaded.cleanup())
  return loaded
}

// The methods an OpenCode Link 0.4.0 host lists, on Blender 5.3.
const CAPABILITIES = {
  methods: ["hello", "scene", "apply", "execute_code", "lab_tool"],
  operations: [],
  undo: "grouped_best_effort" as const,
  background: false,
}

async function fakeHost(options: Parameters<typeof startFakeHost>[0] = {}) {
  const host = await startFakeHost({
    hostVersion: readPackage().version,
    appVersion: "5.3.0",
    scriptHandle: "blend-test",
    capabilities: CAPABILITIES,
    ...options,
  })
  cleanups.push(() => host.close())
  return host
}

function paths() {
  const fixture = fixturePaths()
  cleanups.push(() => fixture.cleanup())
  return fixture
}

const content = (result: { content?: unknown }) => JSON.parse(String(result.content))

type Status = {
  host: { state: string; action?: string; partVersion?: string }
  connection: { state: string }
  notices?: string[]
}

describe("status.host.state (AC-B7, AC-B13)", () => {
  it("ok, outdated and incompatible come only from a live hello", async () => {
    for (const [options, state] of [
      [{ hostVersion: readPackage().version }, "ok"],
      [{ hostVersion: "0.3.8" }, "outdated"],
      [{ appVersion: "5.2.3" }, "incompatible"],
      [{ hostVersion: "99.0.0" }, "incompatible"],
    ] as const) {
      const loaded = await plugin({ paths: paths() })
      assert.equal(((await loaded.rpc("status")) as Status).host.state, "unknown")
      const host = await fakeHost(options)
      assert.deepEqual(await loaded.rpc("advertise", announcement(host.endpoint, host.scriptHandle)), {
        outcome: "accepted",
      })
      const status = (await loaded.rpc("status")) as Status
      assert.equal(status.host.state, state, JSON.stringify(options))
      assert.equal(status.host.action, state === "outdated" ? "update" : undefined)
      assert.equal(status.connection.state, "available")
    }
  })

  it("unsupported under the simulated WSL condition", async () => {
    const loaded = await plugin({ wsl: () => true, paths: paths() })
    assert.equal(((await loaded.rpc("status")) as Status).host.state, "unsupported")
    const installed = (await loaded.rpc("installHost", {})) as { outcome: string; detail: string }
    assert.equal(installed.outcome, "refused")
    assert.match(installed.detail, /WSL/)
  })

  it("closed Blender: unknown without inspection, absent for an empty folder, outdated for an older manifest", async () => {
    const fixture = paths()
    assert.deepEqual(((await (await plugin({ paths: fixture })).rpc("status")) as Status).host, {
      state: "unknown",
      expectedRange: readPackage().opencodeIntegration.host?.range,
    })
    const empty = join(fixture.root, "extensions")
    mkdirSync(empty)
    const absent = (
      (await (await plugin({ paths: fixture, options: { hostInspectionDir: empty } })).rpc("status")) as Status
    ).host
    assert.deepEqual(absent, {
      state: "absent",
      expectedRange: readPackage().opencodeIntegration.host?.range,
      action: "install",
    })
    mkdirSync(join(empty, "opencode_link"))
    writeFileSync(join(empty, "opencode_link", "blender_manifest.toml"), 'id = "opencode_link"\nversion = "0.3.8"\n')
    const outdated = (
      (await (await plugin({ paths: fixture, options: { hostInspectionDir: empty } })).rpc("status")) as Status
    ).host
    assert.deepEqual(outdated, {
      state: "outdated",
      expectedRange: readPackage().opencodeIntegration.host?.range,
      partVersion: "0.3.8",
      action: "update",
    })
  })

  it("the default inspection folder is the newest Blender >= 5.3 user extensions repository", async () => {
    const fixture = paths()
    const root = join(fixture.env.XDG_CONFIG_HOME as string, "blender")
    for (const version of ["4.5", "5.2"]) mkdirSync(join(root, version), { recursive: true })
    assert.equal(((await (await plugin({ paths: fixture })).rpc("status")) as Status).host.state, "unknown")
    mkdirSync(join(root, "5.3"), { recursive: true })
    assert.equal(((await (await plugin({ paths: fixture })).rpc("status")) as Status).host.state, "absent")
  })

  it("loading never installs the host part", async () => {
    const fixture = paths()
    const dir = join(fixture.root, "extensions")
    mkdirSync(dir)
    await plugin({ paths: fixture, options: { hostInspectionDir: dir } })
    assert.deepEqual(readdirSync(dir), [])
  })
})

describe("offers and leases (AC-B8, AC-B15)", () => {
  it("an offer lives 15 s without renewal and a renewal at 5 s extends it", async () => {
    const time = clock()
    const loaded = await plugin({ now: time.now })
    const host = await fakeHost()
    const offer = announcement(host.endpoint, host.scriptHandle)
    assert.deepEqual(await loaded.rpc("advertise", offer), { outcome: "accepted" })
    time.advance(OFFER_LEASE_MS - 100)
    assert.equal(((await loaded.rpc("hosts")) as { offers: unknown[] }).offers.length, 1)
    time.advance(200)
    assert.equal(((await loaded.rpc("hosts")) as { offers: unknown[] }).offers.length, 0)

    assert.deepEqual(await loaded.rpc("advertise", offer), { outcome: "accepted" })
    time.advance(OFFER_RENEW_MS)
    assert.deepEqual(await loaded.rpc("advertise", offer), { outcome: "renewed" })
    time.advance(OFFER_LEASE_MS - 100)
    assert.equal(((await loaded.rpc("hosts")) as { offers: unknown[] }).offers.length, 1)
    time.advance(OFFER_RENEW_MS + 200)
    assert.equal(((await loaded.rpc("hosts")) as { offers: unknown[] }).offers.length, 0)
  })

  it("attachHost binds the session; a bound session is refused by a second host", async () => {
    const loaded = await plugin()
    const first = await fakeHost()
    const second = await fakeHost({ scriptHandle: "blend-second" })
    await loaded.rpc("advertise", announcement(first.endpoint, first.scriptHandle, "first"))
    await loaded.rpc("advertise", announcement(second.endpoint, second.scriptHandle, "second"))
    const offers = ((await loaded.rpc("hosts")) as { offers: Array<{ offerID: string }> }).offers
    assert.equal(offers.length, 2)
    const [a, b] = offers.map((offer) => offer.offerID) as [string, string]
    assert.deepEqual(await loaded.rpc("attachHost", { offerID: a, sessionID: "ses_one" }), { outcome: "attached" })
    assert.deepEqual(await loaded.rpc("attachHost", { offerID: a, sessionID: "ses_one" }), { outcome: "attached" })
    assert.deepEqual(await loaded.rpc("attachHost", { offerID: b, sessionID: "ses_one" }), { outcome: "busy" })
    assert.deepEqual(await loaded.rpc("attachHost", { offerID: "offer_unknown00", sessionID: "ses_one" }), {
      outcome: "gone",
    })
    assert.equal(((await loaded.rpc("status")) as Status).connection.state, "attached")
  })

  it("a lost withdraw still breaks the binding within 15 s; a withdraw breaks it at once", async () => {
    const time = clock()
    const loaded = await plugin({ now: time.now })
    const host = await fakeHost()
    await loaded.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
    const offerID = loaded.registry.hosts().offers[0]?.offerID as string
    loaded.registry.attach(offerID, "ses_one")
    time.advance(OFFER_LEASE_MS - 100)
    assert.ok(loaded.registry.binding("ses_one"))
    time.advance(200)
    assert.equal(loaded.registry.binding("ses_one"), null)

    await loaded.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
    loaded.registry.attach(loaded.registry.hosts().offers[0]?.offerID as string, "ses_one")
    loaded.events.splice(0)
    assert.deepEqual(await loaded.rpc("withdraw", { offer_key: "test-offer", token: "0".repeat(64) }), {
      outcome: "none",
    })
    assert.deepEqual(await loaded.rpc("withdraw", { offer_key: "test-offer", token: host.endpoint.token }), {
      outcome: "withdrawn",
    })
    assert.equal(loaded.registry.binding("ses_one"), null)
    assert.deepEqual(
      loaded.events.map((event) => event.name),
      ["hosts_changed", "status_changed"],
    )
  })
})

describe("advertisement refusals (AC-B14)", () => {
  it("refuses bad hosts, schema violations and failed hellos without creating an offer", async () => {
    const loaded = await plugin()
    const host = await fakeHost()
    const valid = announcement(host.endpoint, host.scriptHandle)
    const wrongToken = await fakeHost({ token: "a".repeat(64) })
    const otherProtocol = await fakeHost({ protocol: 2 })
    const held = new Set<net.Socket>()
    const silent = net.createServer((socket) => void held.add(socket))
    await new Promise<void>((resolve) => silent.listen(0, "127.0.0.1", () => resolve()))
    cleanups.push(() => {
      for (const socket of held) socket.destroy()
      return new Promise<void>((resolve) => silent.close(() => resolve()))
    })
    const closed = net.createServer()
    await new Promise<void>((resolve) => closed.listen(0, "127.0.0.1", () => resolve()))
    const closedPort = (closed.address() as net.AddressInfo).port
    await new Promise<void>((resolve) => closed.close(() => resolve()))

    const cases: Array<[string, unknown]> = [
      ["localhost", { ...valid, host: "localhost" }],
      ["any address", { ...valid, host: "0.0.0.0" }],
      ["extra field", { ...valid, extra: true }],
      ["short token", { ...valid, token: "abc" }],
      ["label with the port", { ...valid, label: `Blender ${host.endpoint.port}` }],
      ["protocol 2 announced", { ...valid, protocol: 2 }],
      ["wrong token", { ...valid, token: "b".repeat(64) }],
      ["other protocol in hello", announcement(otherProtocol.endpoint, otherProtocol.scriptHandle)],
      ["other script handle", { ...announcement(wrongToken.endpoint, "blend-other") }],
      ["closed port", { ...valid, port: closedPort }],
      ["hello timeout", { ...valid, port: (silent.address() as net.AddressInfo).port }],
      ["no real host", { ...valid, port: 1, token: "c".repeat(64) }],
    ]
    for (const [label, input] of cases) {
      assert.deepEqual(await loaded.rpc("advertise", input), { outcome: "refused" }, label)
    }
    assert.deepEqual(await loaded.rpc("hosts"), { offers: [] })
  })
})

describe("attachment (FAM-5)", () => {
  beforeEach(() => resetMethodForTests())

  async function offered(count = 1, answer: (action: string) => "allow" | "deny" = () => "allow") {
    const loaded = await plugin({ answer })
    const hosts = []
    for (let index = 0; index < count; index += 1) {
      const host = await fakeHost({
        scriptHandle: `blend-${index}`,
        methods: { scene: () => ({ revision: 0, objects: [] }) },
      })
      await loaded.rpc("advertise", announcement(host.endpoint, host.scriptHandle, `offer-${index}`))
      hosts.push(host)
    }
    return { loaded, hosts, offerIDs: loaded.registry.hosts().offers.map((offer) => offer.offerID) }
  }

  it("the only offered Blender nobody uses attaches itself at the first Blender call, without permission", async () => {
    const { loaded, hosts, offerIDs } = await offered()
    assert.equal(loaded.registry.binding("ses_test"), null)
    await loaded.tool("blender_context")
    assert.equal(loaded.registry.binding("ses_test")?.offerID, offerIDs[0])
    assert.deepEqual(loaded.permissions, [])
    assert.deepEqual(
      hosts[0]?.requests.map((request) => request.method),
      ["hello", "scene"],
    )
    // The method joins the conversation once.
    await loaded.tool("blender_context")
    assert.deepEqual(loaded.instructions, [{ sessionID: "ses_test", key: METHOD_KEY, value: METHOD_TEXT }])
    // Held by this conversation, it never attaches itself to another one.
    await assert.rejects(loaded.tool("blender_context", {}, "ses_one"), /attached to another conversation/)
    assert.equal(
      content(await loaded.tool("blender_hosts", {}, "ses_one")).blenders[0].attached,
      "another conversation",
    )
    assert.equal(loaded.registry.binding("ses_one"), null)
  })

  it("never attaches by itself with two offered Blenders or an incompatible one", async () => {
    const two = await offered(2)
    await assert.rejects(two.loaded.tool("blender_context"), /No Blender is attached to this conversation/)
    assert.equal(two.loaded.registry.binding("ses_test"), null)
    const loaded = await plugin()
    const old = await fakeHost({ appVersion: "5.2.3" })
    await loaded.rpc("advertise", announcement(old.endpoint, old.scriptHandle))
    await assert.rejects(loaded.tool("blender_context"), /incompatible add-on/)
    assert.equal(loaded.registry.binding("ses_test"), null)
    const none = await plugin()
    await assert.rejects(none.tool("blender_context"), /No Blender is offered/)
  })

  it("blender_hosts lists without permission; attach asks it; detach releases", async () => {
    let answer: "allow" | "deny" = "deny"
    const { loaded, offerIDs } = await offered(2, () => answer)
    const listed = content(await loaded.tool("blender_hosts"))
    assert.equal(listed.blenders.length, 2)
    assert.equal(listed.host.state, "ok")
    assert.equal(loaded.permissions.length, 0)
    await assert.rejects(loaded.tool("blender_hosts", { attach: offerIDs[1] }), /rejected/)
    assert.equal(loaded.registry.binding("ses_test"), null)
    answer = "allow"
    const attached = content(await loaded.tool("blender_hosts", { attach: offerIDs[1] }))
    assert.equal(attached.attached, "attached")
    assert.equal(
      attached.blenders.find((b: { offerID: string }) => b.offerID === offerIDs[1]).attached,
      "this conversation",
    )
    assert.deepEqual(
      loaded.permissions.map((request) => request.action),
      ["blender_attach", "blender_attach"],
    )
    // With two offered Blenders, another conversation only learns which one is its own.
    const other = content(await loaded.tool("blender_hosts", {}, "ses_one"))
    assert.equal(other.blenders.find((b: { offerID: string }) => b.offerID === offerIDs[1]).attached, undefined)
    assert.equal(content(await loaded.tool("blender_hosts", { detach: true })).detached, "detached")
    assert.equal(loaded.registry.binding("ses_test"), null)
    await assert.rejects(loaded.tool("blender_hosts", { attach: "offer_unknown00" }), /No offered Blender/)
  })
})

describe("tools", () => {
  beforeEach(() => resetMethodForTests())

  type Methods = Record<string, (params: Record<string, unknown>) => unknown>
  async function attached(
    methods: Methods = {},
    answer: (action: string) => "allow" | "deny" = () => "allow",
    revision = 0,
  ) {
    const loaded = await plugin({ answer })
    const host = await fakeHost({ methods, revision })
    await loaded.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
    loaded.registry.attach(loaded.registry.hosts().offers[0]?.offerID as string, "ses_test")
    return { loaded, host }
  }

  it("has ten tools and no skill", async () => {
    const loaded = await plugin()
    assert.deepEqual(loaded.toolNames(), [
      "blender_apply",
      "blender_context",
      "blender_execute_code",
      "blender_hosts",
      "blender_inspect",
      "blender_install_host",
      "blender_lab",
      "blender_render",
      "blender_save",
      "blender_verify",
    ])
    assert.equal(loaded.skills.size, 0)
  })

  it("blender_context reads the typed scene with the query", async () => {
    let received: Record<string, unknown> | undefined
    const { loaded } = await attached({
      scene: (params) => {
        received = params
        return { revision: 3, objects: [{ name: "Cube" }] }
      },
    })
    assert.equal(content(await loaded.tool("blender_context", { match: "C*" })).revision, 3)
    assert.deepEqual(received, { script_handle: "blend-test", match: "C*", limit: 50, offset: 0 })
  })

  it("blender_apply checks the batch, then sends it once with a command_id and the current revision (FAM-3)", async () => {
    const calls: Array<Record<string, unknown>> = []
    const { loaded } = await attached(
      {
        apply: (params) => {
          calls.push(params)
          return { status: "applied", revision: 8 }
        },
      },
      () => "allow",
      7,
    )
    await assert.rejects(
      loaded.tool("blender_apply", { operations: [{ op: "scene.create_object" }] }),
      /expected one of/,
    )
    await assert.rejects(
      loaded.tool("blender_apply", { operations: [{ op: "create_object", kind: "mesh", name: "A", body: "x" }] }),
      /body: applies to text only/,
    )
    await assert.rejects(
      loaded.tool("blender_apply", {
        operations: Array.from({ length: 51 }, () => ({ op: "set_camera", ref: "Cam" })),
      }),
      /1 to 50/,
    )
    assert.equal(calls.length, 0)
    const operations = [
      { op: "create_object", kind: "mesh", name: "A", as: "a" },
      { op: "set_parent", ref: "$a", parent: null },
    ]
    const first = content(
      await loaded.tool("blender_apply", { command_id: "c1", operations, expect: [{ ref: "B", exists: false }] }),
    )
    assert.equal(first.status, "applied")
    assert.deepEqual(calls[0], {
      script_handle: "blend-test",
      command_id: "c1",
      if_revision: 7,
      expect: [{ ref: "object:B", predicate: "absent" }],
      operations: [
        { op: "create_object", args: { kind: "mesh", name: "A", as: "a" } },
        { op: "set_parent", args: { ref: "$a" } },
      ],
    })
    // The same command_id answers the first result without a second batch.
    assert.equal(content(await loaded.tool("blender_apply", { command_id: "c1", operations })).replayed, true)
    await loaded.tool("blender_apply", { if_revision: 0, operations })
    assert.equal(calls.length, 2)
    assert.equal(calls[1]?.if_revision, 0, "revision 0 is a revision")
    assert.match(String(calls[1]?.command_id), /^c-/)
  })

  it("host refusals become explanations", async () => {
    const { loaded } = await attached({
      apply: () => {
        throw Object.assign(new Error("stale"), { code: "stale_revision" })
      },
    })
    await assert.rejects(
      loaded.tool("blender_apply", { if_revision: 0, operations: [{ op: "set_camera", ref: "Cam" }] }),
      /changed since your last read/,
    )
  })

  it("blender_execute_code asks permission on every run; a denied run reaches nothing (SEC-3)", async () => {
    let answer: "allow" | "deny" = "allow"
    const runs: Array<Record<string, unknown>> = []
    const { loaded } = await attached(
      {
        execute_code: (params) => {
          runs.push(params)
          return { status: "executed", result: { n: runs.length } }
        },
      },
      () => answer,
    )
    assert.equal(content(await loaded.tool("blender_execute_code", { code: "result = 1" })).result.n, 1)
    assert.equal(
      content(await loaded.tool("blender_execute_code", { code: "result = 2", read_only: true })).result.n,
      2,
    )
    answer = "deny"
    await assert.rejects(loaded.tool("blender_execute_code", { code: "result = 3" }), /rejected/)
    assert.equal(runs.length, 2)
    assert.equal(runs[1]?.read_only, true)
    assert.deepEqual(
      loaded.permissions.map((request) => request.action),
      ["blender_execute_code", "blender_execute_code", "blender_execute_code"],
    )
  })

  it("inspect, verify and save run their fixed programs read only, asking nothing", async () => {
    const sent: Array<Record<string, unknown>> = []
    const results = [
      { blender: "5.3.0", type: { type: "Strip" } },
      { blender: "5.3.0", facts: { image: { black: true } }, image_base64: "iVBORw0KGgo=" },
      { saved: "x.blend" },
    ]
    const { loaded } = await attached({
      execute_code: (params) => {
        sent.push(params)
        return { status: "executed", result: results[sent.length - 1] }
      },
    })
    assert.equal(content(await loaded.tool("blender_inspect", { type: 'Strip"); import os #' })).blender, "5.3.0")
    const verified = (await loaded.tool("blender_verify", { frame: 30 })).content as Array<{
      type: string
      text?: string
      uri?: string
    }>
    assert.equal(JSON.parse(verified[0]?.text ?? "{}").facts.image.black, true)
    assert.equal(verified[1]?.uri, "data:image/png;base64,iVBORw0KGgo=")
    assert.equal(content(await loaded.tool("blender_save", { path: "copy.blend", copy: true })).saved, "x.blend")
    for (const params of sent) {
      assert.equal(params.read_only, true)
      assert.match(String(params.code), /^import json\nPARAMS = json\.loads\("/)
      assert.ok(!/^import os #/m.test(String(params.code)), "parameters stay JSON data")
    }
    assert.match(String(sent[2]?.code), /save_as_mainfile/)
    assert.deepEqual(loaded.permissions, [])
    await assert.rejects(loaded.tool("blender_inspect", {}), /type, operator/)
  })

  it("blender_lab runs a Lab tool in Blender and returns its image as an attachment (FAM-4)", async () => {
    let received: Record<string, unknown> | undefined
    const { loaded } = await attached({
      lab_tool: (params) => {
        received = params
        return { status: "ok", result: { width: 8, image_base64: "iVBORw0KGgo=" } }
      },
    })
    const result = (
      await loaded.tool("blender_lab", {
        tool: "get_screenshot_of_window_as_image",
        args: { size_limit_in_bytes: 1000 },
      })
    ).content as Array<{ type: string; text?: string; uri?: string }>
    assert.deepEqual(received, {
      script_handle: "blend-test",
      tool: "get_screenshot_of_window_as_image",
      args: { size_limit_in_bytes: 1000 },
    })
    assert.ok(!result[0]?.text?.includes("iVBORw0KGgo="), "no base64 in the text")
    assert.equal(result[1]?.uri, "data:image/png;base64,iVBORw0KGgo=")
    await assert.rejects(loaded.tool("blender_lab", { tool: "execute_blender_code" }), /Expected one of/)
  })

  it("blender_render follows a background render to its end, and cancels one", async () => {
    const dir = mkdtempSync(join(tmpdir(), "blender-render-"))
    cleanups.push(() => void 0)
    const frames = [1, 2, 3].map((frame) => join(dir, `shot_${String(frame).padStart(4, "0")}.png`))
    const log = join(dir, "render.log")
    // A stand-in for the render Blender: writes its markers and frames, then finishes.
    const script = `const fs=require("fs");const log=${JSON.stringify(log)};const w=(t)=>fs.appendFileSync(log,t+"\\n");w('OPENCODE_RENDER {"engine":"CYCLES","size":[160,90]}');let i=0;const f=${JSON.stringify(frames)};const t=setInterval(()=>{if(i<f.length){w("Fra:"+(i+1)+" Mem:1M | Sample 4/16");fs.writeFileSync(f[i],"png");w("Time: 00:00.20 (Saving: 00:00.01)");i++}else{w('OPENCODE_DONE {"bytes":9}');clearInterval(t)}},150)`
    const child = spawn(process.execPath, ["-e", script], { stdio: "ignore" })
    const slow = spawn(process.execPath, ["-e", "setInterval(()=>{},1000)"], { stdio: "ignore" })
    cleanups.push(() => {
      slow.kill()
      child.kill()
    })
    let next = { pid: child.pid, log, frames }
    const { loaded } = await attached({
      execute_code: () => ({
        status: "executed",
        result: {
          ...next,
          work: dir,
          kind: "frames",
          output: join(dir, "shot_####.png"),
          targets: next.frames,
          start: 1,
          end: 3,
          scene: "Scene",
          source: "auto",
        },
      }),
    })
    const started = content(
      await loaded.tool("blender_render", { output: "shot_####.png", frame_start: 1, frame_end: 3 }),
    )
    assert.match(started.task, /^render-/)
    const done = content(await loaded.tool("blender_render", { task: started.task, wait_s: 10 }))
    assert.equal(done.state, "done")
    assert.equal(done.frames.done, 3)
    assert.equal(done.settings.engine, "CYCLES")
    next = { pid: slow.pid, log: join(dir, "slow.log"), frames: [join(dir, "never.png")] }
    const second = content(await loaded.tool("blender_render", { output: "never.png" }))
    assert.equal(second.state, "rendering")
    assert.equal(content(await loaded.tool("blender_render", { task: second.task, cancel: true })).state, "cancelled")
    await assert.rejects(loaded.tool("blender_render", {}), /output is required/)
    assert.deepEqual(loaded.permissions, [])
  })

  it("an add-on without the 0.4.0 methods is told to update", async () => {
    const { loaded } = await attached()
    const old = await fakeHost({
      hostVersion: "0.3.8",
      scriptHandle: "blend-old",
      capabilities: { ...CAPABILITIES, methods: ["hello", "context", "apply"] },
    })
    const other = await plugin()
    await other.rpc("advertise", announcement(old.endpoint, old.scriptHandle))
    other.registry.attach(other.registry.hosts().offers[0]?.offerID as string, "ses_test")
    await assert.rejects(other.tool("blender_context"), /no scene method: install OpenCode Link 0\.4\.0/)
    assert.ok(loaded)
  })
})

describe("legacy installation (AC-B16)", () => {
  it("a shim in the Global configuration produces legacy_install", async () => {
    const fixture = paths()
    assert.equal(((await (await plugin({ paths: fixture })).rpc("status")) as Status).notices, undefined)
    const plugins = join(fixture.env.XDG_CONFIG_HOME as string, "opencode", "plugins")
    mkdirSync(plugins, { recursive: true })
    writeFileSync(join(plugins, "blender.ts"), "export default {}\n")
    assert.deepEqual(((await (await plugin({ paths: fixture })).rpc("status")) as Status).notices, ["legacy_install"])
  })

  it("a listener answering the earlier bridge's ping produces legacy_install after hosts", async () => {
    const server = net.createServer((socket) => {
      socket.on("data", () =>
        socket.end(`${JSON.stringify({ status: "success", protocol: { name: "opencode-blender-bridge" } })}\n`),
      )
    })
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()))
    cleanups.push(() => new Promise<void>((resolve) => server.close(() => resolve())))
    const loaded = await plugin({ paths: paths(), legacyPort: (server.address() as net.AddressInfo).port })
    assert.equal(((await loaded.rpc("status")) as Status).notices, undefined, "status alone opens no socket")
    await loaded.rpc("hosts")
    await loaded.internals.legacy.refresh()
    assert.deepEqual(((await loaded.rpc("status")) as Status).notices, ["legacy_install"])
    assert.ok(loaded.events.some((event) => event.name === "status_changed"))
  })
})

describe("host installation (HST-1, RPC-3)", () => {
  it("installs, leaves an identical copy unchanged and updates an older one", async () => {
    const fixture = paths()
    const dir = join(fixture.root, "extensions")
    mkdirSync(dir)
    const loaded = await plugin({ paths: fixture, options: { hostInspectionDir: dir } })
    const installed = (await loaded.rpc("installHost", {})) as { outcome: string; detail: string }
    assert.equal(installed.outcome, "installed")
    assert.doesNotMatch(installed.detail, /[/\\]/)
    const manifest = readFileSync(join(dir, "opencode_link", "blender_manifest.toml"), "utf8")
    assert.match(manifest, new RegExp(`version = "${readPackage().version}"`))
    assert.ok(existsSync(join(dir, "opencode_link", "thin_plan.py")))
    assert.equal(((await loaded.rpc("installHost", {})) as { outcome: string }).outcome, "unchanged")
    writeFileSync(join(dir, "opencode_link", "blender_manifest.toml"), 'version = "0.0.1"\n')
    assert.equal(((await loaded.rpc("installHost", {})) as { outcome: string }).outcome, "updated")
    assert.deepEqual(readdirSync(dir), ["opencode_link"])
    assert.equal(((await loaded.rpc("status")) as Status).host.state, "unknown")
  })

  it("refuses without a Blender 5.3 user folder, and blender_install_host asks permission", async () => {
    const loaded = await plugin({ paths: paths() })
    const refused = (await loaded.rpc("installHost", {})) as { outcome: string; detail: string }
    assert.equal(refused.outcome, "refused")
    assert.match(refused.detail, /Blender 5\.3/)
    await loaded.tool("blender_install_host")
    assert.deepEqual(
      loaded.permissions.map((request) => request.action),
      ["blender_install_host"],
    )
  })
})
