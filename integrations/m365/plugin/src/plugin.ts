import { readdirSync, readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import type { Plugin } from "@opencode/plugin"
import { buildPluginTools, registerPluginTools, rpcDefinition, toJson } from "./kit.ts"
import { readOptions, withAuthOptions } from "./options.ts"
import { getAuthStatus } from "./runtime/auth.ts"
import { dispatchDeclaredMethod, listPublicMethods } from "./runtime/dispatcher.ts"

const root = new URL("../", import.meta.url)

export async function setupM365(ctx: Plugin.Context) {
  const options = readOptions(ctx.options as Record<string, unknown> | undefined)
  const pkg = JSON.parse(readFileSync(new URL("package.json", root), "utf8")) as { version: string }
  const status = () =>
    withAuthOptions(options, () => {
      const auth = getAuthStatus()
      return {
        contract: 1,
        integration: { id: "m365", version: pkg.version, family: "api", spec: 0 },
        connection: { state: !auth.configured ? "error" : auth.signed_in ? "connected" : "signed_out" },
      }
    })
  const definition = rpcDefinition("neuralnomads.m365", "api")
  // Separate detail method keeps the common status output conforming to contract 1.
  definition.methods.authStatus = {
    input: { type: "object", properties: {}, additionalProperties: false },
    output: { type: "object", additionalProperties: true },
  }
  const rpc = await ctx.rpc.register(
    definition as never,
    {
      status,
      authStatus: () => withAuthOptions(options, getAuthStatus),
    } as never,
  )
  const emit = () => void rpc.events.emit("status_changed" as never, {} as never).catch(() => {})
  const tools = buildPluginTools(listPublicMethods(), "m365", async (manifest, args, context) => {
    // A Graph fallback can mutate any resource; every API action is permission-gated.
    if (manifest.requires?.auth || manifest.risk !== "read") {
      await ctx.permission.assert({
        sessionID: context.sessionID,
        agent: context.agent,
        action: "m365_access",
        resources: [manifest.name],
        save: [manifest.name],
        source: { type: "tool", messageID: context.messageID, id: context.id },
      } as Parameters<typeof ctx.permission.assert>[0])
    }
    try {
      return await withAuthOptions(options, async () =>
        toJson(await dispatchDeclaredMethod(manifest.name, args, context.signal)),
      )
    } finally {
      emit()
    }
  })
  const registrations: { dispose(): Promise<void> }[] = [rpc]
  try {
    registrations.push(
      await ctx.tool.transform((editor) => {
        registerPluginTools(editor, "m365", "Microsoft Graph: files, mail, Teams and Office workflows.", tools)
      }),
    )
    const skills = readdirSync(new URL("skills/", root), { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((entry) => {
        const path = new URL(`skills/${entry.name}/SKILL.md`, root)
        const source = readFileSync(path, "utf8").replace(/\r\n/g, "\n")
        const header = /^---\n[\s\S]*?\n---\n/.exec(source)?.[0] || ""
        const description = header.match(/description:\s*(?:\|\n\s*)?([^\n]+)/)?.[1]?.trim() || entry.name
        return {
          id: entry.name,
          name: entry.name,
          description,
          autoinvoke: false,
          path: fileURLToPath(path),
          content: source.slice(header.length),
        }
      })
    registrations.push(
      await ctx.skill.transform((editor) => {
        for (const skill of skills) if (!editor.get(skill.id)) editor.add(skill as never)
      }),
    )
  } catch (error) {
    for (const registration of registrations.reverse()) await registration.dispose()
    throw error
  }
  return async () => {
    for (const registration of registrations.reverse()) await registration.dispose()
  }
}

const plugin: Plugin.Plugin = { id: "neuralnomads.m365", setup: setupM365 }
export default plugin
