// Attachments kept across an OpenCode restart (plugin/src/attachments.ts): a second plugin load with a fresh
// registry and the same storage stands for the restarted daemon; the Blender offers itself again as its add-on
// does every 5 s. The real restart with OpenCode 2.2.5 is in native-session-bindings.test.ts.

import assert from "node:assert/strict"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, beforeEach, describe, it } from "node:test"

import { startFakeHost } from "../../../packages/conformance/src/index.ts"
import { LEASE_MS, resetAttachmentsForTests } from "../plugin/src/attachments.ts"
import { readPackage } from "../plugin/src/integration.ts"
import {
  announcement,
  clock,
  type LoadedPlugin,
  loadPlugin,
  type MemoryStorage,
  memoryStorage,
} from "./helpers/plugin.ts"

const cleanups: Array<() => Promise<void> | void> = []
after(async () => {
  for (const cleanup of cleanups.reverse()) await cleanup()
})

const CAPABILITIES = {
  methods: ["hello", "scene", "apply", "execute_code", "lab_tool"],
  operations: [],
  undo: "grouped_best_effort" as const,
  background: false,
}

async function blender(scriptHandle: string) {
  const host = await startFakeHost({
    hostVersion: readPackage().version,
    appVersion: "5.3.0",
    scriptHandle,
    capabilities: CAPABILITIES,
    methods: { scene: () => ({ revision: 0, objects: [] }) },
  })
  cleanups.push(() => host.close())
  return host
}

async function plugin(input: Parameters<typeof loadPlugin>[0] = {}): Promise<LoadedPlugin> {
  const loaded = await loadPlugin(input)
  cleanups.push(() => loaded.cleanup())
  return loaded
}

const content = (result: { content?: unknown }) => JSON.parse(String(result.content))
const state = async (loaded: LoadedPlugin, sessionID = "ses_test") =>
  ((await loaded.rpc("bindingRead", { sessionID })) as { state: string; binding?: { label: string } }).state

// Two Blenders, so that only the stored attachment (never the automatic one) can explain a restored binding.
async function twoBlenders() {
  const first = await blender("blend-first")
  const second = await blender("blend-second")
  const offers = [
    { ...announcement(first.endpoint, first.scriptHandle, "offer-first"), label: "Blender: first.blend" },
    { ...announcement(second.endpoint, second.scriptHandle, "offer-second"), label: "Blender: second.blend" },
  ]
  return { first, second, offers }
}

async function offer(loaded: LoadedPlugin, announcements: Array<Record<string, unknown>>) {
  for (const entry of announcements)
    assert.notEqual(((await loaded.rpc("advertise", entry)) as { outcome: string }).outcome, "refused")
  await loaded.internals.maintain()
}

const offerOf = (loaded: LoadedPlugin, label: string) =>
  loaded.registry.hosts().offers.find((entry) => entry.label === label)?.offerID as string

describe("attachments across an OpenCode restart", () => {
  beforeEach(() => resetAttachmentsForTests())

  it("the conversation is attached again to its Blender when that Blender offers itself again", async () => {
    const storage = memoryStorage()
    const { second, offers } = await twoBlenders()
    const before = await plugin({ storage })
    await offer(before, offers)
    content(await before.tool("blender_hosts", { attach: offerOf(before, "Blender: second.blend") }))
    assert.equal(await state(before), "attached")
    await before.cleanup()

    // The restarted daemon: a fresh registry and plugin, the same storage, nothing attached yet.
    const restarted = await plugin({ storage })
    assert.equal(await state(restarted), "none")
    await offer(restarted, offers)
    assert.equal(await state(restarted), "attached")
    assert.equal(restarted.registry.binding("ses_test")?.offerID, offerOf(restarted, "Blender: second.blend"))
    assert.deepEqual(restarted.permissions, [], "no permission and no client action")
    await restarted.tool("blender_context")
    assert.equal(second.requests.filter((request) => request.method === "scene").length, 1, "the same Blender")
  })

  it("an RPC attachment (a client such as CodeNomad) is kept too, and an explicit detach forgets it", async () => {
    const storage = memoryStorage()
    const { offers } = await twoBlenders()
    const before = await plugin({ storage })
    await offer(before, offers)
    const first = offerOf(before, "Blender: first.blend")
    assert.deepEqual(await before.rpc("attachHost", { offerID: first, sessionID: "ses_test" }), { outcome: "attached" })
    await before.cleanup()
    const restarted = await plugin({ storage })
    await offer(restarted, offers)
    assert.equal(restarted.registry.binding("ses_test")?.offerID, offerOf(restarted, "Blender: first.blend"))
    assert.deepEqual(await restarted.rpc("detachHost", { sessionID: "ses_test" }), { outcome: "detached" })
    assert.deepEqual([...storage.values.keys()], [])
    const again = await plugin({ storage })
    await offer(again, offers)
    assert.equal(await state(again), "none")
  })

  it("a detach is never undone by the restoration its own change triggers", async () => {
    const { offers } = await twoBlenders()
    const loaded = await plugin()
    await offer(loaded, offers)
    const first = offerOf(loaded, "Blender: first.blend")
    await loaded.rpc("attachHost", { offerID: first, sessionID: "ses_test" })
    assert.deepEqual(await loaded.rpc("detachHost", { sessionID: "ses_test" }), { outcome: "detached" })
    await loaded.internals.maintain()
    assert.equal(await state(loaded), "none")
    await loaded.tool("blender_hosts", { attach: first })
    await loaded.tool("blender_hosts", { detach: true })
    await loaded.internals.maintain()
    assert.equal(await state(loaded), "none")
  })

  it("the stored attachment lives while its Blender is offered and LEASE_MS after it stops", async () => {
    const time = clock()
    const storage = memoryStorage()
    const { offers } = await twoBlenders()
    const before = await plugin({ storage, now: time.now })
    await offer(before, offers)
    await before.tool("blender_hosts", { attach: offerOf(before, "Blender: first.blend") })
    await before.cleanup()
    const restarted = await plugin({ storage, now: time.now })
    // Its Blender does not offer itself again: kept for the lease, then forgotten.
    time.advance(LEASE_MS - 1_000)
    await restarted.internals.maintain()
    assert.equal(storage.values.size, 1)
    time.advance(2_000)
    await restarted.internals.maintain()
    assert.equal(storage.values.size, 0)
  })

  it("only the Location that owns the conversation attaches it back; the storage holds no secret", async () => {
    const storage: MemoryStorage = memoryStorage()
    const { first, offers } = await twoBlenders()
    const before = await plugin({ storage })
    await offer(before, offers)
    await before.tool("blender_hosts", { attach: offerOf(before, "Blender: first.blend") })
    const stored = JSON.stringify([...storage.values.values()])
    for (const secret of [first.endpoint.token, String(first.endpoint.port), "blend-first", "offer-first"])
      assert.ok(!stored.includes(secret), `the storage never holds ${secret}`)
    await before.cleanup()
    // The same conversation, still in the first Location; this plugin serves another one.
    const sessions = new Map([["ses_test", { id: "ses_test", location: { ...before.location } }]])
    const foreign = await plugin({ storage, location: { directory: join(tmpdir(), "another-location") }, sessions })
    await offer(foreign, offers)
    assert.equal(foreign.registry.binding("ses_test"), null, "the session belongs to another Location")
  })
})

describe("which conversation holds which Blender", () => {
  beforeEach(() => resetAttachmentsForTests())

  it("blender_hosts names another conversation's Blender when several are offered", async () => {
    const { offers } = await twoBlenders()
    const loaded = await plugin()
    await offer(loaded, offers)
    const second = offerOf(loaded, "Blender: second.blend")
    await loaded.tool("blender_hosts", { attach: second }, "ses_one")
    const listed = content(await loaded.tool("blender_hosts"))
    const attached = Object.fromEntries(
      listed.blenders.map((entry: { label: string; attached: unknown }) => [entry.label, entry.attached]),
    )
    assert.deepEqual(attached, { "Blender: first.blend": null, "Blender: second.blend": "another conversation" })
    await assert.rejects(
      loaded.tool("blender_context"),
      /second\.blend \[[^\]]+\] \(attached to another conversation\)/,
    )
  })
})
