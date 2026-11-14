// A Blender that closed under a conversation: its process gone, and the crash report Blender wrote, if any. The
// add-on's hello names its process and the folder Blender writes "<file name>.crash.txt" to; a later call that
// finds the Blender unreachable, or its offer gone, says so instead of a bare "unreachable".

import { readdirSync, statSync } from "node:fs"
import { join } from "node:path"

import { isAlive } from "./tasks.ts"

export type HostProcess = { pid: number; tempDir: string; label?: string; since: number; offerID?: string }

export function readProcess(hello: unknown, label?: string, now = Date.now()): HostProcess | null {
  const found = (hello as { process?: { pid?: unknown; temp_dir?: unknown } } | null)?.process
  if (!found || !Number.isInteger(found.pid) || (found.pid as number) <= 0) return null
  if (typeof found.temp_dir !== "string" || !found.temp_dir) return null
  return { pid: found.pid as number, tempDir: found.temp_dir, label, since: now }
}

// The newest crash report written in the process's crash folder since this conversation first reached it.
export function crashReport(host: HostProcess): string | null {
  let names: string[]
  try {
    names = readdirSync(host.tempDir)
  } catch {
    return null
  }
  let newest: { path: string; time: number } | null = null
  for (const name of names) {
    if (!name.endsWith(".crash.txt")) continue
    const path = join(host.tempDir, name)
    try {
      const time = statSync(path).mtimeMs
      if (time >= host.since - 1_000 && (!newest || time > newest.time)) newest = { path, time }
    } catch {
      // removed meanwhile
    }
  }
  return newest?.path ?? null
}

// null while the process lives: then the usual unreachable message stands.
export function closedMessage(host: HostProcess, alive: (pid: number) => boolean = isAlive): string | null {
  if (alive(host.pid)) return null
  const name = host.label ? ` (${host.label})` : ""
  const report = crashReport(host)
  return report
    ? `Blender closed${name}: its process ${host.pid} has ended. Crash report: ${report}. Open Blender again and attach it (blender_hosts).`
    : `Blender closed${name}: its process ${host.pid} has ended, with no crash report in ${host.tempDir}. Open Blender again and attach it (blender_hosts).`
}
