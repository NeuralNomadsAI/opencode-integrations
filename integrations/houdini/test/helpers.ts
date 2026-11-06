import { resolve } from "node:path"
import type { Plugin } from "@opencode/plugin"
import type { Info, ToolContext } from "@opencode/plugin/promise/tool"
import { createOfferRegistry, requestHost } from "../../../packages/integration-kit/src/index.ts"
import { readPackage, setupHoudini } from "../plugin/src/integration.ts"

export async function loadPlugin(
  input: {
    now?: () => number
    nativeGet?: (input: { sessionID: string }) => Promise<unknown>
    answer?: () => Promise<void>
    options?: object
    state?: string
    wsl?: () => boolean
  } = {},
) {
  const marker = readPackage().opencodeIntegration
  const host = marker.host as NonNullable<typeof marker.host>
  const registry = createOfferRegistry({
    expected: { protocol: host.protocol, range: host.range, appVersions: host.appVersions },
    now: input.now,
    hello: (endpoint) => requestHost(endpoint, "hello", {}, { timeoutMs: 2000 }),
  })
  const handlers: Record<string, (input: unknown, context: object) => Promise<unknown>> = {}
  const events: Array<{ name: string; data: unknown }> = []
  const tools = new Map<string, Info>()
  const permissions: string[] = []
  const location = { directory: resolve("test-location") }
  const ctx = {
    options: input.options,
    location,
    session: {
      get:
        input.nativeGet ??
        (async ({ sessionID }: { sessionID: string }) => {
          if (sessionID !== "ses_test") throw new Error("not found")
          return { id: sessionID, location }
        }),
    },
    rpc: {
      register: async (_definition: unknown, methods: typeof handlers) => {
        Object.assign(handlers, methods)
        return {
          events: {
            emit: async (name: string, data: unknown) => {
              events.push({ name, data })
            },
          },
          dispose: async () => {},
        }
      },
    },
    tool: {
      transform: async (callback: (editor: object) => void) => {
        callback({ namespace: () => {}, add: (tool: Info) => tools.set(tool.name, tool) })
        return { dispose: async () => {} }
      },
    },
    skill: {
      transform: async (callback: (editor: object) => void) => {
        callback({ get: () => null, add: () => {} })
        return { dispose: async () => {} }
      },
    },
    permission: {
      assert: async ({ action }: { action: string }) => {
        permissions.push(action)
        await input.answer?.()
      },
    },
  } as unknown as Plugin.Context
  const cleanup = await setupHoudini(ctx, { registry, wsl: input.wsl ?? (() => false), state: input.state })
  const signal = new AbortController().signal
  return {
    registry,
    cleanup,
    events,
    permissions,
    tools,
    rpc: async (name: string, payload: object = {}) => handlers[name]?.(payload, { signal }),
    tool: async (name: string, payload: object = {}) =>
      tools
        .get(name)
        ?.execute(
          payload as never,
          { sessionID: "ses_test", signal, agent: "build", messageID: "msg_test", id: "call_test" } as ToolContext,
        ),
  }
}
