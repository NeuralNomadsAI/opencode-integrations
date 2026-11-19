import { type ChildProcess, spawn } from "node:child_process"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { createServer, type IncomingMessage } from "node:http"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import type { Plugin } from "@opencode/plugin"
import type { Info, ToolContext } from "@opencode/plugin/promise/tool"
import { createOfferRegistry, HELLO_TIMEOUT_MS, requestHost } from "../../../packages/integration-kit/src/index.ts"
import { type GodotDeps, readPackage, setupGodot } from "../plugin/src/integration.ts"
import { resetMethodForTests } from "../plugin/src/method.ts"

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

export type Asked = { action: string; resources: string[]; metadata?: Record<string, unknown> }

export async function loadPlugin(
  input: {
    now?: () => number
    answer?: (action: string) => Promise<void> | void
    options?: object
    env?: NodeJS.ProcessEnv
    wsl?: () => boolean
    storage?: ReturnType<typeof memoryStorage>
    // The conversation's folder (its Location).
    directory?: string
    shared?: boolean
    spawn?: GodotDeps["spawn"]
  } = {},
) {
  resetMethodForTests()
  const marker = readPackage().opencodeIntegration
  const host = marker.host as NonNullable<typeof marker.host>
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
  const location = { directory: resolve(input.directory ?? "test-location") }
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
        callback({ namespace: () => {}, add: (tool: Info) => tools.set(`godot_${tool.name}`, tool) })
        return { dispose: async () => {} }
      },
    },
    permission: {
      assert: async (request: Asked) => {
        permissions.push(request.action)
        asked.push({
          action: request.action,
          resources: request.resources,
          ...(request.metadata ? { metadata: request.metadata } : {}),
        })
        await input.answer?.(request.action)
      },
    },
  } as unknown as Plugin.Context
  const cleanup = await setupGodot(ctx, {
    registry: input.shared ? undefined : registry,
    env: input.env ?? {},
    wsl: input.wsl ?? (() => false),
    now: input.now,
    spawn: input.spawn,
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

export type LoadedPlugin = Awaited<ReturnType<typeof loadPlugin>>

// What a tool returned, as JSON (the text part of a result with attachments).
export function content(result: unknown) {
  const value = (result as { content?: unknown })?.content ?? result
  const text = Array.isArray(value) ? (value.find((part) => part?.type === "text")?.text ?? "") : value
  return JSON.parse(String(text))
}

export function announcement(
  endpoint: { host: string; port: number; token: string },
  scriptHandle: string,
  offerKey = "test-offer",
  label = "Godot: Trial — main.tscn",
) {
  return { ...endpoint, script_handle: scriptHandle, label, protocol: 1, offer_key: offerKey }
}

// A Godot project of the test's own: project.godot, a scene, and optionally the add-on from the host folder.
export function makeProject(name = "Trial", files: Record<string, string> = {}) {
  const dir = mkdtempSync(join(tmpdir(), "godot-project-"))
  writeFileSync(join(dir, "project.godot"), `config_version=5\n\n[application]\n\nconfig/name="${name}"\n`)
  for (const [file, text] of Object.entries(files)) {
    mkdirSync(join(dir, file, ".."), { recursive: true })
    writeFileSync(join(dir, file), text)
  }
  return dir
}

export const addonSource = join(import.meta.dirname, "..", "host", "addons", "opencode_link")

async function body(request: IncomingMessage): Promise<unknown> {
  let text = ""
  for await (const chunk of request) text += chunk
  return text ? JSON.parse(text) : null
}

// An OpenCode service of the test's own, registered in a private state folder: /api/info, and the plugin's RPC at the
// Locations where it is "loaded" (others answer rpc.unavailable, as a Location without the plugin).
export async function fakeService(plugin: LoadedPlugin, root: string, loadedAt: (directory: string) => boolean) {
  const password = "service-password-sentinel"
  const calls: Array<{ method: string; directory: string | null }> = []
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1")
    const send = (status: number, value: unknown) => {
      response.writeHead(status, { "content-type": "application/json" })
      response.end(JSON.stringify(value))
    }
    if (request.headers.authorization !== `Basic ${Buffer.from(`opencode:${password}`).toString("base64")}`)
      return send(401, { type: "unauthorized" })
    if (request.method === "GET" && url.pathname === "/api/info")
      return send(200, { pid: process.pid, version: "2.2.5" })
    const match = /^\/api\/rpc\/neuralnomads\.godot\/([^/]+)$/.exec(url.pathname)
    if (request.method === "POST" && match) {
      const input = ((await body(request)) as { input?: unknown })?.input
      const directory = url.searchParams.get("location[directory]")
      calls.push({ method: match[1] ?? "", directory })
      if (!directory || !loadedAt(directory))
        return send(400, {
          _tag: "RpcError",
          type: "rpc.unavailable",
          message: "RPC is unavailable: neuralnomads.godot",
        })
      return send(200, { output: await plugin.rpc(match[1] ?? "", input as object) })
    }
    send(404, { type: "not_found" })
  })
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", () => done()))
  const port = (server.address() as { port: number }).port
  mkdirSync(join(root, "state", "opencode"), { recursive: true })
  writeFileSync(
    join(root, "state", "opencode", "service.json"),
    JSON.stringify({ url: `http://127.0.0.1:${port}`, pid: process.pid, version: "2.2.5", password }),
  )
  return { calls, close: () => new Promise<void>((done) => server.close(() => done())) }
}

// The private folders of a Godot run: its settings (APPDATA, XDG), OpenCode's registrations, the user's home.
export function privateEnv(root: string): NodeJS.ProcessEnv {
  const home = join(root, "home")
  for (const dir of ["home", "appdata", "localappdata", "config", "data", "cache", "state"])
    mkdirSync(join(root, dir), { recursive: true })
  return {
    ...process.env,
    HOME: home,
    USERPROFILE: home,
    APPDATA: join(root, "appdata"),
    LOCALAPPDATA: join(root, "localappdata"),
    XDG_CONFIG_HOME: join(root, "config"),
    XDG_DATA_HOME: join(root, "data"),
    XDG_CACHE_HOME: join(root, "cache"),
    XDG_STATE_HOME: join(root, "state"),
  }
}

// A Godot editor without a window (--headless) on a project where OpenCode Link is installed, writing its log.
export function startEditor(godot: string, project: string, env: NodeJS.ProcessEnv, log: string): ChildProcess {
  return spawn(godot, ["--headless", "--editor", "--path", project, "--log-file", log], {
    env,
    windowsHide: true,
    stdio: "ignore",
  })
}

// Waits for a condition, polling.
export async function until<T>(read: () => T | Promise<T>, timeoutMs: number, what: string): Promise<NonNullable<T>> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const value = await read()
    if (value) return value as NonNullable<T>
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`)
    await new Promise((done) => setTimeout(done, 250))
  }
}

export function removeAll(...dirs: string[]) {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true, maxRetries: 5 })
}
