// Folders the plugin reads: its own package (methods, programs, host payload) and the Godot projects it installs into.

import { existsSync, statSync } from "node:fs"
import { join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

// dist/index.js and src/*.ts both sit one level below the package root.
export const packageRoot = fileURLToPath(new URL("..", import.meta.url))
export const methodsDir = join(packageRoot, "methods")
export const programsDir = join(packageRoot, "programs")

// A Godot project: a folder with its project.godot.
export function godotProject(directory: string | undefined): string | undefined {
  if (!directory) return undefined
  const folder = resolve(directory)
  try {
    return existsSync(join(folder, "project.godot")) && statSync(folder).isDirectory() ? folder : undefined
  } catch {
    return undefined
  }
}
