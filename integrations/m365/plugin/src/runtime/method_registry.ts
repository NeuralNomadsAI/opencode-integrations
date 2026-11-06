import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import type { MethodManifest } from "../kit.ts"
import { loadMethodRegistry } from "../kit.ts"

// Re-export the manifest types so the rest of the bundle keeps a stable import path.
export type {
  MethodArgManifest,
  MethodArgType,
  MethodManifest,
} from "../kit.ts"

function getMethodsRoot() {
  const runtimeDir = dirname(fileURLToPath(import.meta.url))
  return join(runtimeDir, runtimeDir.endsWith("runtime") ? "../.." : "..", "methods")
}

let cached: ReturnType<typeof loadMethodRegistry> | null = null

function getRegistry() {
  if (!cached) {
    cached = loadMethodRegistry(getMethodsRoot())
  }
  return cached
}

export function listMethodManifests(): MethodManifest[] {
  return getRegistry().list()
}

export function getMethodManifest(name: string): MethodManifest | null {
  return getRegistry().get(name)
}
