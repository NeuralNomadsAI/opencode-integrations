import { tmpdir } from "node:os"
import { join } from "node:path"
import type { Plugin } from "@opencode/plugin"
import type { Info, ToolContext } from "@opencode/plugin/promise/tool"
import { readPackage, setupGaffer } from "../plugin/src/integration.ts"
import { createOfferRegistry } from "../plugin/src/kit.ts"

export async function loadPlugin(
  input: { now?: () => number; wsl?: () => boolean; answer?: (action: string) => Promise<void> } = {},
) {
  const host = readPackage().opencodeIntegration.host!
  const registry = createOfferRegistry({
    expected: { protocol: host.protocol, range: host.range, appVersions: host.appVersions },
    now: input.now,
  })
  const location = { directory: join(tmpdir(), "gaffer-location") }
  const sessions = new Map(["ses_test", "ses_other"].map((id) => [id, { id, location: { ...location } }]))
  const tools = new Map<string, Info>()
  const handlers = new Map<string, (input: unknown, ctx?: { signal: AbortSignal }) => Promise<unknown>>()
  const events: Array<{ name: string; data: unknown }> = []
  const permissions: string[] = []
  const skills = new Map<string, unknown>()
  const ctx = {
    location,
    session: {
      get: async ({ sessionID }: { sessionID: string }) => sessions.get(sessionID),
      hook: async () => ({ dispose: async () => {} }),
    },
    rpc: {
      register: async (_definition: unknown, methods: Record<string, (input: unknown) => Promise<unknown>>) => {
        for (const [key, value] of Object.entries(methods)) handlers.set(key, value)
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
      transform: async (callback: (editor: unknown) => void) => {
        callback({ namespace: () => {}, add: (tool: Info) => tools.set(`gaffer_${tool.name}`, tool) })
        return { dispose: async () => {} }
      },
    },
    skill: {
      transform: async (callback: (editor: unknown) => void) => {
        callback({ get: (id: string) => skills.get(id), add: (skill: { id: string }) => skills.set(skill.id, skill) })
        return { dispose: async () => {} }
      },
    },
    permission: {
      assert: async ({ action }: { action: string }) => {
        permissions.push(action)
        await input.answer?.(action)
      },
    },
  } as unknown as Plugin.Context
  const cleanup = await setupGaffer(ctx, { registry, wsl: input.wsl ?? (() => false) })
  const signal = new AbortController().signal
  return {
    cleanup,
    registry,
    events,
    permissions,
    sessions,
    location,
    tools,
    skills,
    rpc: (method: string, input: unknown = {}) => handlers.get(method)!(input, { signal }),
    tool: (name: string, input: unknown = {}, sessionID = "ses_test") =>
      tools
        .get(name)!
        .execute(
          input as never,
          { sessionID, signal, agent: "build", messageID: "msg_test", id: "call_test" } as ToolContext,
        ),
  }
}
