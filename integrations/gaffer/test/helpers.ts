import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import type { Plugin } from "@opencode/plugin"
import type { Info, ToolContext } from "@opencode/plugin/promise/tool"
import { createOfferRegistry, HELLO_TIMEOUT_MS, requestHost } from "../../../packages/integration-kit/src/index.ts"
import { readPackage, setupGaffer } from "../plugin/src/integration.ts"
import { resetMethodForTests } from "../plugin/src/method.ts"
import type { PathEnv } from "../plugin/src/paths.ts"

// A plugin storage as OpenCode keeps it: JSON values by key, scanned by prefix in key order.
export function memoryStorage() {
  const values = new Map<string, unknown>()
  return {
    values,
    async get(key: string) {
      return values.get(key)
    },
    async set(key: string, value: unknown) {
      values.set(key, structuredClone(value))
    },
    async remove(key: string) {
      values.delete(key)
    },
    async scan({ prefix, after, limit = 100 }: { prefix: string; after?: string; limit?: number }) {
      const keys = [...values.keys()].filter((key) => key.startsWith(prefix) && (!after || key > after)).sort()
      const page = keys.slice(0, limit)
      return {
        entries: page.map((key) => ({ key, value: values.get(key) })),
        ...(keys.length > limit ? { next: page.at(-1) } : {}),
      }
    },
  }
}

// A home folder of the test's own: Gaffer's user folder (<home>/gaffer) is looked for there, never in the user's.
export function isolatedPaths(home = mkdtempSync(join(tmpdir(), "gaffer-home-"))): PathEnv {
  return { env: {}, platform: process.platform, home }
}

// What a permission request asked: its action, resources and metadata.
export type Asked = { action: string; resources: string[]; metadata?: Record<string, unknown> }

export async function loadPlugin(
  input: {
    now?: () => number
    answer?: (action: string) => Promise<void> | void
    options?: object
    // NEURALNOMADS_GAFFER_* variables; none by default, so that the user's own never apply.
    env?: NodeJS.ProcessEnv
    state?: string
    wsl?: () => boolean
    paths?: PathEnv
    storage?: ReturnType<typeof memoryStorage>
    // The daemon's shared offer list, as the plugin creates it, instead of a private one.
    shared?: boolean
  } = {},
) {
  resetMethodForTests()
  const marker = readPackage().opencodeIntegration
  const host = marker.host as NonNullable<typeof marker.host>
  // As createRegistry: one Gaffer offers each of its scripts, and hello names the offer's script.
  const registry = createOfferRegistry({
    expected: { protocol: host.protocol, range: host.range, appVersions: host.appVersions },
    now: input.now,
    hello: (endpoint, scriptHandle) =>
      requestHost(endpoint, "hello", { script_handle: scriptHandle }, { timeoutMs: HELLO_TIMEOUT_MS }),
  })
  const handlers: Record<string, (input: unknown, context: object) => Promise<unknown>> = {}
  const events: Array<{ name: string; data: unknown }> = []
  const tools = new Map<string, Info>()
  const permissions: string[] = []
  const asked: Asked[] = []
  const methods: Array<{ sessionID: string; key: string }> = []
  const location = { directory: resolve("test-location") }
  // The conversations OpenCode knows, by ID, with their Location (a moved one is no longer this Location's).
  const sessions = new Map(["ses_test", "ses_other"].map((id) => [id, { id, location: { ...location } }]))
  const storage = input.storage ?? memoryStorage()
  const ctx = {
    options: input.options,
    location,
    storage,
    session: {
      get: async ({ sessionID }: { sessionID: string }) => {
        const session = sessions.get(sessionID)
        if (!session) throw new Error("not found")
        return session
      },
      instructions: {
        entry: {
          put: async ({ sessionID, key }: { sessionID: string; key: string }) => {
            methods.push({ sessionID, key })
          },
        },
      },
    },
    rpc: {
      register: async (_definition: unknown, registered: typeof handlers) => {
        Object.assign(handlers, registered)
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
        callback({ namespace: () => {}, add: (tool: Info) => tools.set(`gaffer_${tool.name}`, tool) })
        return { dispose: async () => {} }
      },
    },
    permission: {
      assert: async (request: Asked) => {
        permissions.push(request.action)
        asked.push({ action: request.action, resources: request.resources, metadata: request.metadata })
        await input.answer?.(request.action)
      },
    },
  } as unknown as Plugin.Context
  const cleanup = await setupGaffer(ctx, {
    registry: input.shared ? undefined : registry,
    env: input.env ?? {},
    wsl: input.wsl ?? (() => false),
    state: input.state,
    paths: input.paths ?? isolatedPaths(),
    now: input.now,
  })
  const signal = new AbortController().signal
  return {
    registry: cleanup.internals.registry,
    cleanup,
    events,
    permissions,
    asked,
    methods,
    tools,
    storage,
    sessions,
    location,
    rpc: async (name: string, payload: object = {}) => handlers[name]?.(payload, { signal }),
    tool: async (name: string, payload: object = {}, sessionID = "ses_test") => {
      const tool = tools.get(name)
      if (!tool) throw new Error(`No tool ${name}`)
      return tool.execute(
        payload as never,
        { sessionID, signal, agent: "build", messageID: "msg_test", id: "call_test" } as ToolContext,
      )
    },
  }
}

// What a tool returned, as JSON.
export function content(result: unknown) {
  return JSON.parse(String((result as { content?: unknown })?.content ?? result))
}

// An announcement of a fake host's script to the plugin's advertise RPC, as Gaffer's advertiser makes it.
export function announcement(
  endpoint: { host: string; port: number; token: string },
  scriptHandle: string,
  offerKey = "test-offer",
  label = "Gaffer: shot.gfr",
) {
  return { ...endpoint, script_handle: scriptHandle, label, protocol: 1, offer_key: offerKey }
}
