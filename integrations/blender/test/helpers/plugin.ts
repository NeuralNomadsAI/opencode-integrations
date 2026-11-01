// The plugin set up in-process with a recording context: RPC handlers and events, tools (with
// reload), skills and permission requests. No OpenCode daemon is involved.

import { spawn } from "node:child_process"
import { mkdirSync, mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createInterface } from "node:readline"

import type { Plugin } from "@opencode/plugin"
import type { Info, ToolContext } from "@opencode/plugin/promise/tool"

import { type BlenderDeps, helloParams, readPackage, setupBlender } from "../../plugin/src/integration.ts"
import { createOfferRegistry, HELLO_TIMEOUT_MS, type OfferRegistry, requestHost } from "../../plugin/src/kit.ts"
import { readOptions } from "../../plugin/src/options.ts"
import type { PathEnv } from "../../plugin/src/paths.ts"

export const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
export const hostFolder = join(import.meta.dirname, "..", "..", "host")

export type Clock = { now: () => number; advance(ms: number): void }

export function clock(start = 1_000_000): Clock {
  let time = start
  return {
    now: () => time,
    advance: (ms) => {
      time += ms
    },
  }
}

// A fresh HOME/XDG tree for path-dependent behaviour (inspection, legacy shim).
export function fixturePaths(): PathEnv & { root: string; cleanup(): void } {
  const root = mkdtempSync(join(tmpdir(), "blender-plugin-"))
  const home = join(root, "home")
  mkdirSync(home, { recursive: true })
  return {
    root,
    home,
    platform: "linux",
    env: { XDG_CONFIG_HOME: join(root, "config") },
    cleanup: () => rmSync(root, { recursive: true, force: true }),
  }
}

export type PermissionAnswer = "allow" | "deny"

export type LoadedPlugin = Awaited<ReturnType<typeof loadPlugin>>

export async function loadPlugin(
  input: {
    options?: Record<string, unknown>
    paths?: PathEnv
    now?: () => number
    legacyPort?: number
    wsl?: () => boolean
    registry?: OfferRegistry
    answer?: (action: string) => PermissionAnswer | Promise<PermissionAnswer>
    location?: { directory: string; workspaceID?: string }
    sessions?: Map<string, { id: string; location: { directory: string; workspaceID?: string } }>
    nativeGet?: ((input: { sessionID: string }) => Promise<unknown>) | null
  } = {},
) {
  const options = input.options ?? {}
  const pkg = readPackage()
  const marker = pkg.opencodeIntegration
  const host = marker.host as NonNullable<typeof marker.host>
  const parsed = readOptions(options, {})
  const registry =
    input.registry ??
    createOfferRegistry({
      expected: { protocol: host.protocol, range: host.range, appVersions: host.appVersions },
      now: input.now,
      hello: (endpoint) => requestHost(endpoint, "hello", helloParams(parsed), { timeoutMs: HELLO_TIMEOUT_MS }),
    })
  const handlers = new Map<string, (input: unknown, context: unknown) => Promise<unknown>>()
  const events: Array<{ name: string; data: unknown }> = []
  const permissions: Array<{ action: string; resources: string[] }> = []
  const toolTransforms: Array<(editor: unknown) => void> = []
  let tools = new Map<string, Info>()
  const skills = new Map<string, { id: string; content: string }>()
  const userSkills = new Map<string, unknown>()
  const location = input.location ?? { directory: join(tmpdir(), "location") }
  // Exact native table: an arbitrary ID must never acquire ownership by echoing its input.
  const sessions =
    input.sessions ?? new Map(["ses_test", "ses_one"].map((id) => [id, { id, location: { ...location } }]))

  const runToolTransforms = () => {
    const next = new Map<string, Info>()
    const editor = {
      namespace: () => {},
      add: (tool: Info) => next.set(`${tool.options?.namespace ? `${tool.options.namespace}_` : ""}${tool.name}`, tool),
      list: () => [...next.values()],
      get: (id: string) => next.get(id),
      update: () => {},
      remove: (id: string) => next.delete(id),
    }
    for (const transform of toolTransforms) transform(editor)
    tools = next
  }

  const ctx = {
    options,
    location,
    session: {
      get:
        input.nativeGet === null
          ? undefined
          : (input.nativeGet ??
            (async ({ sessionID }: { sessionID: string }) => {
              const session = sessions.get(sessionID)
              if (!session) throw new Error("Session not found")
              return session
            })),
    },
    rpc: {
      async register(
        definition: { id: string },
        methods: Record<string, (input: unknown, context: unknown) => Promise<unknown>>,
      ) {
        for (const [name, handler] of Object.entries(methods)) handlers.set(name, handler)
        return {
          events: { emit: async (name: string, data: unknown) => void events.push({ name, data }) },
          dispose: async () => {},
          id: definition.id,
        }
      },
    },
    tool: {
      async transform(callback: (editor: unknown) => void) {
        toolTransforms.push(callback)
        runToolTransforms()
        return { dispose: async () => {} }
      },
      async reload() {
        runToolTransforms()
      },
    },
    skill: {
      async transform(callback: (editor: unknown) => void) {
        callback({
          get: (id: string) => skills.get(id) ?? userSkills.get(id),
          add: (skill: { id: string; content: string }) => skills.set(skill.id, skill),
          list: () => [...skills.values()],
          update: () => {},
          remove: () => {},
        })
        return { dispose: async () => {} }
      },
    },
    permission: {
      async assert(request: { action: string; resources: string[] }) {
        permissions.push({ action: request.action, resources: request.resources })
        if (((await input.answer?.(request.action)) ?? "allow") === "deny")
          throw new Error("The user rejected permission")
      },
    },
  } as unknown as Plugin.Context

  const deps: BlenderDeps = {
    registry,
    paths: input.paths,
    legacyPort: input.legacyPort ?? 1,
    wsl: input.wsl ?? (() => false),
  }
  const cleanup = await setupBlender(ctx, deps)
  const signal = new AbortController().signal

  return {
    registry,
    sessions,
    location,
    internals: cleanup.internals,
    cleanup,
    events,
    permissions,
    skills,
    toolNames: () => [...tools.keys()].sort(),
    async rpc(method: string, payload: unknown = {}) {
      const handler = handlers.get(method)
      if (!handler) throw new Error(`no RPC method ${method}`)
      return await handler(payload, { signal, error: (type: string) => ({ type }) })
    },
    async tool(name: string, payload: Record<string, unknown> = {}, sessionID = "ses_test") {
      const tool = tools.get(name)
      if (!tool) throw new Error(`tool ${name} is not registered`)
      const context = {
        sessionID,
        agent: "build",
        messageID: "msg_test",
        id: "call_test",
        signal,
        progress: async () => {},
      } as unknown as ToolContext
      return await tool.execute(payload as never, context)
    },
    context(sessionID = "ses_test") {
      return {
        sessionID,
        agent: "build",
        messageID: "msg_test",
        id: "call_test",
        signal,
        progress: async () => {},
      } as unknown as ToolContext
    },
  }
}

export type HostProcess = { endpoint: { host: "127.0.0.1"; port: number; token: string }; close(): Promise<void> }

// The add-on's wire and core modules under the system Python, with a stub application.
export async function startStubHost(allowCodeExecution: boolean): Promise<HostProcess> {
  const child = spawn(
    PYTHON,
    ["-I", join(import.meta.dirname, "..", "python", "stub_host.py"), hostFolder, allowCodeExecution ? "1" : "0"],
    {
      stdio: ["pipe", "pipe", "inherit"],
      windowsHide: true,
    },
  )
  const line = await new Promise<string>((resolve, reject) => {
    const lines = createInterface({ input: child.stdout })
    lines.once("line", resolve)
    child.once("exit", (code) => reject(new Error(`stub host exited with ${code}`)))
  })
  const { port, token } = JSON.parse(line) as { port: number; token: string }
  return {
    endpoint: { host: "127.0.0.1", port, token },
    close: async () => {
      child.stdin.end()
      await new Promise((resolve) => child.once("exit", resolve))
    },
  }
}

export function announcement(endpoint: { port: number; token: string }, scriptHandle: string, offerKey = "test-offer") {
  return {
    host: "127.0.0.1",
    port: endpoint.port,
    token: endpoint.token,
    script_handle: scriptHandle,
    label: "Blender: test.blend",
    protocol: 1,
    offer_key: offerKey,
  }
}
