// Read-only detection of an earlier OpenCode installation in Houdini (notice `legacy_install`): its package
// packages/opencode_houdini.json in a Houdini preferences folder, which loads another OpenCode panel and host next
// to OpenCode Link. Nothing is changed: the user removes it (or sets "enable": false in it).

import { existsSync, readFileSync, statSync } from "node:fs"
import { join } from "node:path"

import { type PathEnv, prefsDirs } from "./paths.ts"

export const LEGACY_PACKAGE = "opencode_houdini.json"

export type LegacyInstall = { package: string; enabled: boolean; points_to?: string }

// The earlier packages found in every Houdini preferences folder (and the explicit one), with whether each is on.
export function legacyInstalls(paths: PathEnv, explicit?: string): LegacyInstall[] {
  const dirs = new Set(prefsDirs(paths).map((entry) => entry.dir))
  if (explicit) dirs.add(explicit)
  const found: LegacyInstall[] = []
  for (const dir of dirs) {
    const file = join(dir, "packages", LEGACY_PACKAGE)
    if (!existsSync(file)) continue
    let enabled = true
    let pointsTo: string | undefined
    try {
      if (statSync(file).size <= 1024 * 1024) {
        const pkg = JSON.parse(readFileSync(file, "utf8")) as { enable?: unknown; path?: unknown; hpath?: unknown }
        enabled = pkg.enable !== false && pkg.enable !== "false"
        const target = pkg.path ?? pkg.hpath
        if (typeof target === "string") pointsTo = target
      }
    } catch {
      // unreadable: counted as on
    }
    found.push({ package: file, enabled, ...(pointsTo ? { points_to: pointsTo } : {}) })
  }
  return found
}
