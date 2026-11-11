import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import type { Plugin } from "@opencode/plugin"
import type { Info, ToolContext } from "@opencode/plugin/promise/tool"
import { createIntegrationRpc } from "../../../../packages/integration-kit/src/integration-rpc.ts"
import type { IntegrationMarker } from "../../../../packages/integration-kit/src/marker.ts"
import { createCommandLog, MAX_OPERATIONS, OPERATIONS, validateApply } from "./apply.ts"
import { applyScript, SHAPES } from "./apply-script.ts"
import { type AffinityConnection, createAffinityConnection, type McpToolResult } from "./connection.ts"
import { contextScript } from "./context-script.ts"
import { readOptions } from "./options.ts"

export { readOptions } from "./options.ts"

export const NAMESPACE = "affinity"
export const EXECUTE_PERMISSION = "affinity_execute_code"
const NAMESPACE_DESCRIPTION =
  "Affinity by Canva, through its embedded MCP server: read the open documents, the SDK documentation, render pages and run SDK scripts."
const MAX_TEXT = 60_000
const DOCUMENT = /^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$/
const TOPIC = /^[A-Za-z0-9_][A-Za-z0-9_./-]{0,159}$/
const IMAGE_MIMES = new Set(["image/jpeg", "image/png", "image/webp"])

type Content = { type: "text"; text: string } | { type: "file"; uri: string; mime: string; name?: string }

function bounded(text: string, offset = 0, max = MAX_TEXT) {
  const slice = text.slice(offset, offset + max)
  const next = offset + slice.length
  return next < text.length
    ? `${slice}\n\n[truncated: ${text.length - next} more characters, continue with offset ${next}]`
    : slice
}

function textOf(result: McpToolResult) {
  return (result.content ?? [])
    .filter((part) => part.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n")
}

// Affinity reports script errors and refusals as isError results: surface them as tool errors.
function checked(result: McpToolResult) {
  if (result?.isError) throw new Error(bounded(textOf(result) || "Affinity reported an error.", 0, 4_000))
  return result
}

function toContent(result: McpToolResult, label: string): string | Content[] {
  const files: Content[] = []
  for (const part of result.content ?? []) {
    if (part.type === "image" && typeof part.data === "string" && IMAGE_MIMES.has(String(part.mimeType))) {
      const mime = String(part.mimeType)
      files.push({
        type: "file",
        uri: `data:${mime};base64,${part.data}`,
        mime,
        name: `${label}-${files.length + 1}.${mime.split("/")[1]}`,
      })
    }
  }
  const text = bounded(textOf(result), 0, 100_000)
  if (files.length === 0) return text
  return [{ type: "text", text: text || `${files.length} image attachment(s).` }, ...files]
}

function str(input: Record<string, unknown>, key: string, pattern?: RegExp, max = 200_000) {
  const value = input[key]
  if (value === undefined) return undefined
  if (typeof value !== "string" || value.length === 0 || value.length > max || (pattern && !pattern.test(value)))
    throw new Error(`Invalid ${key}.`)
  if (pattern === TOPIC && value.split("/").includes("..")) throw new Error(`Invalid ${key}.`)
  return value
}

function int(input: Record<string, unknown>, key: string, min: number, max: number, fallback: number) {
  const value = input[key] ?? fallback
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max)
    throw new Error(`Invalid ${key}: an integer from ${min} to ${max}.`)
  return value
}

function only(input: unknown, keys: string[]): Record<string, unknown> {
  if (typeof input !== "object" || input === null || Array.isArray(input)) throw new Error("Expected object input.")
  for (const key of Object.keys(input)) if (!keys.includes(key)) throw new Error(`Unknown argument ${key}.`)
  return input as Record<string, unknown>
}

const CURRENT_DOCUMENT_SCRIPT =
  "const { Document } = require('/document.js'); const d = Document.current; console.log(d ? String(d.sessionUuid) : '');"

export function buildTools(
  affinity: AffinityConnection,
  assertExecute: (context: ToolContext, script: string) => Promise<void>,
): Info[] {
  const run = async (name: string, args: Record<string, unknown>, context: ToolContext) => {
    const result = checked(await affinity.call(name, args, context.signal))
    context.signal.throwIfAborted()
    return result
  }
  const fixedScript = async (script: string, context: ToolContext) =>
    textOf(await run("execute_script", { script }, context))
  const tool = (
    name: string,
    description: string,
    properties: Record<string, unknown>,
    required: string[],
    execute: (input: Record<string, unknown>, context: ToolContext) => Promise<string | Content[]>,
  ): Info =>
    ({
      name,
      description,
      input: { type: "object", properties, required, additionalProperties: false },
      options: { namespace: NAMESPACE, codemode: true },
      async execute(input: unknown, context: ToolContext) {
        context.signal.throwIfAborted()
        const content = await execute(only(input, Object.keys(properties)), context)
        return { content }
      },
    }) as unknown as Info

  const commandLog = createCommandLog()
  const documentArg = {
    type: "string",
    description: "Document session UUID from affinity_context; default: the current document.",
  }
  return [
    tool(
      "context",
      "Read-only snapshot of Affinity: open documents, the target document's size, pages, current spread object tree (ref = nodeId, type, name, box in px) and selection. Call it first.",
      {
        document: documentArg,
        depth: { type: "integer", minimum: 1, maximum: 8, default: 3, description: "Tree depth." },
        limit: { type: "integer", minimum: 1, maximum: 2000, default: 300, description: "Maximum objects listed." },
      },
      [],
      async (input, context) => {
        const script = contextScript({
          ...(str(input, "document", DOCUMENT) ? { document: str(input, "document", DOCUMENT) } : {}),
          depth: int(input, "depth", 1, 8, 3),
          nodes: int(input, "limit", 1, 2000, 300),
        })
        const output = (await fixedScript(script, context)).trim().split("\n").at(-1) ?? ""
        try {
          return JSON.stringify(JSON.parse(output), null, 2)
        } catch {
          throw new Error("Affinity returned an unreadable context.")
        }
      },
    ),
    tool(
      "apply",
      `Change a document with up to ${MAX_OPERATIONS} typed operations on refs from affinity_context, as ONE undo step. Everything is checked first (refs, expect clauses, if_revision); on any error nothing changes. Operations (field "op"):
create_shape {shape, box{x,y,width,height}, fill?}; create_text {text, position{x,y} (baseline start), size (px), colour?};
set_fill {refs, fill}; set_stroke {refs, colour?|"none", width?}; set_opacity {refs, opacity 0-1};
move {refs, dx, dy}; set_box {ref, box}; rotate {refs, degrees}; align {refs, horizontal? left|center|right, vertical? top|middle|bottom, to? selection|page};
rename {refs, name}; set_visible {refs, visible}; set_text {ref, text}; group {refs}; ungroup {refs}; boolean {refs (2+), mode union|subtract|intersect|xor}; delete {refs}; select {refs}.
fill = "none" | {colour:"#rrggbb[aa]"} | {gradient:{stops:[{colour,position 0-1}...], angle? (deg, 0 = left to right, 90 = top to bottom), type? linear|radial}}.
Shapes: ${Object.keys(SHAPES).join(", ")}. Boxes are spread pixels. Created objects get their refs in the result; a ref grouped, combined or deleted cannot be used later in the same call.`,
      {
        document: documentArg,
        if_revision: {
          type: "string",
          description: "Revision from affinity_context; refused if the document changed since.",
        },
        command_id: {
          type: "string",
          description: "Unique ID; replaying it applies nothing and returns the first result.",
        },
        operations: {
          type: "array",
          minItems: 1,
          maxItems: MAX_OPERATIONS,
          items: { type: "object", properties: { op: { type: "string", enum: [...OPERATIONS] } }, required: ["op"] },
        },
        expect: {
          type: "array",
          maxItems: MAX_OPERATIONS,
          description: "Preconditions: {ref, exists?, name?, type?}.",
          items: { type: "object", properties: { ref: { type: "string" } }, required: ["ref"] },
        },
      },
      ["operations"],
      async (input, context) => {
        const { plan, commandId } = validateApply(input)
        const replayed = commandId ? commandLog.get(commandId) : undefined
        if (replayed) return JSON.stringify({ ...JSON.parse(replayed), replayed: true }, null, 2)
        const output = (await fixedScript(applyScript(plan), context)).trim().split("\n").at(-1) ?? ""
        let result: { applied?: boolean | "unknown"; error?: string }
        try {
          result = JSON.parse(output)
        } catch {
          throw new Error("Affinity returned an unreadable apply result; check affinity_context before retrying.")
        }
        if (result.applied === "unknown")
          throw new Error(
            `Affinity failed during execution (${result.error}); the change may be applied: check affinity_context.`,
          )
        if (!result.applied && result.error) throw new Error(`Nothing was changed: ${result.error}`)
        const text = JSON.stringify(result, null, 2)
        if (commandId && result.applied) commandLog.set(commandId, JSON.stringify(result))
        return text
      },
    ),
    tool(
      "docs",
      "Affinity SDK documentation. Without topic: list the topics. With topic (e.g. preamble, document.js, examples/makeGrid.js, ts/dom/NodeApi.d.ts): read it, paged by offset.",
      {
        topic: { type: "string", description: "Topic filename from the list." },
        offset: { type: "integer", minimum: 0, default: 0 },
      },
      [],
      async (input, context) => {
        const topic = str(input, "topic", TOPIC)
        const offset = int(input, "offset", 0, 50_000_000, 0)
        if (!topic) return bounded(textOf(await run("list_sdk_documentation", {}, context)), offset)
        return bounded(textOf(await run("read_sdk_documentation_topic", { filename: topic }, context)), offset)
      },
    ),
    tool(
      "hints",
      "Search Affinity's shared pool of SDK hints (sent to Canva's service). Use before experimenting.",
      { query: { type: "string", description: "The problem to solve." } },
      ["query"],
      async (input, context) =>
        bounded(textOf(await run("search_sdk_hints", { prompt: str(input, "query", undefined, 2_000) }, context))),
    ),
    tool(
      "library",
      "Affinity's script library. Without title: list the scripts. With title: read one.",
      { title: { type: "string" } },
      [],
      async (input, context) => {
        const title = str(input, "title", undefined, 300)
        return bounded(
          textOf(await run(title ? "read_library_script" : "list_library_scripts", title ? { title } : {}, context)),
        )
      },
    ),
    tool(
      "render",
      "Render a spread of a document, or the current selection, as a JPEG attachment (max 1024 px) to verify a result.",
      {
        document: documentArg,
        spread: { type: "integer", minimum: 0, default: 0, description: "Zero-based spread index." },
        selection: { type: "boolean", default: false, description: "Render the selected objects instead of a spread." },
      },
      [],
      async (input, context) => {
        const selection = input.selection ?? false
        if (typeof selection !== "boolean") throw new Error("Invalid selection.")
        const document =
          str(input, "document", DOCUMENT) ?? (await fixedScript(CURRENT_DOCUMENT_SCRIPT, context)).trim()
        if (!DOCUMENT.test(document)) throw new Error("No document is open in Affinity.")
        const spread = selection ? 0 : int(input, "spread", 0, 100_000, 0)
        const render = () =>
          selection
            ? run("render_selection", { document_session_uuid: document }, context)
            : run("render_spread", { document_session_uuid: document, spread_index: spread }, context)
        // The first render after a change made outside this connection (by the user, or another
        // client) can return the previous image; the second one is current.
        await render()
        return toContent(await render(), selection ? "selection" : "spread")
      },
    ),
    tool(
      "execute_script",
      "Run JavaScript with the Affinity SDK (require('/document.js')…); output comes from console.log. Can change documents and, if Affinity allows it, files and network. Asks permission. Group changes into one command for a single undo step; verify with affinity_render.",
      { script: { type: "string", description: "Plain JavaScript, directly executable." } },
      ["script"],
      async (input, context) => {
        const script = str(input, "script", undefined, 200_000) as string
        await assertExecute(context, script)
        return toContent(await run("execute_script", { script }, context), "script")
      },
    ),
  ]
}

export async function setupAffinity(ctx: Plugin.Context) {
  const root = new URL("../", import.meta.url)
  const pkg = JSON.parse(readFileSync(new URL("package.json", root), "utf8")) as {
    version: string
    opencodeIntegration: IntegrationMarker
  }
  const options = readOptions(ctx.options)
  const affinity = createAffinityConnection({
    urls: options.urls,
    version: pkg.version,
    callTimeoutMs: options.callTimeoutMs,
  })

  const assertExecute = async (context: ToolContext, script: string) => {
    await ctx.permission.assert({
      sessionID: context.sessionID,
      agent: context.agent,
      source: { type: "tool", messageID: context.messageID, id: context.id },
      action: EXECUTE_PERMISSION,
      resources: [`${NAMESPACE}_execute_script`],
      save: [`${NAMESPACE}_execute_script`],
      metadata: { script: script.length > 4_000 ? `${script.slice(0, 4_000)}\n…` : script },
    } as Parameters<typeof ctx.permission.assert>[0])
  }
  const tools = buildTools(affinity, assertExecute)
  await ctx.tool.transform((editor) => {
    editor.namespace({ name: NAMESPACE, description: NAMESPACE_DESCRIPTION })
    for (const tool of tools) editor.add(tool)
  })

  const skillID = "affinity-workflows"
  const skillPath = new URL(`skills/${skillID}/SKILL.md`, root)
  const content = readFileSync(skillPath, "utf8").replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, "")
  await ctx.skill.transform((editor) => {
    if (!editor.get(skillID))
      editor.add({
        id: skillID,
        name: skillID,
        path: fileURLToPath(skillPath),
        description: "Inspect, script and verify Affinity documents safely.",
        autoinvoke: false,
        content,
      } as Parameters<typeof editor.add>[0])
  })

  const rpc = createIntegrationRpc({
    marker: pkg.opencodeIntegration,
    version: pkg.version,
    // Status never opens a connection (PKG-6): it reports the one tools opened, if any.
    connection: async () => ({ state: affinity.state() }),
  })
  const registration = await ctx.rpc.register(rpc.definition, rpc.handlers)
  const stop = affinity.onChange(() => {
    void registration.events.emit("status_changed", {}).catch(() => {})
  })
  return () => {
    stop()
    affinity.close()
  }
}

const plugin: Plugin.Plugin = { id: "neuralnomads.affinity", setup: setupAffinity }
export default plugin
