// What became of a Godot editor that stopped answering a conversation (the kit's crashWatch). Godot's editor writes no
// log of its own; OpenCode Link writes what Godot's crash handler prints ("Program crashed" and the backtrace) to
// opencode-crash-<pid>.log in the project's user data folder, which its hello names.

import { closeSync, openSync, readSync, statSync } from "node:fs"
import { join } from "node:path"

import { crashWatch } from "./kit.ts"

// The end of a log, where a crash is written.
export function logTail(path: string, bytes = 64 * 1024) {
  try {
    const size = statSync(path).size
    const length = Math.min(size, bytes)
    const buffer = Buffer.alloc(length)
    const fd = openSync(path, "r")
    try {
      readSync(fd, buffer, 0, length, size - length)
    } finally {
      closeSync(fd)
    }
    return buffer.toString("utf8")
  } catch {
    return ""
  }
}

export const CRASHED = /Program crashed/

export const { crashReport, closedMessage, silentMessage } = crashWatch({
  appName: "Godot",
  app: "godot",
  part: "OpenCode Link",
  isReport: (name, host) =>
    name === `opencode-crash-${host.pid}.log` && CRASHED.test(logTail(join(host.tempDir, name))),
})
