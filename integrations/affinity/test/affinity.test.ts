import assert from "node:assert/strict"
import { test } from "node:test"
import type { Plugin } from "@opencode/plugin"
import { createAffinityConnection } from "../plugin/src/connection.ts"
import { LegacySseClient, type McpError, parseEvent } from "../plugin/src/mcp-client.ts"
import { DEFAULT_URLS, readOptions } from "../plugin/src/options.ts"
import { setupAffinity } from "../plugin/src/plugin.ts"
import { startFakeAffinity } from "./fake-affinity.ts"

const clientInfo = { name: "test", version: "0.0.0" }

test("options accept loopback http URLs only and bound the call timeout", () => {
  assert.deepEqual(
    readOptions({}, {}).urls.map(String),
    DEFAULT_URLS.map((url) => String(new URL(url))),
  )
  assert.equal(String(readOptions({ url: "http://127.0.0.1:7000/sse" }, {}).urls[0]), "http://127.0.0.1:7000/sse")
  assert.equal(
    String(readOptions({}, { NEURALNOMADS_AFFINITY_URL: "http://localhost:7001/sse" }).urls[0]),
    "http://localhost:7001/sse",
  )
  for (const url of [
    "https://127.0.0.1:6767/sse",
    "http://example.com/sse",
    "http://user:pw@127.0.0.1/sse",
    "x",
    "",
    5,
  ])
    assert.throws(() => readOptions({ url }, {}))
  assert.throws(() => readOptions({ callTimeoutMs: 10 }, {}))
  assert.equal(readOptions({ callTimeoutMs: 5_000 }, {}).callTimeoutMs, 5_000)
})

test("SSE frames parse multi-line data and event names", () => {
  assert.deepEqual(parseEvent("event: endpoint\ndata: /message?x=1"), { type: "endpoint", data: "/message?x=1" })
  assert.deepEqual(parseEvent('data: {"a":\ndata: 1}'), { type: "message", data: '{"a":\n1}' })
})

test("client negotiates the protocol, refuses foreign endpoints and non-loopback URLs", async () => {
  const fake = await startFakeAffinity({ protocols: ["2025-06-18"] })
  try {
    const client = await LegacySseClient.connect(fake.url, { timeoutMs: 3_000, clientInfo })
    assert.equal(client.protocolVersion, "2025-06-18")
    assert.equal(client.serverInfo?.name, "Affinity")
    assert.ok(fake.calls.some((call) => call.method === "notifications/initialized"))
    client.close()
    assert.equal(client.closed, true)
  } finally {
    await fake.close()
  }
  const foreign = await startFakeAffinity({ foreignEndpoint: true })
  try {
    await assert.rejects(
      LegacySseClient.connect(foreign.url, { timeoutMs: 3_000, clientInfo }),
      (error: McpError) => error.code === "protocol",
    )
  } finally {
    await foreign.close()
  }
  await assert.rejects(
    LegacySseClient.connect(new URL("http://example.com/sse"), { timeoutMs: 1_000, clientInfo }),
    /loopback/,
  )
})

test("connection reads the preamble, serializes calls, reports state and reconnects", async () => {
  const fake = await startFakeAffinity()
  const states: string[] = []
  const affinity = createAffinityConnection({ urls: [fake.url], version: "0.1.0" })
  affinity.onChange((state) => states.push(state))
  try {
    const [a, b] = await Promise.all([
      affinity.call("execute_script", { script: "one" }),
      affinity.call("execute_script", { script: "two" }),
    ])
    assert.equal(a.isError, false)
    assert.equal(b.isError, false)
    const methods = fake.calls
      .filter((call) => call.method === "tools/call")
      .map((call) => (call.params as { name: string }).name)
    assert.deepEqual(methods, ["read_sdk_documentation_topic", "execute_script", "execute_script"])
    assert.equal(affinity.state(), "connected")
    fake.dropStreams()
    for (let i = 0; i < 50 && affinity.state() !== "none"; i++) await new Promise((r) => setTimeout(r, 10))
    assert.equal(affinity.state(), "none")
    assert.equal((await affinity.call("list_sdk_documentation", {})).isError, false)
    assert.deepEqual(states, ["connected", "none", "connected"])
    for (let i = 0; i < 3; i++) await affinity.call("render_spread", { document_session_uuid: "x", spread_index: 0 })
    affinity.close()
    let open = await fake.openConnections()
    for (let i = 0; i < 50 && open > 0; i++) {
      await new Promise((r) => setTimeout(r, 10))
      open = await fake.openConnections()
    }
    assert.equal(open, 0, "close() leaves no socket open")
  } finally {
    affinity.close()
    await fake.close()
  }
  const down = createAffinityConnection({ urls: [fake.url], version: "0.1.0", connectTimeoutMs: 500 })
  await assert.rejects(down.call("list_sdk_documentation", {}), (error: McpError) => error.code === "unreachable")
  assert.equal(down.state(), "none")
})

function harness(url: URL, deny = false) {
  const tools = new Map<string, { execute: (input: unknown, context: unknown) => Promise<{ content: unknown }> }>()
  const skills = new Map<string, unknown>()
  const asked: { action: string; metadata?: { script?: string } }[] = []
  const events: unknown[] = []
  let namespace: unknown
  let handlers: Record<string, (input: unknown) => Promise<unknown>> = {}
  let definition: { id: string; methods: Record<string, unknown>; events: Record<string, unknown> } | undefined
  const ctx = {
    options: { url: String(url) },
    tool: {
      transform: async (callback: (editor: unknown) => void) =>
        callback({
          namespace: (value: unknown) => {
            namespace = value
          },
          add: (tool: { name: string; execute: (input: unknown, context: unknown) => Promise<{ content: unknown }> }) =>
            tools.set(`affinity_${tool.name}`, tool),
        }),
    },
    skill: {
      transform: async (callback: (editor: unknown) => void) =>
        callback({ get: (id: string) => skills.get(id), add: (value: { id: string }) => skills.set(value.id, value) }),
    },
    permission: {
      assert: async (input: { action: string; metadata?: { script?: string } }) => {
        asked.push(input)
        if (deny) throw Error("denied")
      },
    },
    rpc: {
      register: async (def: typeof definition, methods: typeof handlers) => {
        definition = def
        handlers = methods
        return { events: { emit: async (name: string, data: unknown) => void events.push({ name, data }) } }
      },
    },
  }
  const context = {
    sessionID: "session",
    agent: "build",
    messageID: "message",
    id: "call",
    signal: new AbortController().signal,
  }
  return {
    ctx: ctx as unknown as Plugin.Context,
    tools,
    skills,
    asked,
    events,
    namespace: () => namespace,
    definition: () => definition,
    status: () => handlers.status?.({}),
    call: async (name: string, input: unknown) => (await tools.get(name)?.execute(input, context))?.content,
  }
}

test("plugin registers curated tools, a skill and the vendor status RPC", async () => {
  const fake = await startFakeAffinity()
  const h = harness(fake.url)
  const cleanup = await setupAffinity(h.ctx)
  try {
    assert.deepEqual([...h.tools.keys()].sort(), [
      "affinity_apply",
      "affinity_context",
      "affinity_docs",
      "affinity_execute_script",
      "affinity_hints",
      "affinity_library",
      "affinity_render",
    ])
    assert.equal((h.namespace() as { name: string }).name, "affinity")
    assert.ok(h.skills.has("affinity-workflows"))
    assert.equal(h.definition()?.id, "neuralnomads.affinity")
    assert.deepEqual(Object.keys(h.definition()?.methods ?? {}), ["status"])
    const status = (state: string) => ({
      contract: 1,
      integration: { id: "affinity", version: "0.1.0", family: "vendor-mcp", spec: 0 },
      connection: { state },
    })
    assert.deepEqual(await h.status(), status("none"))
    assert.equal(fake.calls.length, 0, "status never connects")
    assert.equal(JSON.parse((await h.call("affinity_context", { depth: 2 })) as string).app, "Affinity 3.3")
    assert.deepEqual(await h.status(), status("connected"))
    assert.deepEqual(h.events, [{ name: "status_changed", data: {} }])
    assert.match((await h.call("affinity_docs", {})) as string, /document\.js/)
    assert.match((await h.call("affinity_docs", { topic: "big.js" })) as string, /continue with offset 60000/)
    await assert.rejects(() => h.call("affinity_docs", { topic: "../secret" }), /Invalid topic/)
    await assert.rejects(() => h.call("affinity_docs", { other: 1 }), /Unknown argument/)
    assert.equal(await h.call("affinity_hints", { query: "gradients" }), "hint for gradients")
    assert.equal(await h.call("affinity_library", { title: "Grid" }), "script Grid")
    const render = (await h.call("affinity_render", {})) as { type: string; mime?: string; uri?: string }[]
    assert.equal(render[1]?.type, "file")
    assert.equal(render[1]?.mime, "image/jpeg")
    assert.ok(render[1]?.uri?.startsWith("data:image/jpeg;base64,"))
    const renderCall = fake.calls.find((call) => (call.params as { name?: string })?.name === "render_spread")
    assert.ok(renderCall)
    assert.deepEqual((renderCall.params as { arguments: unknown }).arguments, {
      document_session_uuid: "E9E73C76-340C-4637-8DC6-1AD5201AB6A5",
      spread_index: 0,
    })
    assert.equal(h.asked.length, 0, "read tools never ask the execute permission")
    assert.equal(await h.call("affinity_execute_script", { script: "console.log(1)" }), "ok\n")
    assert.equal(h.asked[0]?.action, "affinity_execute_code")
    assert.equal(h.asked[0]?.metadata?.script, "console.log(1)")
  } finally {
    cleanup()
    await fake.close()
  }
})

test("denied permission never runs the script and script errors surface as tool errors", async () => {
  let scripts = 0
  const fake = await startFakeAffinity({
    onScript: (script) => {
      scripts++
      return script.includes("boom") ? { text: "TypeError: boom", isError: true } : { text: "ok" }
    },
  })
  const denied = harness(fake.url, true)
  const cleanupDenied = await setupAffinity(denied.ctx)
  const allowed = harness(fake.url)
  const cleanupAllowed = await setupAffinity(allowed.ctx)
  try {
    await assert.rejects(() => denied.call("affinity_execute_script", { script: "console.log(1)" }), /denied/)
    assert.equal(scripts, 0)
    await assert.rejects(() => allowed.call("affinity_execute_script", { script: "boom" }), /TypeError: boom/)
    assert.equal(scripts, 1)
  } finally {
    cleanupDenied()
    cleanupAllowed()
    await fake.close()
  }
})

test("status reports none when Affinity is closed, without leaking details", async () => {
  const fake = await startFakeAffinity()
  const url = fake.url
  await fake.close()
  const h = harness(url)
  const cleanup = await setupAffinity(h.ctx)
  try {
    await assert.rejects(() => h.call("affinity_context", {}), /not reachable/)
    const status = (await h.status()) as { connection: { state: string } }
    assert.equal(status.connection.state, "none")
    assert.equal(JSON.stringify(status).includes(String(url.port)), false)
  } finally {
    cleanup()
  }
})
