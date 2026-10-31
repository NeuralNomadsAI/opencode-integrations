// Folders the plugin reads: its own package (methods, skills, data, host payload), the OpenCode
// global configuration (legacy probe only) and Blender's user extensions repository.

import { existsSync, readdirSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"

// dist/index.js and src/*.ts both sit one level below the package root.
export const packageRoot = fileURLToPath(new URL("..", import.meta.url))
export const methodsDir = join(packageRoot, "methods")
export const skillsDir = join(packageRoot, "skills")
export const docsDir = join(packageRoot, "data", "blender_docs")

export const ADDON_ID = "opencode_link"
export const MIN_BLENDER = [5, 2] as const

export type PathEnv = { env: NodeJS.ProcessEnv; platform: NodeJS.Platform; home: string }

export const defaultPathEnv = (): PathEnv => ({ env: process.env, platform: process.platform, home: homedir() })

// OpenCode's global configuration directories (HST-19), plus OPENCODE_CONFIG_DIR when the daemon has one.
export function globalConfigDirs({ env, home }: PathEnv): string[] {
  const dirs = [join(env.XDG_CONFIG_HOME || join(home, ".config"), "opencode")]
  if (env.OPENCODE_CONFIG_DIR) dirs.push(env.OPENCODE_CONFIG_DIR)
  return [...new Set(dirs)]
}

// Blender's per-version user configuration root.
export function blenderConfigRoot({ env, platform, home }: PathEnv): string {
  if (platform === "win32")
    return join(env.APPDATA || join(home, "AppData", "Roaming"), "Blender Foundation", "Blender")
  if (platform === "darwin") return join(home, "Library", "Application Support", "Blender")
  return join(env.XDG_CONFIG_HOME || join(home, ".config"), "blender")
}

function versionOf(name: string): [number, number] | null {
  const match = /^(\d+)\.(\d+)$/.exec(name)
  return match ? [Number(match[1]), Number(match[2])] : null
}

// The user extensions repository of the newest Blender >= 5.2 started on this machine, with that
// version's folder (which proves the inspection even before the repository exists), or null.
export function defaultExtensionsDir(paths: PathEnv): { dir: string; base: string } | null {
  const root = blenderConfigRoot(paths)
  if (!existsSync(root)) return null
  const versions = readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => ({ name: entry.name, version: versionOf(entry.name) }))
    .filter((entry): entry is { name: string; version: [number, number] } => entry.version !== null)
    .filter(
      ({ version }) => version[0] > MIN_BLENDER[0] || (version[0] === MIN_BLENDER[0] && version[1] >= MIN_BLENDER[1]),
    )
    .sort((a, b) => b.version[0] - a.version[0] || b.version[1] - a.version[1])
  const newest = versions[0]
  if (!newest) return null
  return { dir: join(root, newest.name, "extensions", "user_default"), base: join(root, newest.name) }
}
