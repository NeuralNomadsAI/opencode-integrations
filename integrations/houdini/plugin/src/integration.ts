import { readFileSync } from "node:fs"
import { join } from "node:path"
import type { Plugin } from "@opencode/plugin"
import type { Info, ToolContext } from "@opencode/plugin/promise/tool"
import {
  createIntegrationRpc,
  type IntegrationMarker,
  type OfferRegistry,
  requestHost,
  sharedOfferRegistry,
} from "../../../../packages/integration-kit/src/index.ts"
import { inspectHost, installHost } from "./host-install.ts"
import { packageRoot } from "./paths.ts"

export function readPackage() {
  return JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf8")) as {
    version: string
    opencodeIntegration: IntegrationMarker
  }
}

const contextSchema = {
  type: "object",
  properties: {
    script_handle: { type: "string" },
    if_revision: { type: "integer", minimum: 0 },
    include: { type: "array", items: { type: "string" }, maxItems: 20, uniqueItems: true },
    refs: { type: "array", items: { type: "string" }, maxItems: 50 },
    match: {
      type: "object",
      properties: { name: { type: "string" }, type: { type: "string" } },
      additionalProperties: false,
    },
    radius: { type: "integer", minimum: 0, maximum: 2 },
    limit: { type: "integer", minimum: 1, maximum: 100 },
  },
  additionalProperties: false,
}
const applySchema = {
  type: "object",
  properties: {
    script_handle: { type: "string" },
    if_revision: { type: "integer", minimum: 0 },
    command_id: { type: "string", minLength: 1 },
    summary: { type: "string", maxLength: 300 },
    intent: { type: "object" },
    patch: { type: "object" },
    operations: { type: "array", minItems: 1, maxItems: 50, items: { type: "object" } },
    expect: { type: "array", maxItems: 50, items: { type: "object" } },
    dry_run: { type: "boolean", default: true },
    confirm: { type: "boolean", default: false },
  },
  required: ["script_handle", "if_revision", "command_id"],
  additionalProperties: false,
}

export async function setupHoudini(
  ctx: Plugin.Context,
  deps: { registry?: OfferRegistry; wsl?: () => boolean; state?: string } = {},
) {
  const pkg = readPackage()
  const marker = pkg.opencodeIntegration
  const host = marker.host as NonNullable<IntegrationMarker["host"]>
  const registry =
    deps.registry ??
    sharedOfferRegistry("houdini", {
      expected: { protocol: host.protocol, range: host.range, appVersions: host.appVersions },
      hello: (endpoint) => requestHost(endpoint, "hello", {}, { timeoutMs: 2000 }),
    })
  const raw = ctx.options as { hostInspectionDir?: unknown } | undefined
  const preferences =
    typeof raw?.hostInspectionDir === "string"
      ? raw.hostInspectionDir
      : process.env.NEURALNOMADS_HOUDINI_HOST_INSPECTION_DIR
  const rpc = createIntegrationRpc({
    marker,
    version: pkg.version,
    registry,
    nativeSession: {
      location: ctx.location,
      get: ctx.session?.get ? (input) => ctx.session.get(input as Parameters<typeof ctx.session.get>[0]) : undefined,
    },
    inspectHost: async () => inspectHost(preferences),
    installHost: async () => installHost(marker, preferences, deps.state),
    wsl: deps.wsl,
  })
  const bindings = rpc.bindings as NonNullable<typeof rpc.bindings>
  const assert = (tool: ToolContext, action: string, resources = ["*"]) =>
    ctx.permission.assert({
      sessionID: tool.sessionID,
      agent: tool.agent,
      action,
      resources,
      source: { type: "tool", messageID: tool.messageID, id: tool.id },
    } as Parameters<typeof ctx.permission.assert>[0])
  const tools: Info[] = []
  const add = (
    name: string,
    description: string,
    input: object,
    execute: (input: Record<string, unknown>, tool: ToolContext) => Promise<unknown>,
  ) => {
    tools.push({
      name,
      description,
      input,
      options: { namespace: "houdini", codemode: true },
      async execute(input, tool) {
        return { content: JSON.stringify(await execute(input as Record<string, unknown>, tool)) }
      },
    } as Info)
  }
  const call = async (method: string, input: Record<string, unknown>, tool: ToolContext) => {
    const ticket = await bindings.binding(tool.sessionID, tool.signal)
    if (input.script_handle && input.script_handle !== ticket.bound.scriptHandle)
      throw new Error("The script handle is not attached to this conversation.")
    if (method === "apply" && input.dry_run === false) {
      if (input.confirm !== true) throw new Error("Mutations require dry_run=false and confirm=true.")
      await assert(tool, "houdini_write")
      if (!(await bindings.verify(tool.sessionID, ticket.authority, tool.signal)))
        throw new Error("Application attachment changed during permission.")
    }
    if (tool.signal.aborted || !registry.current(ticket.authority))
      throw new Error("Application attachment is unverifiable.")
    return requestHost(
      ticket.bound.endpoint,
      method,
      { ...input, script_handle: ticket.bound.scriptHandle, session_id: tool.sessionID },
      { signal: tool.signal },
    )
  }
  add(
    "context",
    "Read bounded context from the attached Houdini graph, including its current handle and revision.",
    contextSchema,
    (input, tool) => call("context", input, tool),
  )
  add(
    "apply",
    "Preflight or apply a revision-checked Houdini graph intent. Defaults to dry-run; mutation requires confirm=true.",
    applySchema,
    (input, tool) => call("apply", input, tool),
  )
  add(
    "hosts",
    "List Houdini hosts offered by the user.",
    { type: "object", additionalProperties: false },
    async (_input, tool) => {
      const authority = await bindings.snapshot(tool.sessionID, tool.signal)
      if (!authority) throw new Error("Session ownership is unverifiable.")
      await assert(tool, "houdini_attach")
      if (!(await bindings.verify(tool.sessionID, authority, tool.signal)) || !registry.current(authority))
        throw new Error("Session ownership or host offers changed.")
      return registry.hosts()
    },
  )
  add(
    "attach",
    "Attach a user-offered Houdini to this conversation, with permission.",
    { type: "object", properties: { offerID: { type: "string" } }, required: ["offerID"], additionalProperties: false },
    async (input, tool) => {
      const authority = await bindings.snapshot(tool.sessionID, tool.signal)
      if (!authority) return { outcome: "refused" }
      await assert(tool, "houdini_attach")
      if (!(await bindings.verify(tool.sessionID, authority, tool.signal)) || !registry.current(authority))
        return { outcome: "refused" }
      return registry.attach(String(input.offerID), tool.sessionID)
    },
  )
  add(
    "detach",
    "Detach this conversation from Houdini.",
    { type: "object", additionalProperties: false },
    (_input, tool) => bindings.detach(tool.sessionID, tool.signal),
  )
  add(
    "install_host",
    "Install the embedded OpenCode Link host in the configured Houdini preferences folder.",
    { type: "object", additionalProperties: false },
    async (_input, tool) => {
      await assert(tool, "houdini_install_host")
      return rpc.handlers.installHost?.({})
    },
  )
  const registrations: Array<{ dispose(): Promise<void> }> = []
  let disconnect = () => {}
  const cleanup = async () => {
    disconnect()
    for (const r of registrations.splice(0).reverse()) await r.dispose()
  }
  try {
    const registration = await ctx.rpc.register(rpc.definition as never, rpc.handlers as never)
    registrations.push(registration)
    disconnect = rpc.connect(registration as never)
    registrations.push(
      await ctx.tool.transform((editor) => {
        editor.namespace({ name: "houdini", description: "Inspect and edit the explicitly attached Houdini scene." })
        for (const tool of tools) editor.add(tool)
      }),
    )
    const file = join(packageRoot, "skills", "houdini-pipeline", "SKILL.md")
    const source = readFileSync(file, "utf8").replaceAll("\r\n", "\n")
    registrations.push(
      await ctx.skill.transform((editor) => {
        if (!editor.get("houdini-pipeline"))
          editor.add({
            id: "houdini-pipeline",
            name: "houdini-pipeline",
            description: "Revision-checked Houdini graph work.",
            autoinvoke: false,
            path: file,
            content: source.replace(/^---\n[\s\S]*?\n---\n/, ""),
          } as never)
      }),
    )
  } catch (error) {
    await cleanup()
    throw error
  }
  return Object.assign(cleanup, { internals: { registry, rpc, tools } })
}
