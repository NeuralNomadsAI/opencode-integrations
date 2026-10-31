import type { Info, ToolContext, ToolEditor } from "@opencode/plugin/promise/tool"
import type { JsonSchema } from "effect"

import { validateAndNormalizeArgs } from "./manifest-args.ts"
import type { MethodArgManifest, MethodManifest } from "./types.ts"
import { isRecord } from "./validators.ts"

function buildArgSchema(arg: MethodArgManifest): JsonSchema.JsonSchema {
  const schema: JsonSchema.JsonSchema = arg.type === "any" ? {} : { type: arg.type }
  if (arg.type === "object") schema.additionalProperties = true
  if (arg.type === "array") schema.items = arg.items ? buildArgSchema(arg.items) : {}
  if (arg.description !== undefined) schema.description = arg.description
  if (arg.enum?.length) schema.enum = [...arg.enum]
  if (arg.default !== undefined) schema.default = structuredClone(arg.default)
  return schema
}

export function buildToolInput(args: Record<string, MethodArgManifest> = {}): JsonSchema.JsonSchema {
  return {
    type: "object",
    properties: Object.fromEntries(Object.entries(args).map(([name, arg]) => [name, buildArgSchema(arg)])),
    required: Object.entries(args)
      .filter(([, arg]) => arg.required)
      .map(([name]) => name),
    additionalProperties: false,
  }
}

// Text, or content parts: attachments such as images travel as { type: "file", uri, mime }.
export type ToolContent = { type: "text"; text: string } | { type: "file"; uri: string; mime: string; name?: string }

export type ManifestExecutor = (
  manifest: MethodManifest,
  args: Record<string, unknown>,
  context: ToolContext,
) => Promise<string | ToolContent[]>

// Tool names in manifests carry the namespace prefix (`<app>_`); the editor
// adds the namespace back, so effective tool IDs equal manifest names.
// Build definitions before the synchronous transform; the transform only registers them.
export function buildPluginTools(manifests: MethodManifest[], namespace: string, execute: ManifestExecutor): Info[] {
  const prefix = `${namespace}_`
  const seen = new Set<string>()
  return manifests.map((source) => {
    const manifest = structuredClone(source)
    if (!manifest.name.startsWith(prefix) || seen.has(manifest.name)) {
      throw new Error(`Invalid or duplicate ${namespace} tool name: ${manifest.name}`)
    }
    seen.add(manifest.name)
    return {
      name: manifest.name.slice(prefix.length),
      description: manifest.description,
      input: buildToolInput(manifest.args),
      options: { namespace, codemode: true },
      async execute(input, context) {
        context.signal.throwIfAborted()
        if (!isRecord(input)) throw new Error(`Expected object input for ${manifest.name}.`)
        const args = validateAndNormalizeArgs(manifest, input)
        const content = await execute(manifest, args, context)
        context.signal.throwIfAborted()
        return { content }
      },
    }
  })
}

export function registerPluginTools(
  editor: ToolEditor,
  namespace: string,
  description: string,
  tools: readonly Info[],
) {
  editor.namespace({ name: namespace, description })
  for (const tool of tools) editor.add(tool)
}
