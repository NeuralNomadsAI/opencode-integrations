// Read-only detection of an earlier OpenCode installation in Gaffer (notice `legacy_install`): a startup file
// gaffer/startup/gui/openCode.py not written by this integration, and the gaffer/OpenCode folder it loads, which
// register another OpenCode editor and a package of the same name next to OpenCode Link. Nothing is changed: the
// user removes them.

import { existsSync, readFileSync, statSync } from "node:fs"
import { join } from "node:path"

import { HEADER } from "./host-install.ts"
import { gafferUserDir, type PathEnv } from "./paths.ts"

export type LegacyInstall = { path: string; kind: "startup file" | "folder" }

export function legacyInstalls(paths: PathEnv, startup?: string): LegacyInstall[] {
  const user = gafferUserDir(paths)
  const found: LegacyInstall[] = []
  for (const dir of new Set([join(user, "startup", "gui"), ...(startup ? [startup] : [])])) {
    const file = join(dir, "openCode.py")
    if (!existsSync(file)) continue
    try {
      if (statSync(file).size <= 1024 * 1024 && readFileSync(file, "utf8").startsWith(HEADER)) continue
    } catch {
      // unreadable: reported
    }
    found.push({ path: file, kind: "startup file" })
  }
  const folder = join(user, "OpenCode")
  if (existsSync(folder)) found.push({ path: folder, kind: "folder" })
  return found
}
