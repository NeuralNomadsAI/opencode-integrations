// Folders the plugin reads: its own package (methods, programs, host payload), its state folder (the installed
// payload) and Gaffer's user startup folder.

import { homedir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"

// dist/index.js and src/*.ts both sit one level below the package root.
export const packageRoot = fileURLToPath(new URL("..", import.meta.url))
export const methodsDir = join(packageRoot, "methods")
export const programsDir = join(packageRoot, "programs")

export type PathEnv = { env: NodeJS.ProcessEnv; platform: NodeJS.Platform; home: string }

export const defaultPathEnv = (): PathEnv => ({ env: process.env, platform: process.platform, home: homedir() })

export function stateDir(env = process.env, home = homedir(), platform = process.platform) {
  return platform === "win32"
    ? join(env.LOCALAPPDATA ?? join(home, "AppData", "Local"), "NeuralNomads", "opencode-gaffer")
    : join(env.XDG_STATE_HOME ?? join(home, ".local", "state"), "neuralnomads", "opencode-gaffer")
}

// Gaffer's user folder: ~/gaffer (GAFFER_STARTUP_PATHS starts with its startup folder; on Windows the home folder
// is USERPROFILE).
export function gafferUserDir({ env, platform, home }: PathEnv) {
  return join(platform === "win32" ? env.USERPROFILE || home : env.HOME || home, "gaffer")
}

// The GUI startup folder the installed part's loader goes into.
export function defaultStartupDir(paths: PathEnv) {
  return join(gafferUserDir(paths), "startup", "gui")
}
