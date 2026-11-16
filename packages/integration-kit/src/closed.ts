// What became of an application that stopped answering a conversation: crashed (it wrote its crash report; on
// Windows its process may stay open behind a crash window), closed (its process gone, no report: closed, or ended
// by another program), or alive without renewing its offer. The application part's hello names its process and
// the folder its crash reports go to; each integration says which report names are this process's.

import { readdirSync, statSync } from "node:fs"
import { join } from "node:path"

export type HostProcess = {
  pid: number
  tempDir: string
  label?: string
  since: number
  offerID?: string
  document?: string
}

export function isAlive(pid: number) {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM"
  }
}

export function readProcess(hello: unknown, label?: string, now = Date.now()): HostProcess | null {
  const read = hello as { process?: { pid?: unknown; temp_dir?: unknown }; document?: unknown } | null
  const found = read?.process
  if (!found || !Number.isInteger(found.pid) || (found.pid as number) <= 0) return null
  if (typeof found.temp_dir !== "string" || !found.temp_dir) return null
  const document = typeof read?.document === "string" && read.document ? read.document : undefined
  return { pid: found.pid as number, tempDir: found.temp_dir, label, since: now, document }
}

export type CrashWatchOptions = {
  // "Blender": the application as messages name it.
  appName: string
  // The tool prefix ("blender": blender_hosts).
  app: string
  // The application part's name ("OpenCode Link").
  part: string
  // Whether a file name in the crash folder is this process's crash report.
  isReport: (name: string, host: HostProcess) => boolean
  // What a report says of the unsaved changes, when not that they are lost (a scene saved at the crash).
  crashNote?: (report: string) => string | undefined
}

export function crashWatch(options: CrashWatchOptions) {
  const { appName, app, part } = options
  const again = `Open ${appName} again and attach it (${app}_hosts).`

  // This process's crash report when written since `after` (by default since the conversation first reached it).
  const crashReport = (host: HostProcess, after = host.since): string | null => {
    let names: string[]
    try {
      names = readdirSync(host.tempDir)
    } catch {
      return null
    }
    for (const name of names) {
      if (!options.isReport(name, host)) continue
      const path = join(host.tempDir, name)
      try {
        if (statSync(path).mtimeMs >= after - 1_000) return path
      } catch {
        // removed meanwhile
      }
    }
    return null
  }

  // Crashed or closed; null while its process lives without a new crash report.
  const closedMessage = (
    host: HostProcess,
    alive: (pid: number) => boolean = isAlive,
    after = host.since,
  ): string | null => {
    const name = host.label ? ` (${host.label})` : ""
    const report = crashReport(host, after)
    const living = alive(host.pid)
    if (report)
      return `${appName} crashed${name}: crash report ${report}. ${
        living
          ? `Its process ${host.pid} is still open (${appName}'s crash window) and answers nothing.`
          : `Its process ${host.pid} has ended.`
      } ${options.crashNote?.(report) ?? "Changes after its last save are lost."} ${again}`
    if (living) return null
    return `${appName} closed${name}: its process ${host.pid} has ended, with no crash report in ${host.tempDir} (closed, or ended by another program). ${again}`
  }

  // Its offer is gone while its process lives without a crash report: the application part stopped renewing it.
  const silentMessage = (host: HostProcess) => {
    const name = host.label ? ` (${host.label})` : ""
    return `${appName}${name} no longer offers itself: its process ${host.pid} still runs but stopped renewing its offer (frozen, or ${part} was disabled). ${app}_hosts lists the ${appName}s offered.`
  }

  return { crashReport, closedMessage, silentMessage }
}

export type CrashWatch = ReturnType<typeof crashWatch>
