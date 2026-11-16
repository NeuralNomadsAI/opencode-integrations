// Folders the plugin reads: its own package (methods, programs, host payload), its state folder (the installed
// payload) and Houdini's user preferences folders.

import { existsSync, readdirSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"

// dist/index.js and src/*.ts both sit one level below the package root.
export const packageRoot = fileURLToPath(new URL("..", import.meta.url))
export const methodsDir = join(packageRoot, "methods")
export const programsDir = join(packageRoot, "programs")

export const MIN_HOUDINI = [21, 0] as const

export type PathEnv = { env: NodeJS.ProcessEnv; platform: NodeJS.Platform; home: string }

export const defaultPathEnv = (): PathEnv => ({ env: process.env, platform: process.platform, home: homedir() })

export function stateDir(env = process.env, home = homedir(), platform = process.platform) {
  return platform === "win32"
    ? join(env.LOCALAPPDATA ?? join(home, "AppData", "Local"), "NeuralNomads", "opencode-houdini")
    : join(env.XDG_STATE_HOME ?? join(home, ".local", "state"), "neuralnomads", "opencode-houdini")
}

// Where Houdini keeps its per-version user preferences folders: houdini<major.minor> in the Documents folder
// (or in HOME when it is set) on Windows and in the home folder on Linux; <major.minor> under
// ~/Library/Preferences/houdini on macOS.
function prefsRoot({ env, platform, home }: PathEnv): { root: string; pattern: RegExp } {
  if (platform === "darwin") return { root: join(home, "Library", "Preferences", "houdini"), pattern: /^(\d+)\.(\d+)$/ }
  if (platform === "win32") return { root: env.HOME || join(home, "Documents"), pattern: /^houdini(\d+)\.(\d+)$/ }
  return { root: home, pattern: /^houdini(\d+)\.(\d+)$/ }
}

// Every existing Houdini preferences folder (any version), newest first.
export function prefsDirs(paths: PathEnv): Array<{ dir: string; version: [number, number] }> {
  const { root, pattern } = prefsRoot(paths)
  if (!existsSync(root)) return []
  let entries: string[]
  try {
    entries = readdirSync(root, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
  } catch {
    return []
  }
  return entries
    .map((name) => ({ name, match: pattern.exec(name) }))
    .filter((entry): entry is { name: string; match: RegExpExecArray } => entry.match !== null)
    .map(({ name, match }) => ({
      dir: join(root, name),
      version: [Number(match[1]), Number(match[2])] as [number, number],
    }))
    .sort((a, b) => b.version[0] - a.version[0] || b.version[1] - a.version[1])
}

// The newest existing preferences folder of Houdini 21 or later (Houdini creates it on its first start), or null.
export function defaultPrefsDir(paths: PathEnv): string | null {
  const found = prefsDirs(paths).find(
    ({ version }) => version[0] > MIN_HOUDINI[0] || (version[0] === MIN_HOUDINI[0] && version[1] >= MIN_HOUDINI[1]),
  )
  return found?.dir ?? null
}
