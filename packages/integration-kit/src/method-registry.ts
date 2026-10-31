import { readdirSync, readFileSync } from "node:fs"
import { extname, join } from "node:path"

import type { MethodManifest } from "./types.ts"

function listMethodFiles(rootPath: string): string[] {
  const entries = readdirSync(rootPath, { withFileTypes: true })
  const result: string[] = []

  for (const entry of entries) {
    const fullPath = join(rootPath, entry.name)
    if (entry.isDirectory()) {
      result.push(...listMethodFiles(fullPath))
      continue
    }
    if (entry.isFile() && extname(entry.name).toLowerCase() === ".json") {
      result.push(fullPath)
    }
  }

  return result
}

export type MethodRegistry = {
  list(): MethodManifest[]
  get(name: string): MethodManifest | null
  has(name: string): boolean
  publicMethods(): MethodManifest[]
}

export function loadMethodRegistry(methodsRoot: string): MethodRegistry {
  const manifests = listMethodFiles(methodsRoot)
    .map((filePath) => JSON.parse(readFileSync(filePath, "utf8")) as MethodManifest)
    .sort((a, b) => a.name.localeCompare(b.name))

  const map = new Map(manifests.map((manifest) => [manifest.name, manifest]))

  return {
    list: () => manifests.slice(),
    get: (name) => map.get(name) ?? null,
    has: (name) => map.has(name),
    publicMethods: () => manifests.filter((manifest) => (manifest.execution.tool ?? manifest.name) === manifest.name),
  }
}
