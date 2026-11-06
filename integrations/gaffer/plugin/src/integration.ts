import { readFileSync } from "node:fs"
import { join } from "node:path"
import { pathToFileURL } from "node:url"
import type { Plugin } from "@opencode/plugin"
import { installHost } from "./host-install.ts"
import {
  createIntegrationRpc,
  HELLO_TIMEOUT_MS,
  type IntegrationMarker,
  type OfferRegistry,
  requestHost,
  sharedOfferRegistry,
} from "./kit.ts"
import { packageRoot } from "./paths.ts"
import { applyInput, contextInput } from "./schemas.ts"
import { loadSkills } from "./skills.ts"

export function readPackage(): { version: string; opencodeIntegration: IntegrationMarker } {
  return JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf8"))
}

export function resultContent(result: Record<string, unknown>) {
  const job = result.job as { outputs?: Array<{ preview_path?: string; name?: string }> } | undefined
  const images = (job?.outputs ?? []).flatMap(({ preview_path, name }) =>
    preview_path
      ? [
          {
            type: "file" as const,
            uri: pathToFileURL(preview_path).href,
            mime: "image/png",
            name: `${name ?? "render"}-preview.png`,
          },
        ]
      : [],
  )
  // Image paths are attachment URIs, never part of the text content.
  const clean = JSON.parse(JSON.stringify(result))
  for (const output of clean.job?.outputs ?? []) delete output.preview_path
  const text = JSON.stringify(clean, null, 2)
  return images.length ? [{ type: "text" as const, text }, ...images] : text
}

export async function setupGaffer(ctx: Plugin.Context, deps: { registry?: OfferRegistry; wsl?: () => boolean } = {}) {
  const pkg = readPackage()
  const marker = pkg.opencodeIntegration
  const host = marker.host
  if (!host) throw new Error("Gaffer requires a native host marker.")
  const registry =
    deps.registry ??
    sharedOfferRegistry("gaffer", {
      expected: { protocol: host.protocol, range: host.range, appVersions: host.appVersions },
      hello: (endpoint, scriptHandle) =>
        requestHost(endpoint, "hello", { script_handle: scriptHandle }, { timeoutMs: HELLO_TIMEOUT_MS }),
    })
  const rpc = createIntegrationRpc({
    marker,
    version: pkg.version,
    registry,
    wsl: deps.wsl,
    nativeSession: {
      location: ctx.location,
      get: ctx.session?.get ? (input) => ctx.session.get(input as never) : undefined,
    },
    installHost: () => installHost(marker, ctx.options as Record<string, unknown> | undefined),
  })
  const bindings = rpc.bindings
  if (!bindings) throw new Error("Gaffer requires native session bindings.")
  const registrations: Array<{ dispose(): Promise<void> }> = []
  let disconnect = () => {}
  const cleanup = async () => {
    disconnect()
    for (const item of registrations.splice(0).reverse()) await item.dispose()
  }
  try {
    const registration = await ctx.rpc.register(rpc.definition as never, rpc.handlers as never)
    registrations.push(registration)
    disconnect = rpc.connect(registration as never)
    registrations.push(
      await ctx.tool.transform((editor) => {
        editor.namespace({ name: "gaffer", description: "Inspect and edit the explicitly attached Gaffer script." })
        for (const [name, input] of [
          ["context", contextInput],
          ["apply", applyInput],
        ] as const) {
          editor.add({
            name,
            input: input as never,
            description:
              name === "context"
                ? "Read a bounded working set from the attached graph."
                : "Apply one atomic revision-checked graph intent with preflight, rollback and verification.",
            options: { namespace: "gaffer", codemode: true },
            async execute(raw, tool) {
              const args = raw as Record<string, unknown>
              const ticket = await bindings.binding(tool.sessionID, tool.signal)
              if (args.script_handle !== ticket.bound.scriptHandle)
                throw new Error("The script handle is not attached to this conversation.")
              if (tool.signal.aborted || !registry.current(ticket.authority))
                throw new Error("Attachment changed; read again.")
              const result = await requestHost(
                ticket.bound.endpoint,
                name,
                { ...args, session_id: tool.sessionID },
                { signal: tool.signal },
              )
              return { content: resultContent(result as Record<string, unknown>) }
            },
          })
        }
        for (const name of ["hosts", "attach", "detach", "install_host"] as const) {
          editor.add({
            name,
            description: `${name.replaceAll("_", " ")} for OpenCode for Gaffer.`,
            input:
              name === "attach"
                ? {
                    type: "object",
                    properties: { offerID: { type: "string" } },
                    required: ["offerID"],
                    additionalProperties: false,
                  }
                : { type: "object", additionalProperties: false },
            options: { namespace: "gaffer", codemode: true },
            async execute(raw, tool) {
              const authority = await bindings.snapshot(tool.sessionID, tool.signal)
              if (!authority) throw new Error("Session ownership is unverifiable.")
              if (name !== "detach")
                await ctx.permission.assert({
                  sessionID: tool.sessionID,
                  agent: tool.agent,
                  action: name === "install_host" ? "gaffer_install_host" : "gaffer_attach",
                  resources: ["*"],
                  source: { type: "tool", messageID: tool.messageID, id: tool.id },
                } as never)
              if (!(await bindings.verify(tool.sessionID, authority, tool.signal)))
                throw new Error("Attachment changed during permission.")
              const result =
                name === "hosts"
                  ? registry.hosts()
                  : name === "attach"
                    ? registry.attach(String((raw as { offerID: string }).offerID), tool.sessionID)
                    : name === "detach"
                      ? await bindings.detach(tool.sessionID, tool.signal)
                      : await rpc.handlers.installHost?.({})
              return { content: JSON.stringify(result) }
            },
          })
        }
      }),
    )
    registrations.push(
      await ctx.skill.transform((editor) => {
        for (const skill of loadSkills()) if (!editor.get(skill.id)) editor.add(skill as never)
      }),
    )
    registrations.push(
      await ctx.session.hook("context", async (event) => {
        try {
          const { bound } = await bindings.binding(String(event.sessionID))
          event.system.push({
            type: "text",
            text: `A Gaffer script is explicitly attached. Use gaffer_context and gaffer_apply with script_handle ${JSON.stringify(bound.scriptHandle)}. Read before editing; use the returned revision and opaque refs.`,
          })
        } catch {
          /* Unattached sessions receive no graph metadata. */
        }
      }),
    )
  } catch (error) {
    await cleanup()
    throw error
  }
  return Object.assign(cleanup, { internals: { registry, rpc } })
}
