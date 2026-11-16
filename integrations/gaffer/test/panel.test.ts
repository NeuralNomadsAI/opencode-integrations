import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { cpSync, mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"
import { build } from "esbuild"
import { type Api, createPanel, SCRIPT_MARKER } from "../plugin/src/sidecar-core.ts"

const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
const KEY = "document:shot"
const HOST = { host: "127.0.0.1", port: 4242, token: "a".repeat(64) }
const CONNECT = {
  ...HOST,
  protocol: 1,
  script_handle: "gaffer:shot",
  offer_label: "Gaffer: shot.gfr",
  script_key: KEY,
  script_name: "shot.gfr",
  directory: join(tmpdir(), "gaffer-shots"),
}

it("the installed panel runs its sidecar with Node: payload layout, Gaffer stubs, no Bun", async () => {
  const payload = mkdtempSync(join(tmpdir(), "gaffer-panel-"))
  try {
    cpSync(join(import.meta.dirname, "..", "host", "python"), join(payload, "python"), { recursive: true })
    // As the host build makes it: one CommonJS file for Node 22 at the payload root.
    await build({
      entryPoints: [join(import.meta.dirname, "..", "plugin", "src", "sidecar.ts")],
      bundle: true,
      format: "cjs",
      platform: "node",
      target: "node22",
      outfile: join(payload, "sidecar.cjs"),
      logLevel: "warning",
    })
    const sidecar = spawnSync(process.execPath, [join(payload, "sidecar.cjs")], {
      input: "not json\n",
      encoding: "utf8",
      windowsHide: true,
      timeout: 20_000,
      // Never discover the OpenCode service of the computer running the test.
      env: { ...process.env, XDG_STATE_HOME: join(payload, "state") },
    })
    assert.equal(sidecar.status, 0, sidecar.stderr)
    assert.deepEqual(JSON.parse(sidecar.stdout), { error: { message: "Invalid panel request." } })
    const probe = spawnSync(python, ["-I", join(import.meta.dirname, "panel_probe.py"), payload], {
      encoding: "utf8",
      windowsHide: true,
    })
    assert.equal(probe.status, 0, probe.stderr)
  } finally {
    rmSync(payload, { recursive: true, force: true })
  }
})

type Item = { type: string; data: Record<string, unknown> }
type Input = { offerID: string; sessionID: string }
type Out = { id?: string; event?: string; error?: unknown; data: Record<string, unknown> & { message?: string } }

function fakeService() {
  const calls: Array<[string, unknown]> = []
  const bindings = new Map<string, string>()
  const listeners = new Set<(item: Item) => void>()
  let offers = [
    { offerID: "offer_other", label: "Gaffer: other.gfr", protocol: "ok" },
    { offerID: "offer_shot", label: "Gaffer: shot.gfr", protocol: "ok" },
  ]
  let created = 0
  // The first calls of a Location OpenCode is still opening: the integration's RPC is not there yet.
  let unavailable = 0
  const record =
    (name: string, result: (input: Input) => unknown = () => ({})) =>
    async (input: Input) => {
      calls.push([name, input])
      return result(input)
    }
  const rpc = new Proxy({} as Record<string, unknown>, {
    get: (_target, name: string) => {
      const methods: Record<string, (input: Input) => unknown> = {
        hosts: () => {
          if (unavailable > 0) {
            unavailable -= 1
            throw new Error("RPC is unavailable: neuralnomads.gaffer")
          }
          return { offers }
        },
        attachHost: ({ offerID, sessionID }) => {
          const bound = bindings.get(sessionID)
          if (bound && bound !== offerID) return { outcome: "busy" }
          bindings.set(sessionID, offerID)
          return { outcome: "attached" }
        },
        detachHost: ({ sessionID }) => ({ outcome: bindings.delete(sessionID) ? "detached" : "none" }),
        bindingRead: ({ sessionID }) =>
          bindings.has(sessionID)
            ? { state: "attached", binding: { offerID: bindings.get(sessionID), label: "x" } }
            : { state: "none" },
      }
      return record(`rpc.${name}`, methods[name] ?? (() => ({ outcome: "refused" })))
    },
  })
  const api = {
    rpc: () => rpc,
    session: {
      list: record("session.list", () => ({
        data: [
          { id: "ses_archived", metadata: { [SCRIPT_MARKER]: KEY }, time: { archived: 1 } },
          { id: "ses_foreign", metadata: { [SCRIPT_MARKER]: "document:other" }, time: {} },
          { id: "ses_old", metadata: { [SCRIPT_MARKER]: KEY }, time: {} },
        ],
        cursor: {},
      })),
      create: record("session.create", () => ({ id: `ses_new${++created}` })),
      prompt: record("session.prompt"),
      interrupt: record("session.interrupt"),
      switchModel: record("session.switchModel"),
      active: record("session.active", () => ({})),
    },
    message: {
      list: record("message.list", () => ({
        data: [
          { type: "assistant", content: [{ type: "text", text: "Done." }] },
          { type: "user", text: "Add a sphere" },
        ],
      })),
    },
    model: {
      list: record("model.list", () => ({
        data: [
          { id: "m1", providerID: "p", name: "M1", enabled: true },
          { id: "m2", providerID: "p", name: "M2", enabled: false },
        ],
      })),
      default: record("model.default", () => ({ data: { id: "m1", providerID: "p" } })),
    },
    permission: { reply: record("permission.reply") },
    event: {
      subscribe: ({ signal }: { signal: AbortSignal }) =>
        (async function* () {
          const queue: Item[] = []
          let wake = () => {}
          const listener = (item: Item) => {
            queue.push(item)
            wake()
          }
          listeners.add(listener)
          signal.addEventListener("abort", () => wake(), { once: true })
          try {
            while (!signal.aborted) {
              const item = queue.shift()
              if (item) yield item
              else
                await new Promise<void>((resolve) => {
                  wake = resolve
                })
            }
          } finally {
            listeners.delete(listener)
          }
        })(),
    },
  }
  return {
    api: api as unknown as Api,
    calls,
    bindings,
    setOffers: (next: typeof offers) => {
      offers = next
    },
    setUnavailable: (calls: number) => {
      unavailable = calls
    },
    push: async (item: Item) => {
      for (const listener of listeners) listener(item)
      await new Promise((resolve) => setImmediate(resolve))
    },
  }
}

function harness(offerWaitMs = -1) {
  const service = fakeService()
  const out: Out[] = []
  const host: Array<[string, unknown]> = []
  const panel = createPanel({
    discover: async () => service.api,
    send: (message) => out.push(message as Out),
    requestHost: async (_endpoint, method, params) => {
      host.push([method, params])
      return {}
    },
    offerWaitMs,
    checkEveryMs: 60_000,
  })
  let id = 0
  const call = (method: string, params: Record<string, unknown> = {}, channel = "panel") =>
    panel.dispatch({ id: String(++id), channel, method, params })
  const events = (name: string) => out.filter((message) => message.event === name).map((message) => message.data)
  return { service, out, host, panel, call, events }
}

it("Connect resumes the script's marked conversation and attaches it to that script's offer only", async () => {
  const { service, host, panel, call, events } = harness()
  try {
    await call("connect", CONNECT)
    assert.equal(service.calls.filter(([name]) => name === "session.create").length, 0)
    assert.deepEqual(service.bindings, new Map([["ses_old", "offer_shot"]]))
    assert.deepEqual(events("session")[0], {
      id: "ses_old",
      resumed: true,
      model: "p/m1",
      workspace: CONNECT.directory,
    })
    assert.deepEqual(events("history")[0]?.messages, [
      { role: "user", text: "Add a sphere" },
      { role: "assistant", text: "Done." },
    ])
    assert.deepEqual(events("models")[0], { selected: "p/m1", models: [{ id: "m1", provider_id: "p", name: "M1" }] })
    assert.deepEqual(events("status").at(-1), { state: "idle" })

    // Streaming: this conversation's events only.
    await service.push({ type: "session.text.delta", data: { sessionID: "ses_other", delta: "no" } })
    await service.push({ type: "session.execution.started", data: { sessionID: "ses_old" } })
    await service.push({ type: "session.text.delta", data: { sessionID: "ses_old", delta: "Hel" } })
    await service.push({ type: "session.text.delta", data: { sessionID: "ses_old", delta: "lo" } })
    await service.push({
      type: "permission.asked",
      data: { sessionID: "ses_old", id: "per_1", action: "gaffer_write" },
    })
    assert.deepEqual(events("assistant_delta"), [{ text: "Hel" }, { text: "lo" }])
    assert.deepEqual(events("status").at(-1), { state: "busy" })
    assert.deepEqual(events("permission"), [{ request_id: "per_1", action: "gaffer_write", resources: [] }])

    await call("permission_reply", { request_id: "per_1", reply: "once" })
    await call("prompt", { text: "Add a light" })
    await call("switch_model", { model: "p/m1" })
    await call("interrupt")
    const names = service.calls.map(([name]) => name)
    assert.deepEqual(service.calls.find(([name]) => name === "permission.reply")?.[1], {
      sessionID: "ses_old",
      requestID: "per_1",
      decision: "once",
    })
    assert.deepEqual(service.calls.find(([name]) => name === "session.prompt")?.[1], {
      sessionID: "ses_old",
      text: "Add a light",
    })
    assert.ok(names.includes("session.switchModel") && names.includes("session.interrupt"))
    assert.deepEqual(host, [
      ["resume", { script_handle: "gaffer:shot", session_id: "ses_old" }],
      ["cancel", { script_handle: "gaffer:shot", session_id: "ses_old" }],
    ])

    // New: another conversation, marked for the same script, on the same offer; the previous one is released.
    await call("new_session", CONNECT)
    const created = service.calls.find(([name]) => name === "session.create")?.[1]
    assert.deepEqual(created, {
      title: "Gaffer: shot.gfr",
      location: { directory: CONNECT.directory },
      metadata: { [SCRIPT_MARKER]: KEY },
    })
    assert.deepEqual(service.bindings, new Map([["ses_new1", "offer_shot"]]))
    assert.deepEqual(events("session").at(-1), {
      id: "ses_new1",
      resumed: false,
      model: "p/m1",
      workspace: CONNECT.directory,
    })

    // Close: this conversation only; the offer is never withdrawn and never announced by the panel.
    await call("close")
    assert.deepEqual(service.bindings, new Map())
    assert.deepEqual(host.at(-1), ["cancel", { script_handle: "gaffer:shot", session_id: "ses_new1" }])
    await call("prompt", { text: "late" })
    assert.equal(events("error").at(-1)?.message, "Choose Connect first.")
    const rpcMethods = new Set(service.calls.map(([name]) => name).filter((name) => name.startsWith("rpc.")))
    assert.deepEqual(rpcMethods, new Set(["rpc.hosts", "rpc.attachHost", "rpc.bindingRead", "rpc.detachHost"]))
  } finally {
    await panel.closeAll()
  }
})

it("Connect refuses an ambiguous or absent offer, keeps a conversation attached elsewhere and hides SDK errors", async () => {
  const { service, panel, call, events } = harness()
  try {
    service.setOffers([
      { offerID: "offer_a", label: "Gaffer: shot.gfr", protocol: "ok" },
      { offerID: "offer_b", label: "Gaffer: shot.gfr", protocol: "ok" },
    ])
    await call("connect", CONNECT)
    assert.match(String(events("error").at(-1)?.message), /^Several scripts offer Gaffer: shot.gfr/)
    service.setOffers([])
    await call("connect", CONNECT)
    assert.equal(events("error").at(-1)?.message, "This script is not offered to OpenCode yet; choose Connect again.")
    assert.equal(service.calls.filter(([name]) => name === "rpc.attachHost").length, 0)

    // The marked conversation is attached to another application: never taken from it.
    service.setOffers([
      { offerID: "offer_shot", label: "Gaffer: shot.gfr", protocol: "ok" },
      { offerID: "offer_blender", label: "Blender: shot.blend", protocol: "ok" },
    ])
    service.bindings.set("ses_old", "offer_blender")
    await call("connect", CONNECT)
    assert.match(String(events("error").at(-1)?.message), /attached to another application/)
    assert.equal(service.bindings.get("ses_old"), "offer_blender")

    // Attached here, then moved elsewhere by another client: closing leaves that binding alone.
    service.bindings.clear()
    await call("connect", CONNECT)
    service.bindings.set("ses_old", "offer_blender")
    await call("close")
    assert.equal(service.bindings.get("ses_old"), "offer_blender")

    for (const bad of [{ token: "short" }, { host: "10.0.0.1" }, { offer_label: "" }]) {
      await call("connect", { ...CONNECT, ...bad })
      assert.match(
        String(events("error").at(-1)?.message),
        /^The panel action failed: (Invalid host endpoint|Incomplete connect request)\. Check that OpenCode runs/,
      )
    }
    service.bindings.clear()
    await call("connect", CONNECT)
    ;(service.api.session as { prompt: unknown }).prompt = async () => {
      throw new Error(`token ${HOST.token} on port ${HOST.port}`)
    }
    await call("prompt", { text: "x" })
    const message = events("error").at(-1)?.message as string
    assert.ok(!message.includes(HOST.token) && !message.includes(String(HOST.port)), message)
  } finally {
    await panel.closeAll()
  }
})

// Real trial: the first Connect failed while OpenCode opened the script's folder (its Location) and loaded its plugins
// ("RPC is unavailable"); it was read as the CodeNomad conversation attached to the same script holding it.
it("Connect waits for the Location OpenCode is still opening, next to a conversation attached to the same script", async () => {
  const { service, panel, call, events } = harness(10_000)
  try {
    service.bindings.set("ses_codenomad", "offer_shot")
    service.setUnavailable(2)
    await call("connect", CONNECT)
    assert.deepEqual(events("error"), [])
    assert.equal(service.calls.filter(([name]) => name === "rpc.hosts").length, 3)
    // Both conversations are attached to the script; closing the panel leaves the other one attached.
    assert.deepEqual(
      service.bindings,
      new Map([
        ["ses_codenomad", "offer_shot"],
        ["ses_old", "offer_shot"],
      ]),
    )
    await call("close")
    assert.deepEqual(service.bindings, new Map([["ses_codenomad", "offer_shot"]]))
  } finally {
    await panel.closeAll()
  }
})

it("a failed Connect says which step failed and why, without a credential", async () => {
  const { service, panel, call, events } = harness(-1)
  try {
    service.setUnavailable(1)
    await call("connect", CONNECT)
    assert.equal(
      events("error").at(-1)?.message,
      "Connect failed while finding this script's offer: RPC is unavailable: neuralnomads.gaffer. Check that OpenCode runs, then choose Connect.",
    )
    ;(service.api.session as { list: unknown }).list = async () => {
      throw new Error(`refused by 127.0.0.1:${HOST.port} with Basic secret ${HOST.token}\nstack`)
    }
    await call("connect", CONNECT)
    const message = String(events("error").at(-1)?.message)
    assert.match(
      message,
      /^Connect failed while finding this script's conversation: refused by 127\.0\.0\.1:… with Basic …/,
    )
    assert.ok(
      !message.includes(HOST.token) && !message.includes(String(HOST.port)) && !message.includes("stack"),
      message,
    )
  } finally {
    await panel.closeAll()
  }
})

it("Connect without a running OpenCode says so", async () => {
  const out: Out[] = []
  const panel = createPanel({ discover: async () => undefined, send: (message) => out.push(message as never) })
  await panel.dispatch({ id: "1", channel: "panel", method: "connect", params: CONNECT })
  assert.equal(out[0]?.data.message, "Start OpenCode before connecting the panel.")
  assert.deepEqual(out[1], { id: "1", error: { message: "Panel action failed." } })
})
