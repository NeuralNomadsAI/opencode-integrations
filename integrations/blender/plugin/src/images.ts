// Captures come back as attachments (FAM-4): each `{ image: { mime, data } }` in a host result
// becomes a file part with a data URI, and the text keeps only a reference to it.

import type { ToolContent } from "./kit.ts"

const MIMES = new Set(["image/png", "image/jpeg", "image/webp"])

export function attachImages(result: unknown): string | ToolContent[] {
  const files: ToolContent[] = []
  const visit = (value: unknown, label: string): unknown => {
    if (Array.isArray(value)) return value.map((item) => visit(item, label))
    if (typeof value !== "object" || value === null) return value
    const record = value as Record<string, unknown>
    const image = record.image as { mime?: unknown; data?: unknown } | undefined
    const output: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(record)) {
      if (key === "image" && image && typeof image.data === "string" && MIMES.has(String(image.mime))) {
        const name = `${label || "capture"}-${files.length + 1}.${String(image.mime).split("/")[1]}`
        files.push({ type: "file", uri: `data:${image.mime};base64,${image.data}`, mime: String(image.mime), name })
        output.image = `attachment ${name}`
      } else output[key] = visit(item, typeof record.name === "string" ? record.name : label)
    }
    return output
  }
  const text = JSON.stringify(visit(result, ""), null, 2)
  return files.length === 0 ? text : [{ type: "text", text }, ...files]
}
