// Host-less documentation tool: searches the Blender Python API reference and user manual shipped
// in data/blender_docs (see its SOURCE.json).

import { readdirSync, readFileSync, statSync } from "node:fs"
import { join, relative, resolve, sep } from "node:path"

import { docsDir } from "./paths.ts"

type Scope = "api" | "manual"
type SearchResult = { path: string; line: number; snippet: string }

const DEFAULT_MAX_RESULTS = 20
const DEFAULT_CONTEXT_LINES = 1
const DEFAULT_MAX_CHARS = 32 * 1024

function clamp(value: unknown, fallback: number, min: number, max: number) {
  const parsed = typeof value === "number" ? Math.trunc(value) : Number.parseInt(String(value), 10)
  return Number.isFinite(parsed) ? Math.max(min, Math.min(max, parsed)) : fallback
}

function required(params: Record<string, unknown>, key: string) {
  const value = params[key]
  if (typeof value !== "string" || !value.trim()) throw new Error(`${key} is required`)
  return value.trim()
}

const posix = (path: string) => path.split(sep).join("/")

function rstFiles(root: string): string[] {
  return readdirSync(root, { withFileTypes: true })
    .flatMap((entry) => {
      const full = join(root, entry.name)
      if (entry.isDirectory()) return rstFiles(full)
      return entry.isFile() && entry.name.toLowerCase().endsWith(".rst") ? [full] : []
    })
    .sort()
}

function inside(root: string, candidate: string) {
  const base = resolve(root)
  const target = resolve(candidate)
  if (target !== base && !target.startsWith(base + sep))
    throw new Error("The documentation path leaves the docs folder")
  return target
}

function resolveApiDoc(root: string, identifier: string) {
  const normalized = identifier.replace(/\\/g, "/").replace(/\.rst$/i, "")
  for (const candidate of [join(root, `${normalized}.rst`), join(root, `${normalized.replace(/\//g, ".")}.rst`)]) {
    const file = inside(root, candidate)
    try {
      if (statSync(file).isFile()) return file
    } catch {
      // next candidate
    }
  }
  const lower = normalized.toLowerCase()
  const matches = rstFiles(root).filter((file) => {
    const stem = posix(relative(root, file))
      .replace(/\.rst$/i, "")
      .toLowerCase()
    return stem === lower || stem.endsWith(`/${lower}`) || stem.endsWith(`.${lower}`)
  })
  const best = matches.sort((a, b) => a.length - b.length || a.localeCompare(b))[0]
  if (!best) throw new Error(`No Blender API page found for ${identifier}`)
  return best
}

function search(root: string, scope: Scope, params: Record<string, unknown>) {
  const query = required(params, "query")
  const needle = query.toLowerCase()
  const maxResults = clamp(params.max_results, DEFAULT_MAX_RESULTS, 1, 100)
  const contextLines = clamp(params.context_lines, DEFAULT_CONTEXT_LINES, 0, 8)
  const results: SearchResult[] = []
  for (const file of rstFiles(join(root, scope))) {
    const lines = readFileSync(file, "utf8").split(/\r?\n/)
    for (let index = 0; index < lines.length; index += 1) {
      if (!lines[index]?.toLowerCase().includes(needle)) continue
      results.push({
        path: posix(relative(root, file)),
        line: index + 1,
        snippet: lines
          .slice(Math.max(0, index - contextLines), Math.min(lines.length, index + contextLines + 1))
          .join("\n")
          .trim(),
      })
      if (results.length >= maxResults) return { scope, query, count: results.length, results }
    }
  }
  return { scope, query, count: results.length, results }
}

export function blenderDocs(params: Record<string, unknown>, root = docsDir) {
  const action = params.action
  if (action === "search_api") return search(root, "api", params)
  if (action === "search_manual") return search(root, "manual", params)
  if (action === "get_api") {
    const identifier = required(params, "identifier")
    const maxChars = clamp(params.max_chars, DEFAULT_MAX_CHARS, 1024, 256 * 1024)
    const file = resolveApiDoc(join(root, "api"), identifier)
    const content = readFileSync(file, "utf8")
    return {
      identifier,
      path: posix(relative(root, file)),
      truncated: content.length > maxChars,
      size_chars: content.length,
      content: content.slice(0, maxChars),
    }
  }
  throw new Error("action must be search_api, search_manual or get_api")
}
