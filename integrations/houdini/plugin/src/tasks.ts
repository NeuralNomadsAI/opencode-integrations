// Background render and PDG tasks started by houdini_render: a hython on this computer, followed from the plugin
// through its log (OPENCODE_PROGRESS, OPENCODE_DONE and OPENCODE_FAILED lines) and the files it writes. Per daemon
// process, as the offers.

import { closeSync, existsSync, openSync, readSync, statSync } from "node:fs"
import { basename } from "node:path"

import { isAlive } from "./kit.ts"

export type RenderStart = {
  pid: number
  log: string
  work?: string
  kind: "render" | "pdg"
  output?: string
  targets: string[]
  start?: number
  end?: number
  node: string
  source?: string
  working_copy?: string
  facts?: unknown
}
export type RenderTask = RenderStart & { id: string; started: number; cancelled?: boolean; finished?: number }

const shared = globalThis as { __neuralnomadsHoudiniTasks?: Map<string, RenderTask> }
shared.__neuralnomadsHoudiniTasks ??= new Map<string, RenderTask>()
const tasks = shared.__neuralnomadsHoudiniTasks
const LOG_TAIL = 256 * 1024

export function isRenderStart(value: unknown): value is RenderStart {
  const start = value as RenderStart
  return (
    typeof start === "object" &&
    start !== null &&
    Number.isInteger(start.pid) &&
    start.pid > 0 &&
    typeof start.log === "string" &&
    (start.kind === "render" || start.kind === "pdg") &&
    Array.isArray(start.targets)
  )
}

export function addTask(start: RenderStart): RenderTask {
  const id = `${start.kind}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
  const task = { ...start, id, started: Date.now() }
  tasks.set(id, task)
  return task
}

export const getTask = (id: string) => tasks.get(id)

function readTail(path: string) {
  if (!existsSync(path)) return ""
  const size = statSync(path).size
  const length = Math.min(size, LOG_TAIL)
  const buffer = Buffer.alloc(length)
  const fd = openSync(path, "r")
  try {
    readSync(fd, buffer, 0, length, size - length)
  } finally {
    closeSync(fd)
  }
  return buffer.toString("utf8")
}

function marker(text: string, key: string) {
  const prefix = `OPENCODE_${key} `
  const line = text
    .split(/\r?\n/)
    .filter((entry) => entry.startsWith(prefix))
    .at(-1)
  if (!line) return undefined
  try {
    return JSON.parse(line.slice(prefix.length)) as unknown
  } catch {
    return line.slice(prefix.length)
  }
}

export function taskStatus(task: RenderTask) {
  const text = readTail(task.log)
  const done = marker(text, "DONE") as { output?: unknown; files?: unknown } | string | undefined
  const failed = marker(text, "FAILED")
  const progress = marker(text, "PROGRESS") as { done?: number; total?: number } | undefined
  const alive = isAlive(task.pid)
  // "done" only when the job said so and every expected file is there, not empty.
  const missing = done ? task.targets.filter((file) => !existsSync(file) || statSync(file).size === 0) : []
  const verified = done !== undefined && !missing.length
  const state = task.cancelled
    ? "cancelled"
    : verified
      ? "done"
      : done !== undefined || failed !== undefined || !alive
        ? "failed"
        : task.kind === "pdg"
          ? "cooking"
          : "rendering"
  if (state !== "rendering" && state !== "cooking") task.finished ??= Date.now()
  const errors = text
    .split(/\r?\n/)
    .filter((line) => /\b(Error|ERROR)\b/.test(line) && !line.startsWith("OPENCODE_"))
    .slice(-5)
  return {
    task: task.id,
    kind: task.kind,
    state,
    node: task.node,
    ...(task.output ? { output: task.output } : {}),
    ...(progress && typeof progress === "object" ? { progress } : {}),
    ...(task.start !== undefined ? { frames: [task.start, task.end ?? task.start] } : {}),
    elapsed_s: Math.round(((task.finished ?? Date.now()) - task.started) / 100) / 10,
    ...(verified && typeof done === "object" ? { result: done } : {}),
    ...(state === "failed"
      ? {
          error: missing.length
            ? `the job finished but ${missing.length} of ${task.targets.length} expected file(s) are missing or empty: ${missing
                .slice(0, 5)
                .map((file) => basename(file))
                .join(", ")}${missing.length > 5 ? ", ..." : ""}`
            : typeof failed === "string"
              ? failed.slice(-1500)
              : failed !== undefined
                ? JSON.stringify(failed).slice(-1500)
                : errors.join(" | ") || `the background hython stopped without finishing; see ${task.log}`,
        }
      : {}),
    ...(errors.length && state !== "failed" ? { log_errors: errors } : {}),
    ...(task.facts ? { facts: task.facts } : {}),
    log: task.log,
    ...(task.working_copy ? { working_copy: task.working_copy } : {}),
  }
}

export function cancelTask(task: RenderTask) {
  task.cancelled = true
  try {
    process.kill(task.pid)
  } catch {}
  return taskStatus(task)
}
