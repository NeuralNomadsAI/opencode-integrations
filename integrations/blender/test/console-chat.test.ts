import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { join } from "node:path"
import { it } from "node:test"
import { type Api, createPanel } from "../../../packages/integration-kit/src/panel.ts"
import { blenderPanel } from "../plugin/src/console-panel.ts"

it("formats and fans out streamed lines in the Blender timer without bpy", () => {
  const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
  const result = spawnSync(python, ["-I", join(import.meta.dirname, "console_chat_test.py")], {
    encoding: "utf8",
    windowsHide: true,
  })
  assert.equal(result.status, 0, result.error?.message ?? result.stderr)
})

it("the sidecar resumes a folder conversation and streams wrapped tool names and interruption", async () => {
  const calls: unknown[] = []
  const out: unknown[] = []
  const bindings = new Map([["ses_codenomad", "offer_here"]])
  const location = { directory: import.meta.dirname }
  const session = { id: "ses_console", agent: "build", time: { updated: 2 } }
  let push: (value: unknown) => void = () => {}
  const api = {
    rpc: (rpc: { id: string }) => {
      assert.equal(rpc.id, "neuralnomads.blender")
      return {
        hosts: async () => ({ offers: [{ label: "Blender: test.blend [10]", offerID: "offer_here" }] }),
        attachHost: async ({ offerID, sessionID }: { offerID: string; sessionID: string }) => {
          bindings.set(sessionID, offerID)
          return { outcome: "attached" }
        },
        bindingRead: async ({ sessionID }: { sessionID: string }) => ({
          state: "attached",
          binding: { offerID: bindings.get(sessionID) },
        }),
        detachHost: async ({ sessionID }: { sessionID: string }) => bindings.delete(sessionID),
      }
    },
    session: {
      list: async () => ({ data: [{ id: "ses_codenomad" }, session], cursor: {} }),
      create: async () => assert.fail("Should resume the marked conversation"),
      active: async () => ({}),
      prompt: async (value: unknown) => calls.push(value),
      interrupt: async (value: unknown) => calls.push(value),
    },
    message: {
      list: async (input: unknown) => {
        assert.deepEqual(input, { sessionID: session.id, order: "desc", limit: 21 })
        return {
          data: [
            {
              type: "assistant",
              content: [
                {
                  type: "tool",
                  name: "execute",
                  state: { status: "completed", input: { code: 'return search("context")' } },
                },
                {
                  type: "tool",
                  name: "execute",
                  state: { status: "completed", input: { code: "return tools.blender.context({})" } },
                },
                { type: "text", text: "Scene read." },
              ],
            },
            ...Array.from({ length: 20 }, (_, i) => ({ type: "user", text: `Question ${i + 1}` })),
          ],
        }
      },
    },
    model: { list: async () => ({ data: [] }), default: async () => ({ data: { providerID: "ok", id: "model" } }) },
    event: {
      subscribe: async function* ({ signal }: { signal: AbortSignal }) {
        while (!signal.aborted) {
          const item = await new Promise((resolve) => {
            const abort = () => resolve(undefined)
            signal.addEventListener("abort", abort, { once: true })
            push = (value) => {
              signal.removeEventListener("abort", abort)
              resolve(value)
            }
          })
          if (item) yield item
        }
      },
    },
  } as unknown as Api

  const panel = createPanel(blenderPanel, {
    discover: async (registration) => {
      assert.equal(registration, "service-example.json")
      return api
    },
    send: (event) => out.push(event),
    requestHost: async (...args) => {
      calls.push(args) // Blender has no session-specific host cancel/resume.
      return {}
    },
  })
  const call = (method: string, params?: Record<string, unknown>) =>
    panel.dispatch({ method, params, id: method, channel: "blender" })
  const event = (name: string, data: object) => ({ channel: "blender", event: name, data })
  const emit = async (type: string, data: object, sessionID = session.id) => {
    push({ type, data: { sessionID, ...data } })
    await new Promise((resolve) => setImmediate(resolve))
  }

  try {
    await call("connect", {
      ...location,
      host: "127.0.0.1",
      port: 4242,
      token: "a".repeat(64),
      script_handle: "blend-test",
      offer_label: "Blender: test.blend [10]",
      document_key: "test.blend",
      registration: "service-example.json",
    })
    assert.ok(out.some((item) => JSON.stringify(item).includes('"resumed":true')))
    assert.equal(bindings.get(session.id), "offer_here")
    const recent = (
      out.find((item) => (item as { event?: string }).event === "history") as {
        data: { messages: unknown[] }
      }
    ).data.messages
    assert.deepEqual(recent[0], { role: "system", text: "Earlier messages are available in OpenCode." })
    assert.deepEqual(recent[1], { role: "user", text: "Question 19" })
    assert.deepEqual(recent.slice(-3), [
      { role: "user", text: "Question 1" },
      { role: "tool", text: "blender_context / completed", name: "blender_context", state: "completed" },
      { role: "assistant", text: "Scene read." },
    ])
    out.length = 0
    await call("prompt", { text: "hello" })
    await emit("session.text.delta", { delta: "not ours" }, "ses_codenomad")
    await emit("session.text.delta", { delta: "hello\n" })
    await emit("session.tool.input.started", { id: "tool_1", name: "blender_context" })
    await emit("session.tool.success", { id: "tool_1" })
    await emit("session.tool.input.started", { id: "lookup", name: "execute" })
    await emit("session.tool.called", { id: "lookup", input: { code: 'return search({ namespace: "blender" })' } })
    await emit("session.tool.success", { id: "lookup" })
    await emit("session.tool.input.started", { id: "tool_2", name: "execute" })
    await emit("session.tool.called", { id: "tool_2", input: { code: "await tools.blender.context({})" } })
    await emit("session.tool.success", { id: "tool_2" })
    await call("interrupt")
    await emit("session.execution.interrupted", {})
    assert.deepEqual(out.slice(0, 4), [
      event("user_message", { text: "hello" }),
      { id: "prompt", result: {} },
      event("assistant_delta", { text: "hello\n" }),
      event("tool", { id: "tool_1", name: "blender_context", state: "starting" }),
    ])
    const completed = out[4] as { data: { name: string; state: string; duration_ms: number } }
    assert.equal(completed.data.name, "blender_context")
    assert.equal(completed.data.state, "completed")
    assert.ok(completed.data.duration_ms >= 0)
    assert.equal((out[5] as typeof completed).data.name, "blender_context")
    assert.equal((out[6] as typeof completed).data.name, "blender_context")
    assert.equal(
      out.some((item) => JSON.stringify(item).includes('"name":"execute"')),
      false,
    )
    assert.deepEqual(out.at(-1), event("status", { state: "idle", interrupted: true }))
    assert.deepEqual(calls, [
      { sessionID: session.id, text: "hello" },
      { sessionID: session.id, resume: false },
    ])
  } finally {
    await panel.closeAll()
  }
  assert.deepEqual([...bindings], [["ses_codenomad", "offer_here"]])
})
