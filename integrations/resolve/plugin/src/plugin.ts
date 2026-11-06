import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import type { Plugin } from "@opencode/plugin"
import { createIntegrationRpc } from "../../../../packages/integration-kit/src/integration-rpc.ts"
import type { IntegrationMarker } from "../../../../packages/integration-kit/src/marker.ts"
import { checkPreconditions, readOptions } from "./preconditions.ts"

export { checkPreconditions, readOptions } from "./preconditions.ts"
export const SERVER = "resolve"

export async function setupResolve(ctx: Plugin.Context) {
  const root = new URL("../", import.meta.url)
  const pkg = JSON.parse(readFileSync(new URL("package.json", root), "utf8")) as {
    version: string
    opencodeIntegration: IntegrationMarker
  }
  const options = readOptions(ctx.options)
  const preconditions = checkPreconditions(options)
  await ctx.mcp.transform((editor) => {
    if (!editor.get(SERVER) && preconditions.ready && options.command) {
      // Explicit connection through /mcps is required; loading never starts the vendor process.
      editor.set(SERVER, { type: "local", command: [...options.command], disabled: true })
    }
  })
  // Guard the entire vendor surface, including future execution tools and catalog refreshes.
  await ctx.tool.transform((editor) => {
    for (const tool of editor.list().filter((tool) => tool.id.startsWith(`${SERVER}_`))) {
      const execute = tool.execute
      editor.update(tool.id, (draft) => {
        draft.execute = async (input, context) => {
          await ctx.permission.assert({
            sessionID: context.sessionID,
            agent: context.agent,
            source: { type: "tool", messageID: context.messageID, id: context.id },
            action: "resolve_execute_code",
            resources: [tool.id],
            save: [tool.id],
          })
          return execute(input, context)
        }
      })
    }
  })
  const skillID = "resolve-workflows"
  const skillPath = new URL(`skills/${skillID}/SKILL.md`, root)
  const source = readFileSync(skillPath, "utf8")
  const content = source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, "")
  await ctx.skill.transform((editor) => {
    if (!editor.get(skillID))
      editor.add({
        id: skillID,
        name: skillID,
        path: fileURLToPath(skillPath),
        description: "Safe project and Fusion workflows for DaVinci Resolve Studio.",
        autoinvoke: false,
        content,
      } as Parameters<typeof editor.add>[0])
  })
  const rpc = createIntegrationRpc({
    marker: pkg.opencodeIntegration,
    version: pkg.version,
    connection: async () => {
      try {
        const server = (await ctx.mcp.list()).data.find((entry) => entry.name === SERVER)
        const state = server?.status.status
        return {
          state: state === "connected" ? "connected" : state === "failed" || state === "needs_auth" ? "error" : "none",
        }
      } catch {
        return { state: "error" }
      }
    },
  })
  const registration = await ctx.rpc.register(rpc.definition, rpc.handlers)
  const controller = new AbortController()
  void (async () => {
    for await (const _event of ctx.event.subscribe({ types: ["mcp.status.changed"], signal: controller.signal })) {
      await registration.events.emit("status_changed", {})
    }
  })().catch(() => {})
  return () => controller.abort()
}

const plugin: Plugin.Plugin = { id: "neuralnomads.resolve", setup: setupResolve }
export default plugin
