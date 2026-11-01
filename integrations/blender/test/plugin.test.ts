// Plugin behaviour without Blender: status derivation, offers and leases with an injected clock,
// advertisement refusals, attachment permission, tools forwarding to a fake host, images as
// attachments, legacy detection, host installation, documentation and skills.

import assert from "node:assert/strict"
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs"
import net from "node:net"
import { join } from "node:path"
import { after, describe, it } from "node:test"

import { startFakeHost } from "../../../packages/conformance/src/index.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { OFFER_LEASE_MS, OFFER_RENEW_MS } from "../plugin/src/kit.ts"
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

async function fakeHost(options: Parameters<typeof startFakeHost>[0] = {}) {
  const host = await startFakeHost({ hostVersion: readPackage().version, scriptHandle: "blend-test", ...options })
  cleanups.push(() => host.close())
  return host
}

function paths() {
  const fixture = fixturePaths()
  cleanups.push(() => fixture.cleanup())
  return fixture
}

type Status = {
  host: { state: string; action?: string; partVersion?: string }
  connection: { state: string }
  notices?: string[]
}

describe("status.host.state (AC-B7, AC-B13)", () => {
  it("ok, outdated and incompatible come only from a live hello", async () => {
    for (const [options, state] of [
      [{ hostVersion: readPackage().version }, "ok"],
      [{ hostVersion: "0.0.9" }, "outdated"],
      [{ appVersion: "5.1.0" }, "incompatible"],
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
    writeFileSync(join(empty, "opencode_link", "blender_manifest.toml"), 'id = "opencode_link"\nversion = "0.0.5"\n')
    const outdated = (
      (await (await plugin({ paths: fixture, options: { hostInspectionDir: empty } })).rpc("status")) as Status
    ).host
    assert.deepEqual(outdated, {
      state: "outdated",
      expectedRange: readPackage().opencodeIntegration.host?.range,
      partVersion: "0.0.5",
      action: "update",
    })
  })

  it("the default inspection folder is the newest Blender >= 5.2 user extensions repository", async () => {
    const fixture = paths()
    const root = join(fixture.env.XDG_CONFIG_HOME as string, "blender")
    for (const version of ["4.5", "5.1", "5.2"]) mkdirSync(join(root, version), { recursive: true })
    const status = ((await (await plugin({ paths: fixture })).rpc("status")) as Status).host
    assert.equal(status.state, "absent")
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

describe("tools", () => {
  type Methods = Record<string, (params: Record<string, unknown>) => unknown>
  async function attached(methods: Methods = {}, answer: (action: string) => "allow" | "deny" = () => "allow") {
    const loaded = await plugin({ answer })
    const host = await fakeHost({ methods })
    await loaded.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
    const offerID = loaded.registry.hosts().offers[0]?.offerID as string
    return { loaded, host, offerID }
  }

  it("blender_attach asks permission before binding (AC-B9)", async () => {
    let answer: "allow" | "deny" = "deny"
    const { loaded, offerID } = await attached({}, () => answer)
    await assert.rejects(loaded.tool("blender_attach", { offerID }), /rejected/)
    assert.equal(loaded.registry.binding("ses_test"), null)
    assert.deepEqual(
      loaded.permissions.map((request) => request.action),
      ["blender_attach"],
    )
    answer = "allow"
    const result = await loaded.tool("blender_attach", { offerID })
    assert.match(String(result.content), /attached/)
    assert.ok(loaded.registry.binding("ses_test"))
    await loaded.tool("blender_hosts")
    assert.deepEqual(
      loaded.permissions.map((request) => request.action),
      ["blender_attach", "blender_attach", "blender_attach"],
    )
  })

  it("tools act on the attached host and captures come back as attachments (AC-B6)", async () => {
    const png = Buffer.from("89504e470d0a1a0a", "hex").toString("base64")
    let received: Record<string, unknown> | undefined
    const { loaded, offerID } = await attached({
      context: (params) => {
        received = params
        return {
          script_handle: "blend-test",
          revision: 3,
          mode: "OBJECT",
          results: [
            {
              name: "scene.get_viewport_screenshot",
              ok: true,
              result: { width: 8, height: 8, image: { mime: "image/png", data: png } },
            },
          ],
        }
      },
    })
    await assert.rejects(loaded.tool("blender_context"), /No verified application is attached/)
    loaded.registry.attach(offerID, "ses_test")
    const result = await loaded.tool("blender_context", {
      reads: [{ name: "scene.get_viewport_screenshot", args: { max_size: 64 } }],
    })
    assert.deepEqual(received, {
      script_handle: "blend-test",
      reads: [{ name: "scene.get_viewport_screenshot", args: { max_size: 64 } }],
    })
    const content = result.content as Array<{ type: string; text?: string; uri?: string; mime?: string }>
    assert.equal(content[0]?.type, "text")
    assert.ok(!content[0]?.text?.includes(png), "no base64 in the text")
    assert.match(content[0]?.text ?? "", /attachment scene\.get_viewport_screenshot-1\.png/)
    assert.deepEqual(content[1], {
      type: "file",
      uri: `data:image/png;base64,${png}`,
      mime: "image/png",
      name: "scene.get_viewport_screenshot-1.png",
    })
  })

  it("blender_apply validates operations before reaching the host (AC-B3)", async () => {
    const calls: unknown[] = []
    const { loaded, offerID } = await attached({
      apply: (params) => {
        calls.push(params)
        return { status: "applied" }
      },
    })
    loaded.registry.attach(offerID, "ses_test")
    const base = { script_handle: "blend-test", if_revision: 0, command_id: "c1" }
    const many = Array.from({ length: 51 }, () => ({ op: "scene.create_object", args: { primitive: "EMPTY" } }))
    await assert.rejects(loaded.tool("blender_apply", { ...base, operations: many }), /1 to 50/)
    await assert.rejects(
      loaded.tool("blender_apply", { ...base, operations: [{ op: "scene.nope" }] }),
      /unknown operation/,
    )
    await assert.rejects(
      loaded.tool("blender_apply", {
        ...base,
        operations: [{ op: "scene.create_object", args: { primitive: "PYRAMID" } }],
      }),
      /Expected one of/,
    )
    await assert.rejects(
      loaded.tool("blender_apply", {
        ...base,
        operations: [{ op: "scene.create_object" }, { op: "shot_manager.launch_batch_render" }],
      }),
      /only operation/,
    )
    assert.equal(calls.length, 0)
    await loaded.tool("blender_apply", {
      ...base,
      operations: [{ op: "addons.search_sketchfab_models", args: { query: "chair" } }],
    })
    assert.deepEqual(
      loaded.permissions.map((request) => request.action),
      ["blender_network"],
    )
    await loaded.tool("blender_apply", {
      ...base,
      command_id: "c2",
      operations: [{ op: "scene.create_object", args: { name: "A" } }],
    })
    assert.deepEqual((calls[1] as { operations: unknown }).operations, [
      { op: "scene.create_object", args: { name: "A", primitive: "CUBE" } },
    ])
  })

  it("host refusals become explanations", async () => {
    const { loaded, offerID } = await attached({
      apply: () => {
        throw Object.assign(new Error("stale"), { code: "stale_revision" })
      },
    })
    loaded.registry.attach(offerID, "ses_test")
    await assert.rejects(
      loaded.tool("blender_apply", {
        script_handle: "blend-test",
        if_revision: 0,
        command_id: "c",
        operations: [{ op: "scene.create_object" }],
      }),
      /changed since your last read/,
    )
  })

  it("blender_catalog lists every read and operation", async () => {
    const loaded = await plugin()
    const catalog = JSON.parse(String((await loaded.tool("blender_catalog")).content)) as {
      count: number
      entries: Array<{ kind: string }>
    }
    assert.equal(catalog.entries.filter((entry) => entry.kind === "read").length, 30)
    assert.equal(catalog.entries.filter((entry) => entry.kind === "operation").length, 49)
  })

  it("blender_docs answers the three documentation actions without Blender (AC-B5)", async () => {
    const loaded = await plugin()
    const api = JSON.parse(
      String((await loaded.tool("blender_docs", { action: "search_api", query: "bmesh" })).content),
    )
    assert.ok(api.count > 0)
    const manual = JSON.parse(
      String((await loaded.tool("blender_docs", { action: "search_manual", query: "modifier" })).content),
    )
    assert.ok(manual.count > 0)
    const page = JSON.parse(
      String((await loaded.tool("blender_docs", { action: "get_api", identifier: "bmesh.ops" })).content),
    )
    assert.ok(page.content.length > 100)
  })

  it("only the dangerous tool is hidden by default", async () => {
    const loaded = await plugin()
    assert.deepEqual(loaded.toolNames(), [
      "blender_apply",
      "blender_attach",
      "blender_audit",
      "blender_catalog",
      "blender_context",
      "blender_detach",
      "blender_docs",
      "blender_hosts",
      "blender_install_host",
      "blender_status",
    ])
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
    assert.ok(existsSync(join(dir, "opencode_link", "catalog.json")))
    assert.equal(((await loaded.rpc("installHost", {})) as { outcome: string }).outcome, "unchanged")
    writeFileSync(join(dir, "opencode_link", "blender_manifest.toml"), 'version = "0.0.1"\n')
    assert.equal(((await loaded.rpc("installHost", {})) as { outcome: string }).outcome, "updated")
    assert.deepEqual(readdirSync(dir), ["opencode_link"])
    assert.equal(((await loaded.rpc("status")) as Status).host.state, "unknown")
  })

  it("refuses without a Blender 5.2 user folder, and blender_install_host asks permission", async () => {
    const loaded = await plugin({ paths: paths() })
    const refused = (await loaded.rpc("installHost", {})) as { outcome: string; detail: string }
    assert.equal(refused.outcome, "refused")
    await loaded.tool("blender_install_host")
    assert.deepEqual(
      loaded.permissions.map((request) => request.action),
      ["blender_install_host"],
    )
  })
})

describe("skills (PKG-2)", () => {
  it("loads the eight blender-* skills", async () => {
    const loaded = await plugin()
    assert.deepEqual(
      [...loaded.skills.keys()],
      [
        "blender-animation",
        "blender-asset-browser",
        "blender-audit",
        "blender-docs",
        "blender-geometry-nodes",
        "blender-scene",
        "blender-shader-editor",
        "blender-shot-manager",
      ],
    )
    for (const skill of loaded.skills.values()) assert.ok(skill.content.trim().length > 0)
  })
})
