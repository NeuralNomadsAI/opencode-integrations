import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import net from "node:net"
import { join } from "node:path"
import { after, describe, it } from "node:test"

import {
  checkStatusOutput,
  hostValidator,
  rpcValidator,
  runSentinelScenario,
  startFakeHost,
} from "../../conformance/src/index.ts"
import {
  appVersionSatisfies,
  capabilityHash,
  comparePartVersion,
  createIntegrationRpc,
  createOfferRegistry,
  deriveHostState,
  type HostError,
  type HostExpectation,
  type IntegrationMarker,
  isWslDaemon,
  MAX_OFFERS,
  offerRegistryKey,
  parseAdvertiseInput,
  parseHello,
  parseWithdrawInput,
  RPC_SCHEMAS,
  requestHost,
  rpcDefinition,
  sharedOfferRegistry,
} from "../src/index.ts"

const spec = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "..", "spec", "schemas", "rpc.schema.json"), "utf8"),
) as { $defs: Record<string, unknown> }

const expected: HostExpectation = { protocol: 1, range: "^0.1.0", appVersions: ">=5.2" }

const marker: IntegrationMarker = {
  spec: 0,
  id: "demo",
  displayName: "OpenCode for Demo",
  family: "native",
  plugin: "neuralnomads.demo",
  rpc: "neuralnomads.demo",
  host: {
    app: "demo",
    protocol: 1,
    range: "^0.1.0",
    appVersions: ">=5.2",
    payload: "host/demo/part-0.1.0.zip",
    sha256: "0".repeat(64),
  },
}

const hosts: Array<{ close(): Promise<void> }> = []
after(async () => {
  for (const host of hosts) await host.close()
})

async function fakeHost(options: Parameters<typeof startFakeHost>[0] = {}) {
  const host = await startFakeHost(options)
  hosts.push(host)
  return host
}

function offerOf(host: Awaited<ReturnType<typeof startFakeHost>>, overrides: Record<string, unknown> = {}) {
  return {
    host: "127.0.0.1",
    port: host.endpoint.port,
    token: host.endpoint.token,
    script_handle: host.scriptHandle,
    label: "scene.blend",
    protocol: 1,
    offer_key: "process-1",
    ...overrides,
  }
}

function clock(start = 1_000_000) {
  let now = start
  return { now: () => now, advance: (ms: number) => (now += ms) }
}

describe("contract schemas", () => {
  it("registers wire schemas equal to spec/schemas/rpc.schema.json", () => {
    for (const [name, schema] of Object.entries(RPC_SCHEMAS)) {
      assert.deepEqual(schema, spec.$defs[name], name)
    }
    const covered = new Set([...Object.keys(RPC_SCHEMAS), "advertise.input", "withdraw.input"])
    assert.deepEqual(
      Object.keys(spec.$defs).filter((name) => !covered.has(name)),
      [],
    )
  })

  it("defines status only for vendor-mcp and api, the whole contract for native", () => {
    assert.deepEqual(Object.keys(rpcDefinition("neuralnomads.demo", "api").methods), ["status"])
    const native = rpcDefinition("neuralnomads.demo", "native")
    assert.deepEqual(Object.keys(native.methods).sort(), [
      "advertise",
      "attachHost",
      "detachHost",
      "hosts",
      "installHost",
      "status",
      "withdraw",
    ])
    assert.deepEqual(Object.keys(native.events).sort(), ["hosts_changed", "status_changed"])
    assert.deepEqual(native.methods.advertise?.input, { type: "object" })
  })

  it("registers the schemas without `pattern`, which OpenCode 2.2.5 refuses to compile", () => {
    const native = rpcDefinition("neuralnomads.demo", "native")
    assert.doesNotMatch(JSON.stringify(native), /"pattern"/)
    assert.match(JSON.stringify(RPC_SCHEMAS), /"pattern"/, "the contract schemas keep their patterns")
    assert.deepEqual(native.methods.status?.output.required, ["contract", "integration"])
  })

  it("validates advertise and withdraw exactly like the JSON Schema", () => {
    const good = {
      host: "127.0.0.1",
      port: 50000,
      token: "a".repeat(64),
      script_handle: "blend:1",
      label: "scene.blend",
      protocol: 1,
      offer_key: "k-1",
    }
    const advertiseCorpus: unknown[] = [
      good,
      { ...good, host: "localhost" },
      { ...good, host: "::1" },
      { ...good, port: 0 },
      { ...good, port: 65536 },
      { ...good, port: 1.5 },
      { ...good, token: "A".repeat(64) },
      { ...good, token: "a".repeat(63) },
      { ...good, label: "" },
      { ...good, label: "x".repeat(120) },
      { ...good, label: "x".repeat(121) },
      { ...good, label: "dir/scene.blend" },
      { ...good, label: "C:\\scene.blend" },
      { ...good, label: "line\nbreak" },
      { ...good, label: "é".repeat(120) },
      { ...good, script_handle: "x".repeat(201) },
      { ...good, offer_key: "x".repeat(65) },
      { ...good, offer_key: "bad key" },
      { ...good, protocol: 0 },
      { ...good, extra: true },
      (({ label: _, ...rest }) => rest)(good),
      null,
      [],
      "advertise",
    ]
    const advertise = rpcValidator("advertise.input")
    for (const value of advertiseCorpus) {
      assert.equal(parseAdvertiseInput(value) !== null, advertise(value), JSON.stringify(value))
    }
    const withdraw = rpcValidator("withdraw.input")
    for (const value of [
      { offer_key: "k", token: "b".repeat(64) },
      { offer_key: "k", token: "b".repeat(64), extra: 1 },
      { offer_key: "", token: "b".repeat(64) },
      { offer_key: "k" },
      {},
    ]) {
      assert.equal(parseWithdrawInput(value) !== null, withdraw(value), JSON.stringify(value))
    }
  })

  it("validates hello like host.schema.json and checks its capability hash", async () => {
    const host = await fakeHost({ document: "scene.blend" })
    const hello = host.hello
    const validate = hostValidator("hello")
    const corpus: unknown[] = [
      hello,
      { ...hello, protocol: 0 },
      { ...hello, host_version: "0.1" },
      { ...hello, app_version: "" },
      { ...hello, revision: -1 },
      { ...hello, document: "dir/scene.blend" },
      { ...hello, capability_hash: "f".repeat(63) },
      { ...hello, capabilities: { ...(hello.capabilities as object), undo: "maybe" } },
      { ...hello, capabilities: { ...(hello.capabilities as object), extra: 1 } },
      { ...hello, script_handle: "\u{1F600}".repeat(200) },
      { ...hello, script_handle: "\u{1F600}".repeat(201) },
    ]
    for (const value of corpus) assert.equal(parseHello(value) !== null, validate(value), JSON.stringify(value))
    assert.equal(parseHello({ ...hello, capability_hash: "f".repeat(64) }), null)
    assert.equal(capabilityHash(hello.capabilities as never), hello.capability_hash)
  })
})

describe("offer registry", () => {
  it("accepts a validated offer, renews it and expires it after 15 s", async () => {
    const host = await fakeHost()
    const time = clock()
    const registry = createOfferRegistry({ expected, now: time.now })
    const changes: string[] = []
    registry.onChange((change) => changes.push(change))
    assert.deepEqual(await registry.advertise(offerOf(host)), { outcome: "accepted" })
    assert.deepEqual(changes, ["hosts", "status"])
    assert.equal(registry.hosts().offers.length, 1)
    time.advance(14_900)
    assert.equal(registry.hosts().offers.length, 1)
    time.advance(200)
    assert.equal(registry.hosts().offers.length, 0)
    assert.deepEqual(changes.slice(2), ["hosts", "status"])

    assert.deepEqual(await registry.advertise(offerOf(host)), { outcome: "accepted" })
    time.advance(5_000)
    assert.deepEqual(await registry.advertise(offerOf(host)), { outcome: "renewed" })
    time.advance(14_900)
    assert.equal(registry.hosts().offers.length, 1, "the renewal at 5 s extends the lease")
    time.advance(200)
    assert.equal(registry.hosts().offers.length, 0)
  })

  it("refuses non-loopback hosts, schema violations and failed hellos without creating offers", async () => {
    const host = await fakeHost()
    const registry = createOfferRegistry({ expected })
    const refused = { outcome: "refused" }
    assert.deepEqual(await registry.advertise(offerOf(host, { host: "localhost" })), refused)
    assert.deepEqual(await registry.advertise(offerOf(host, { host: "::1" })), refused)
    assert.deepEqual(await registry.advertise(offerOf(host, { label: "x".repeat(121) })), refused)
    assert.deepEqual(await registry.advertise(offerOf(host, { token: "c".repeat(64) })), refused, "wrong token")
    assert.deepEqual(await registry.advertise(offerOf(host, { protocol: 2 })), refused, "other protocol")
    assert.deepEqual(await registry.advertise(offerOf(host, { script_handle: "other" })), refused, "other document")
    assert.deepEqual(await registry.advertise(offerOf(host, { label: `x ${host.endpoint.token}` })), refused)
    const closed = await fakeHost()
    const port = closed.endpoint.port
    await closed.close()
    assert.deepEqual(await registry.advertise(offerOf(host, { port })), refused, "closed port")
    const otherProtocol = await fakeHost({ protocol: 2 })
    assert.deepEqual(await registry.advertise(offerOf(otherProtocol)), refused)
    const badHash = await fakeHost({ helloOverride: (hello) => ({ ...hello, capability_hash: "0".repeat(64) }) })
    assert.deepEqual(await registry.advertise(offerOf(badHash)), refused)
    assert.deepEqual(registry.hosts(), { offers: [] })
  })

  it("refuses an advertise whose hello does not answer within 2 s", async () => {
    // Reads and drops the request without ever answering.
    const silent = net.createServer((socket) => socket.resume())
    await new Promise<void>((resolve) => silent.listen(0, "127.0.0.1", () => resolve()))
    hosts.push({ close: () => new Promise((resolve) => silent.close(() => resolve())) })
    const port = (silent.address() as net.AddressInfo).port
    const registry = createOfferRegistry({ expected })
    const started = Date.now()
    const offer = { ...offerOf(await fakeHost()), port }
    assert.deepEqual(await registry.advertise(offer), { outcome: "refused" })
    assert.ok(Date.now() - started < 4_000)
    assert.deepEqual(registry.hosts(), { offers: [] })
  })

  it("attaches one session per live host, reports busy and gone, and breaks bindings on withdraw", async () => {
    const first = await fakeHost({ document: "scene.blend" })
    const second = await fakeHost()
    const registry = createOfferRegistry({ expected })
    await registry.advertise(offerOf(first))
    await registry.advertise(offerOf(second, { offer_key: "process-2", label: "other.blend" }))
    const [a, b] = registry.hosts().offers
    assert.ok(a && b)
    assert.equal(a.document, "scene.blend")
    assert.deepEqual(registry.attach(a.offerID, "ses_1"), { outcome: "attached" })
    assert.deepEqual(registry.attach(a.offerID, "ses_1"), { outcome: "attached" })
    assert.deepEqual(registry.attach(b.offerID, "ses_1"), { outcome: "busy" })
    assert.deepEqual(registry.attach("offer_missing0", "ses_2"), { outcome: "gone" })
    assert.equal(registry.binding("ses_1")?.endpoint.port, first.endpoint.port)
    assert.deepEqual(registry.counts(), { offers: 2, attachedSessions: 1 })
    assert.deepEqual(registry.withdraw({ offer_key: "process-1", token: second.endpoint.token }), { outcome: "none" })
    assert.deepEqual(registry.withdraw({ offer_key: "process-1", token: first.endpoint.token }), {
      outcome: "withdrawn",
    })
    assert.equal(registry.binding("ses_1"), null)
    assert.deepEqual(registry.detach("ses_1"), { outcome: "none" })
  })

  it("replaces an offer of the same key only after a successful hello", async () => {
    const first = await fakeHost()
    const restarted = await fakeHost()
    const registry = createOfferRegistry({ expected })
    await registry.advertise(offerOf(first))
    const [original] = registry.hosts().offers
    assert.ok(original)
    registry.attach(original.offerID, "ses_1")
    const bogus = offerOf(restarted, { token: "d".repeat(64) })
    assert.deepEqual(await registry.advertise(bogus), { outcome: "refused" })
    assert.equal(registry.hosts().offers[0]?.offerID, original.offerID, "a failed replacement keeps the offer")
    assert.deepEqual(await registry.advertise(offerOf(restarted)), { outcome: "accepted" })
    assert.notEqual(registry.hosts().offers[0]?.offerID, original.offerID)
    assert.equal(registry.binding("ses_1"), null, "the replaced offer's bindings are broken")
  })

  it("drops hello display fields that reveal the script handle or the port", async () => {
    const host = await fakeHost({
      scriptHandle: "scene.blend",
      document: "scene.blend",
      helloOverride: (hello, endpoint) => ({ ...hello, app_version: `5.2.0 build ${endpoint.port}` }),
    })
    const registry = createOfferRegistry({ expected })
    assert.deepEqual(await registry.advertise(offerOf(host, { label: "Blender session" })), { outcome: "accepted" })
    const [offer] = registry.hosts().offers
    assert.equal(offer?.document, undefined)
    assert.equal(offer?.appVersion, undefined)
    assert.deepEqual(registry.liveHellos(), [
      { compatibility: "ok", partVersion: "0.1.0", appVersion: undefined, methods: ["hello", "context", "apply"] },
    ])
  })

  it("holds at most 16 offers and marks incompatible hosts", async () => {
    const registry = createOfferRegistry({ expected })
    const host = await fakeHost()
    for (let index = 0; index < MAX_OFFERS; index++) {
      assert.deepEqual(await registry.advertise(offerOf(host, { offer_key: `k${index}` })), { outcome: "accepted" })
    }
    assert.deepEqual(await registry.advertise(offerOf(host, { offer_key: "k16" })), { outcome: "refused" })

    const old = await fakeHost({ appVersion: "5.1.0" })
    const strict = createOfferRegistry({ expected })
    await strict.advertise(offerOf(old))
    const [offer] = strict.hosts().offers
    assert.equal(offer?.protocol, "incompatible")
    assert.deepEqual(strict.attach(offer?.offerID ?? "", "ses_1"), { outcome: "refused" })
  })

  it("is shared by every module instance through Symbol.for", () => {
    const key = offerRegistryKey("demo-shared")
    assert.equal(key, Symbol.for("neuralnomads.demo-shared.offers"))
    const one = sharedOfferRegistry("demo-shared", { expected })
    const two = sharedOfferRegistry("demo-shared", { expected })
    assert.equal(one, two)
    assert.ok((globalThis as unknown as Record<symbol, unknown>)[key])
  })
})

describe("host client and versions", () => {
  it("reports only error codes of the protocol and checks the envelope ID", async () => {
    const host = await fakeHost({
      methods: {
        leak: () => {
          throw Object.assign(new Error("x"), { code: "D:\\secret\\path" })
        },
        busy: () => {
          throw Object.assign(new Error("x"), { code: "busy" })
        },
      },
    })
    await assert.rejects(requestHost(host.endpoint, "leak"), (error: HostError) => {
      assert.equal(error.code, "internal")
      assert.doesNotMatch(error.message, /secret/)
      return true
    })
    await assert.rejects(requestHost(host.endpoint, "busy"), (error: HostError) => error.code === "busy")
    await assert.rejects(requestHost({ ...host.endpoint, host: "localhost" }, "hello"), /127.0.0.1 only/)
    const wrongID = net.createServer((socket) => {
      socket.resume()
      socket.end(`${JSON.stringify({ id: "other", ok: false, error: { code: "busy" } })}\n`)
    })
    await new Promise<void>((resolve) => wrongID.listen(0, "127.0.0.1", () => resolve()))
    hosts.push({ close: () => new Promise((resolve) => wrongID.close(() => resolve())) })
    const port = (wrongID.address() as net.AddressInfo).port
    await assert.rejects(requestHost({ ...host.endpoint, port }, "hello"), (error: HostError) => {
      return error.code === "invalid_response"
    })
  })

  it("counts prereleases inside a range and compares application versions by their numbers", () => {
    assert.equal(comparePartVersion("0.1.1-beta.1", "^0.1.0"), "ok")
    assert.equal(comparePartVersion("0.0.9", "^0.1.0"), "outdated")
    assert.equal(comparePartVersion("0.2.0", "^0.1.0"), "incompatible")
    assert.equal(comparePartVersion("0.2.0-rc.1", "^0.1.0"), "incompatible")
    assert.equal(appVersionSatisfies("5.2.0 LTS Beta", ">=5.2"), true)
    assert.equal(appVersionSatisfies("5.1.3", ">=5.2"), false)
  })
})

describe("host state", () => {
  const range = "^0.1.0"
  const live = (compatibility: "ok" | "outdated" | "incompatible", partVersion = "0.1.0", appVersion = "5.2.0") => ({
    compatibility,
    partVersion,
    appVersion,
  })

  it("follows the normative derivation order", () => {
    const notInspected = { kind: "not-inspected" } as const
    assert.equal(
      deriveHostState({ unsupported: true, live: [live("ok")], inspection: notInspected, range }).state,
      "unsupported",
    )
    assert.deepEqual(deriveHostState({ unsupported: false, live: [live("ok")], inspection: notInspected, range }), {
      state: "ok",
      expectedRange: range,
      partVersion: "0.1.0",
      appVersion: "5.2.0",
    })
    const worst = deriveHostState({
      unsupported: false,
      live: [live("ok"), live("incompatible", "0.1.0", "5.1.0"), live("outdated", "0.0.9")],
      inspection: notInspected,
      range,
    })
    assert.equal(worst.state, "incompatible")
    assert.equal(worst.appVersion, "5.1.0")
    assert.equal(
      deriveHostState({ unsupported: false, live: [live("outdated", "0.0.9")], inspection: notInspected, range })
        .action,
      "update",
    )
    assert.deepEqual(deriveHostState({ unsupported: false, live: [], inspection: notInspected, range }), {
      state: "unknown",
      expectedRange: range,
    })
    assert.deepEqual(deriveHostState({ unsupported: false, live: [], inspection: { kind: "not-found" }, range }), {
      state: "absent",
      expectedRange: range,
      action: "install",
    })
    assert.deepEqual(
      deriveHostState({ unsupported: false, live: [], inspection: { kind: "found", version: "0.0.9" }, range }),
      { state: "outdated", expectedRange: range, partVersion: "0.0.9", action: "update" },
    )
    assert.deepEqual(
      deriveHostState({ unsupported: false, live: [], inspection: { kind: "found", version: "1.0.0" }, range }),
      { state: "incompatible", expectedRange: range, partVersion: "1.0.0" },
    )
    const closed = deriveHostState({
      unsupported: false,
      live: [],
      inspection: { kind: "found", version: "0.1.2" },
      range,
    })
    assert.equal(closed.state, "unknown")
    assert.equal(closed.action, undefined)
  })

  it("detects a daemon running in WSL", () => {
    const linux = (env: NodeJS.ProcessEnv, procVersion: string | null) => ({
      platform: "linux" as const,
      env,
      procVersion: () => procVersion,
    })
    assert.equal(isWslDaemon(linux({}, "Linux version 6.6.36.6-microsoft-standard-WSL2")), true)
    assert.equal(isWslDaemon(linux({ WSL_DISTRO_NAME: "Ubuntu" }, null)), true)
    assert.equal(isWslDaemon(linux({}, "Linux version 6.8.0-generic")), false)
    assert.equal(isWslDaemon({ platform: "win32", env: { WSL_DISTRO_NAME: "x" }, procVersion: () => null }), false)
  })
})

describe("integration RPC", () => {
  function nativeRpc(options: { wsl?: boolean } = {}) {
    const events: Array<{ name: string; data: unknown }> = []
    const registry = createOfferRegistry({ expected })
    const rpc = createIntegrationRpc({
      marker,
      version: "0.1.0",
      registry,
      wsl: () => options.wsl ?? false,
      inspectHost: async () => ({ kind: "not-found" }),
      installHost: async () => ({ outcome: "installed", detail: "Installed into D:\\Apps\\addons" }),
      notices: async () => ["legacy_install", "legacy_install"],
    })
    const disconnect = rpc.connect({
      events: {
        emit: async (name, data) => {
          events.push({ name, data })
        },
      },
    })
    after(disconnect)
    const call = (method: string, input: unknown) => {
      const handler = rpc.handlers[method]
      if (!handler) throw new Error(`no handler ${method}`)
      return handler(input)
    }
    return { call, events, rpc }
  }

  it("never returns a token, port, script handle or offer key (sentinel scenario)", async () => {
    const target = nativeRpc()
    const report = await runSentinelScenario(target)
    assert.deepEqual(report.errors, [])
    assert.ok(target.events.some((event) => event.name === "hosts_changed"))
  })

  it("reports status like the marker and bounds installHost details", async () => {
    const { call } = nativeRpc()
    const status = await call("status", {})
    assert.deepEqual(checkStatusOutput(status, marker, "0.1.0"), [])
    assert.deepEqual(status, {
      contract: 1,
      integration: { id: "demo", version: "0.1.0", family: "native", spec: 0 },
      host: { state: "absent", expectedRange: "^0.1.0", action: "install" },
      connection: { state: "none", offers: 0, attachedSessions: 0 },
      notices: ["legacy_install"],
    })
    const installed = (await call("installHost", {})) as { outcome: string; detail?: string }
    assert.equal(installed.outcome, "installed")
    assert.doesNotMatch(installed.detail ?? "", /[/\\]/)
  })

  it("reports unsupported and refuses installHost under WSL", async () => {
    const { call } = nativeRpc({ wsl: true })
    const status = (await call("status", {})) as { host: { state: string; action?: string } }
    assert.equal(status.host.state, "unsupported")
    assert.equal(status.host.action, undefined)
    const refused = (await call("installHost", {})) as { outcome: string; detail: string }
    assert.equal(refused.outcome, "refused")
    assert.match(refused.detail, /WSL/)
  })

  it("serves status only for the api family", async () => {
    const api = createIntegrationRpc({
      marker: { ...marker, family: "api", host: undefined },
      version: "0.1.0",
      connection: async () => ({ state: "signed_out" }),
    })
    assert.deepEqual(Object.keys(api.handlers), ["status"])
    const status = await api.handlers.status?.({})
    assert.deepEqual(checkStatusOutput(status, { spec: 0, id: "demo", family: "api" }, "0.1.0"), [])
  })
})
