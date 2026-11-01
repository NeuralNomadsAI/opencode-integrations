// Blender's RPC and tool entrypoints use fresh native session ownership, never an ID echo.
import assert from "node:assert/strict"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { describe, it } from "node:test"

import { startFakeHost } from "../../../packages/conformance/src/index.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { announcement, loadPlugin } from "./helpers/plugin.ts"

const a = { directory: join(tmpdir(), "blender-location-a") }
const b = { directory: join(tmpdir(), "blender-location-b") }
const table = () =>
  new Map([
    ["ses_a", { id: "ses_a", location: { ...a } }],
    ["ses_b", { id: "ses_b", location: { ...b } }],
  ])
const content = (result: { content?: unknown }) => JSON.parse(String(result.content))

async function fixture(input: Parameters<typeof loadPlugin>[0] = {}) {
  const loaded = await loadPlugin({ location: a, sessions: table(), ...input, options: { allowCodeExecution: true } })
  const host = await startFakeHost({
    hostVersion: readPackage().version,
    capabilities: {
      methods: ["hello", "context", "apply", "execute_code"],
      operations: [],
      undo: "grouped_best_effort",
      background: false,
    },
    methods: {
      context: () => ({ results: [] }),
      apply: () => ({ status: "applied" }),
      execute_code: () => ({ ok: true }),
    },
  })
  const offer = announcement(host.endpoint, host.scriptHandle)
  assert.deepEqual(await loaded.rpc("advertise", offer), { outcome: "accepted" })
  const offerID = loaded.registry.hosts().offers[0]?.offerID as string
  return {
    loaded,
    host,
    offer,
    offerID,
    async close() {
      await loaded.cleanup()
      await host.close()
    },
  }
}

describe("Blender exact-session RPC and TUI tools", () => {
  it("two Locations share offers but only the owning Location can read, attach, detach or dispatch", async () => {
    const sessions = table()
    const f = await fixture({ sessions })
    const other = await loadPlugin({ location: b, sessions, registry: f.loaded.registry })
    try {
      const read = (plugin: typeof other, sessionID: string) => plugin.rpc("bindingRead", { sessionID })
      assert.equal(((await read(f.loaded, "ses_a")) as { state: string }).state, "none")
      assert.equal(((await read(other, "ses_b")) as { state: string }).state, "none")
      assert.deepEqual(await read(f.loaded, "ses_b"), { state: "unknown" })
      assert.deepEqual(await read(other, "ses_a"), { state: "unknown" })
      assert.deepEqual(await read(f.loaded, "ses_missing"), { state: "unknown" })
      assert.deepEqual(await f.loaded.rpc("attachHost", { sessionID: "ses_b", offerID: f.offerID }), {
        outcome: "refused",
      })
      assert.deepEqual(content(await f.loaded.tool("blender_attach", { offerID: f.offerID }, "ses_b")), {
        outcome: "refused",
      })
      await assert.rejects(f.loaded.tool("blender_hosts", {}, "ses_b"), /unverifiable/)
      assert.equal(f.loaded.permissions.length, 0)
      assert.deepEqual(await f.loaded.rpc("attachHost", { sessionID: "ses_a", offerID: f.offerID }), {
        outcome: "attached",
      })
      assert.deepEqual(content(await other.tool("blender_attach", { offerID: f.offerID }, "ses_b")), {
        outcome: "attached",
      })
      const attached = await read(f.loaded, "ses_a")
      assert.equal((attached as { state: string }).state, "attached")
      assert.deepEqual(await read(other, "ses_b"), attached)
      await f.loaded.tool("blender_context", {}, "ses_a")
      await other.tool("blender_context", {}, "ses_b")

      sessions.set("ses_a", { id: "ses_a", location: b })
      assert.deepEqual(await read(f.loaded, "ses_a"), { state: "unknown" })
      await assert.rejects(f.loaded.rpc("detachHost", { sessionID: "ses_a" }), /unverifiable/)
      await assert.rejects(f.loaded.tool("blender_detach", {}, "ses_a"), /unverifiable/)
      await assert.rejects(f.loaded.tool("blender_context", {}, "ses_a"), /No verified application/)
      assert.deepEqual(await read(other, "ses_a"), attached, "the same session ID keeps its binding across a move")
      await other.tool("blender_context", {}, "ses_a")
      sessions.delete("ses_a")
      assert.deepEqual(await read(other, "ses_a"), { state: "unknown" })
      await assert.rejects(other.tool("blender_context", {}, "ses_a"), /No verified application/)
      assert.deepEqual(content(await other.tool("blender_detach", {}, "ses_b")), { outcome: "detached" })
      assert.equal(((await read(other, "ses_b")) as { state: string }).state, "none")
      assert.equal(f.host.requests.filter((request) => request.method === "context").length, 3)
    } finally {
      await other.cleanup()
      await f.close()
    }
  })

  for (const [label, input] of [
    [
      "native read throws",
      {
        nativeGet: async () => {
          throw new Error("offline")
        },
      },
    ],
    ["native reader unavailable", { nativeGet: null }],
    ["wrong native ID", { nativeGet: async () => ({ id: "ses_other", location: a }) }],
    ["missing native location", { nativeGet: async () => ({ id: "ses_a" }) }],
    ["explicit workspace caller", { location: { ...a, workspaceID: "ws_private" } }],
    [
      "workspace returned by native",
      { nativeGet: async () => ({ id: "ses_a", location: { ...a, workspaceID: "ws_private" } }) },
    ],
    ["foreign native location", { nativeGet: async () => ({ id: "ses_a", location: b }) }],
  ] as const) {
    it(`fails closed when ${label}`, async () => {
      const f = await fixture(input)
      try {
        f.loaded.registry.attach(f.offerID, "ses_a")
        assert.deepEqual(await f.loaded.rpc("bindingRead", { sessionID: "ses_a" }), { state: "unknown" })
        assert.deepEqual(await f.loaded.rpc("attachHost", { sessionID: "ses_a", offerID: f.offerID }), {
          outcome: "refused",
        })
        await assert.rejects(f.loaded.rpc("detachHost", { sessionID: "ses_a" }), /unverifiable/)
        await assert.rejects(f.loaded.tool("blender_context", {}, "ses_a"), /No verified application/)
        assert.equal(f.loaded.permissions.length, 0)
        assert.deepEqual(
          f.host.requests.map((request) => request.method),
          ["hello"],
        )
      } finally {
        await f.close()
      }
    })
  }

  it("label changes emit payload-free status_changed and invalidate the old authority", async () => {
    const f = await fixture()
    try {
      await f.loaded.rpc("attachHost", { sessionID: "ses_a", offerID: f.offerID })
      const before = f.loaded.registry.authority()
      f.loaded.events.splice(0)
      await f.loaded.rpc("advertise", { ...f.offer, label: "Blender: renamed.blend" })
      assert.deepEqual(f.loaded.events, [
        { name: "hosts_changed", data: {} },
        { name: "status_changed", data: {} },
      ])
      const read = (await f.loaded.rpc("bindingRead", { sessionID: "ses_a" })) as {
        binding: unknown
        authority: unknown
      }
      assert.deepEqual(read.binding, { offerID: f.offerID, label: "Blender: renamed.blend" })
      assert.notDeepEqual(read.authority, before)
    } finally {
      await f.close()
    }
  })

  it("an unavailable native snapshot times out to unknown; malformed inputs never get echoed", async () => {
    const loaded = await loadPlugin({ nativeGet: async () => new Promise(() => {}) })
    try {
      assert.deepEqual(await loaded.rpc("bindingRead", { sessionID: "ses_test" }), { state: "unknown" })
      for (const input of [null, {}, { sessionID: "" }]) {
        assert.deepEqual(await loaded.rpc("bindingRead", input), { state: "unknown" })
      }
    } finally {
      await loaded.cleanup()
    }
    const defaults = await loadPlugin()
    try {
      assert.deepEqual(await defaults.rpc("bindingRead", { sessionID: "ses_arbitrary" }), { state: "unknown" })
      assert.equal(((await defaults.rpc("bindingRead", { sessionID: "ses_test" })) as { state: string }).state, "none")
    } finally {
      await defaults.cleanup()
    }
  })
})

describe("Blender permission fencing", () => {
  for (const action of ["attach", "hosts", "network", "code"] as const) {
    for (const mutation of ["label", "revoke", "replace", "retire", "move", "delete"] as const) {
      it(`${action}: ${mutation} while permission is pending never dispatches stale authority`, async () => {
        let enter!: () => void
        let allow!: (value: "allow") => void
        const entered = new Promise<void>((resolve) => {
          enter = resolve
        })
        const answer = new Promise<"allow">((resolve) => {
          allow = resolve
        })
        const f = await fixture({
          answer: async () => {
            enter()
            return answer
          },
        })
        try {
          if (action === "network" || action === "code") f.loaded.registry.attach(f.offerID, "ses_a")
          const pending =
            action === "attach"
              ? f.loaded.tool("blender_attach", { offerID: f.offerID }, "ses_a")
              : action === "hosts"
                ? f.loaded.tool("blender_hosts", {}, "ses_a")
                : action === "code"
                  ? f.loaded.tool("blender_execute_code", { script_handle: f.host.scriptHandle, code: "pass" }, "ses_a")
                  : f.loaded.tool(
                      "blender_apply",
                      {
                        script_handle: f.host.scriptHandle,
                        if_revision: 0,
                        command_id: "race",
                        operations: [{ op: "addons.search_sketchfab_models", args: { query: "chair" } }],
                      },
                      "ses_a",
                    )
          // Observe rejection before releasing the deferred permission to avoid unhandled rejections.
          const settled = pending.then(
            (value) => ({ value }),
            (error: unknown) => ({ error }),
          )
          await entered
          switch (mutation) {
            case "label":
              await f.loaded.rpc("advertise", { ...f.offer, label: "Blender: changed.blend" })
              break
            case "revoke":
              await f.loaded.rpc("withdraw", { offer_key: f.offer.offer_key, token: f.offer.token })
              break
            case "replace": {
              const replacement = await startFakeHost({ hostVersion: readPackage().version })
              try {
                await f.loaded.rpc("advertise", announcement(replacement.endpoint, replacement.scriptHandle))
              } finally {
                await replacement.close()
              }
              break
            }
            case "retire":
              f.loaded.registry.retire()
              break
            case "move":
              f.loaded.sessions.set("ses_a", { id: "ses_a", location: b })
              break
            case "delete":
              f.loaded.sessions.delete("ses_a")
              break
          }
          allow("allow")
          const result = await settled
          if (action === "attach")
            assert.deepEqual(content((result as { value: { content: unknown } }).value), { outcome: "refused" })
          else assert.match(String((result as { error: unknown }).error), /changed|unverifiable/)
          assert.deepEqual(
            f.loaded.permissions.map((permission) => permission.action),
            [action === "network" ? "blender_network" : action === "code" ? "blender_execute_code" : "blender_attach"],
          )
          assert.deepEqual(
            f.host.requests.map((request) => request.method),
            ["hello"],
          )
        } finally {
          allow("allow")
          await f.close()
        }
      })
    }
  }

  it("checks registry.current synchronously after the fresh native read resolves", async () => {
    let reads = 0
    let retire = () => {}
    const f = await fixture({
      nativeGet: async () => {
        if (++reads === 2) retire()
        return { id: "ses_a", location: a }
      },
    })
    try {
      retire = () => f.loaded.registry.retire()
      f.loaded.registry.attach(f.offerID, "ses_a")
      await assert.rejects(
        f.loaded.tool("blender_execute_code", { script_handle: f.host.scriptHandle, code: "pass" }, "ses_a"),
        /changed/,
      )
      assert.equal(reads, 2)
      assert.deepEqual(
        f.host.requests.map((request) => request.method),
        ["hello"],
      )
      assert.deepEqual(await f.loaded.rpc("bindingRead", { sessionID: "ses_a" }), { state: "unknown" })
      assert.deepEqual(await f.loaded.rpc("hosts"), { offers: [] })
      assert.deepEqual(await f.loaded.rpc("advertise", f.offer), { outcome: "refused" })
      assert.deepEqual(await f.loaded.rpc("withdraw", { offer_key: f.offer.offer_key, token: f.offer.token }), {
        outcome: "none",
      })
      assert.equal(f.loaded.registry.binding("ses_a"), null)
      assert.deepEqual(f.loaded.registry.liveHellos(), [])
      assert.deepEqual(f.loaded.registry.counts(), { offers: 0, attachedSessions: 0 })
      assert.equal(f.loaded.registry.authority(), null)
      assert.deepEqual(f.loaded.registry.attach(f.offerID, "ses_a"), { outcome: "refused" })
      assert.deepEqual(f.loaded.registry.detach("ses_a"), { outcome: "none" })
      await assert.rejects(f.loaded.tool("blender_hosts", {}, "ses_a"), /unverifiable/)
    } finally {
      await f.close()
    }
  })
})
