import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"
import { setTimeout as delay } from "node:timers/promises"
import { type Api, createPanel } from "../src/panel.ts"

it("the Python hosts prefer the advertised executable over either Node fallback", () => {
  const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
  const result = spawnSync(python, ["-I", join(import.meta.dirname, "panel_launcher_test.py")], {
    encoding: "utf8",
    windowsHide: true,
  })
  assert.equal(result.status, 0, result.error?.message ?? result.stderr)
})

it("queues during startup, starts drafts lazily and selects/rebinds folder conversations", async () => {
  const directory = join(tmpdir(), "shot")
  const other = join(tmpdir(), "other")
  const prompt: unknown[] = []
  const attached = new Map<string, string>()
  const sessions: Array<{
    id: string
    title: string
    location: { directory: string }
    time: { updated: number; archived?: number }
  }> = []
  let label = ""
  let offer = "offer-a"
  const out: { event: string; data: Record<string, unknown> }[] = []
  const api = {
    rpc: () => ({
      hosts: async () => ({ offers: label ? [{ label, offerID: offer }] : [] }),
      attachHost: async ({
        offerID,
        sessionID,
        takeover,
      }: {
        offerID: string
        sessionID: string
        takeover: boolean
      }) => {
        assert.equal(takeover, true)
        const replaced = attached.has(sessionID) && attached.get(sessionID) !== offerID
        attached.set(sessionID, offerID)
        return { outcome: "attached", replaced }
      },
      bindingRead: async ({ sessionID }: { sessionID: string }) =>
        attached.has(sessionID)
          ? { state: "attached", binding: { offerID: attached.get(sessionID) } }
          : { state: "none" },
      detachHost: async ({ sessionID }: { sessionID: string }) => attached.delete(sessionID),
    }),
    session: {
      list: async () => ({ data: sessions, cursor: {} }),
      create: async ({ title, location }: { title: string; location: { directory: string } }) => {
        const item = { id: `created-${sessions.length}`, title, location, time: { updated: 100 } }
        sessions.push(item)
        return item
      },
      prompt: async (item: unknown) => prompt.push(item),
      active: async () => ({}),
    },
    model: { list: async () => ({ data: [] }), default: async () => ({}) },
    message: { list: async () => ({ data: [] }) },
    event: {
      subscribe: async function* ({ signal }: { signal: AbortSignal }) {
        await new Promise((resolve) => signal.addEventListener("abort", resolve, { once: true }))
      },
    },
  } as unknown as Api
  const panel = createPanel(
    { app: "Test", rpcID: "neuralnomads.test", document: "scene", duplicateOffer: "hosts" },
    {
      discover: async () => api,
      send: (value) => out.push(value as (typeof out)[number]),
      requestHost: async () => ({}),
      retryMs: 3,
      checkEveryMs: 10,
    },
  )
  const params = (offer_label: string, dir = directory) => ({
    host: "127.0.0.1",
    port: 5555,
    token: "a".repeat(64),
    script_handle: offer_label,
    offer_label,
    directory: dir,
  })
  const call = (method: string, params?: Record<string, unknown>, channel = "panel") =>
    panel.dispatch({ id: method, channel, method, params })
  const until = async (condition: () => boolean) => {
    for (let i = 0; i < 200; i++) {
      if (condition()) return
      await delay(5)
    }
    assert.fail("Panel did not reach the expected state")
  }
  const current = () => out.filter((item) => item.event === "session").at(-1)?.data.id
  try {
    await call("connect", params("v001"))
    assert.equal(
      out.some((item) => item.event === "error"),
      false,
    )
    assert.equal(sessions.length, 0)
    await call("prompt", { text: "early" })
    assert.equal(prompt.length, 0)
    label = "v001"
    await until(() => prompt.length === 1)
    assert.equal(sessions[0]?.title, "Test: shot")
    assert.deepEqual(prompt[0], { sessionID: "created-0", text: "early" })

    sessions.push(
      { id: "codenomad", title: "CodeNomad", location: { directory }, time: { updated: 200 } },
      { id: "archived", title: "Archived", location: { directory }, time: { updated: 300, archived: 301 } },
      { id: "different", title: "Different", location: { directory: other }, time: { updated: 400 } },
    )
    label = "v002"
    await call("connect", params(label))
    assert.equal(current(), "created-0")
    await call("list")
    assert.deepEqual(
      ((out.findLast((item) => item.event === "conversations")?.data.entries ?? []) as { id: string }[]).map(
        ({ id }) => id,
      ),
      ["codenomad", "created-0"],
    )
    attached.set("codenomad", "other-host")
    await call("open", { session_id: "codenomad" })
    assert.equal(current(), "codenomad")
    assert.equal(attached.get("codenomad"), offer)
    assert.match(String(out.findLast((item) => item.event === "notice")?.data.message), /moved from another host/)
    await call("new_session", params(label))
    assert.equal(current(), null)
    assert.equal(sessions.length, 4)
    await call("prompt", { text: "first in draft" })
    assert.equal(sessions.length, 5)
    assert.equal(current(), "created-4")
    attached.delete("created-4")
    offer = "offer-restarted"
    await until(() => attached.get("created-4") === offer)
    await call("connect", params(label, other))
    assert.equal(current(), "different")
    await call("connect", params(label), "newly-opened")
    assert.equal(current(), "codenomad")
    await call("connect", params(label, join(tmpdir(), "empty")))
    assert.equal(current(), null)
    assert.equal(sessions.length, 5)
  } finally {
    await panel.closeAll()
  }
})
