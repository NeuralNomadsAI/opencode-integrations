// What became of a Blender that stopped answering a conversation: crashed (Blender wrote its crash report; on
// Windows its process stays open behind Blender's crash window), closed (its process gone, no report: closed, or
// ended by another program), still running a long call, or no longer answering. The add-on's hello names its
// process, the folder Blender writes "<file name>.crash.txt" to, and its document. Every Blender of a user shares
// that folder: only the report named after this Blender's document counts.

import { readdirSync, statSync } from "node:fs"
import { join } from "node:path"

import { isAlive } from "./tasks.ts"

export type HostProcess = {
  pid: number
  tempDir: string
  label?: string
  since: number
  offerID?: string
  document?: string
}

export function readProcess(hello: unknown, label?: string, now = Date.now()): HostProcess | null {
  const read = hello as { process?: { pid?: unknown; temp_dir?: unknown }; document?: unknown } | null
  const found = read?.process
  if (!found || !Number.isInteger(found.pid) || (found.pid as number) <= 0) return null
  if (typeof found.temp_dir !== "string" || !found.temp_dir) return null
  const document = typeof read?.document === "string" && read.document ? read.document : undefined
  return { pid: found.pid as number, tempDir: found.temp_dir, label, since: now, document }
}

// The name Blender gives its crash report: the file's name, or "blender" for a file never saved.
function reportName(host: HostProcess) {
  return `${host.document ? host.document.replace(/\.blend$/i, "") : "blender"}.crash.txt`
}

// This Blender's crash report when written since `after` (by default since this conversation first reached it).
export function crashReport(host: HostProcess, after = host.since): string | null {
  const wanted = reportName(host).toLowerCase()
  let names: string[]
  try {
    names = readdirSync(host.tempDir)
  } catch {
    return null
  }
  for (const name of names) {
    if (name.toLowerCase() !== wanted) continue
    const path = join(host.tempDir, name)
    try {
      if (statSync(path).mtimeMs >= after - 1_000) return path
    } catch {
      // removed meanwhile
    }
  }
  return null
}

const again = "Open Blender again and attach it (blender_hosts)."

// Crashed or closed; null while its process lives without a new crash report.
export function closedMessage(
  host: HostProcess,
  alive: (pid: number) => boolean = isAlive,
  after = host.since,
): string | null {
  const name = host.label ? ` (${host.label})` : ""
  const report = crashReport(host, after)
  const living = alive(host.pid)
  if (report)
    return `Blender crashed${name}: crash report ${report}. ${
      living
        ? `Its process ${host.pid} is still open (Blender's crash window) and answers nothing.`
        : `Its process ${host.pid} has ended.`
    } Changes after its last save are lost. ${again}`
  if (living) return null
  return `Blender closed${name}: its process ${host.pid} has ended, with no crash report in ${host.tempDir} (closed, or ended by another program). ${again}`
}

// Its offer is gone while its process lives without a crash report: the add-on stopped renewing it.
export function silentMessage(host: HostProcess) {
  const name = host.label ? ` (${host.label})` : ""
  return `Blender${name} no longer offers itself: its process ${host.pid} still runs but stopped renewing its offer (frozen, or OpenCode Link was disabled). blender_hosts lists the Blenders offered.`
}
