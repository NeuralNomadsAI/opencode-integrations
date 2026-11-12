// Background render tasks started by blender_render: followed from the plugin through their log and the files
// they write (the render Blender runs on this computer). Per process, as the offers.

import { closeSync, existsSync, openSync, readSync, statSync } from "node:fs"
import { basename } from "node:path"

export type RenderStart = {
  pid: number
  log: string
  work: string
  kind: "still" | "frames" | "video"
  output: string
  targets: string[]
  frames: string[]
  start: number
  end: number
  scene: string
  source: string
  working_copy?: string
  saved_first?: boolean
}
export type RenderTask = RenderStart & { id: string; started: number; cancelled?: boolean; finished?: number }

// Shared by every Location of the daemon process, as the offers.
const shared = globalThis as { __neuralnomadsBlenderTasks?: Map<string, RenderTask> }
shared.__neuralnomadsBlenderTasks ??= new Map<string, RenderTask>()
const tasks = shared.__neuralnomadsBlenderTasks
const LOG_TAIL = 256 * 1024

export function addTask(start: RenderStart): RenderTask {
  const id = `render-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
  const task = { ...start, id, started: Date.now() }
  tasks.set(id, task)
  return task
}

export const getTask = (id: string) => tasks.get(id)
export const listTasks = () => [...tasks.values()]

export function isAlive(pid: number) {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM"
  }
}

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

const marker = (text: string, key: string) => {
  const line = text
    .split(/\r?\n/)
    .filter((l) => l.startsWith(`OPENCODE_${key} `))
    .at(-1)
  if (!line) return undefined
  try {
    return JSON.parse(line.slice(key.length + 10))
  } catch {
    return line.slice(key.length + 10)
  }
}

export function taskStatus(task: RenderTask) {
  const text = readTail(task.log)
  const done = marker(text, "DONE")
  const failed = marker(text, "FAILED")
  const settings = marker(text, "RENDER")
  const alive = isAlive(task.pid)
  const lines = text.split(/\r?\n/)
  const frameLines = lines.map((l) => /Fra:\s*(\d+)/.exec(l)).filter(Boolean) as RegExpExecArray[]
  const samples = lines
    .map((l) => /(?:Sample|Rendered)\s+(\d+)\s*\/\s*(\d+)/i.exec(l))
    .filter(Boolean)
    .at(-1) as RegExpExecArray | undefined
  // Cycles prints "Time:" per frame; EEVEE in the background only "Saved:" lines, timestamped by the log.
  const times = lines
    .map((l) => /^\s*Time:\s*(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)/.exec(l))
    .filter(Boolean) as RegExpExecArray[]
  const stamp = (l: string) => {
    const m = /^(?:(\d+):)?(\d+):(\d+\.\d+)\s/.exec(l)
    return m ? Number(m[1] ?? 0) * 3600 + Number(m[2]) * 60 + Number(m[3]) : undefined
  }
  const saved = lines.filter((l) => /\|\s*Saved: '/.test(l))
  const savedTimes = saved.map(stamp).filter((t): t is number => t !== undefined)
  const seconds = times.length
    ? times.map((m) => Number(m[1] ?? 0) * 3600 + Number(m[2]) * 60 + Number(m[3]))
    : savedTimes.length > 1
      ? savedTimes.slice(1).map((t, i) => t - (savedTimes[i] as number))
      : []
  const errors = lines
    .filter((l) => /^(Error\b|ERROR\b)|Out of memory/.test(l.replace(/^\d+:\d+\.\d+\s+\S+\s+\|\s?/, "")))
    .slice(-5)
  const framesDone =
    task.kind === "still"
      ? existsSync(task.output) || task.frames.some(existsSync)
        ? 1
        : 0
      : task.frames.filter(existsSync).length
  // "done" only when the render Blender said so for THIS output and every expected file is there, not empty.
  const expected = expectedFiles(task)
  const missing = done ? expected.filter((f) => !existsSync(f) || statSync(f).size === 0) : []
  const foreign =
    done &&
    typeof done === "object" &&
    typeof (done as { output?: unknown }).output === "string" &&
    (done as { output: string }).output !== task.output
  const verified = done && !missing.length && !foreign
  const state = task.cancelled
    ? "cancelled"
    : verified
      ? "done"
      : done || failed
        ? "failed"
        : alive
          ? task.kind === "video" && framesDone === task.frames.length
            ? "encoding"
            : "rendering"
          : "failed"
  const unverified = foreign
    ? `the render log reports another output (${(done as { output: string }).output}), not ${task.output}`
    : missing.length
      ? `the render finished but ${missing.length} of ${expected.length} expected file(s) are missing or empty: ${missing
          .slice(0, 5)
          .map((f) => basename(f))
          .join(", ")}${missing.length > 5 ? ", ..." : ""}`
      : undefined
  if (state !== "rendering" && state !== "encoding") task.finished ??= Date.now()
  return {
    task: task.id,
    state,
    output: task.output,
    frames: {
      done: framesDone,
      total: task.frames.length,
      range: [task.start, task.end],
      current: frameLines.at(-1)
        ? Number(frameLines.at(-1)?.[1])
        : saved.length
          ? Math.min(task.start + saved.length - 1, task.end)
          : null,
    },
    ...(samples ? { samples: `${samples[1]}/${samples[2]}` } : {}),
    ...(seconds.length
      ? { seconds_per_frame: Math.round((seconds.reduce((a, b) => a + b, 0) / seconds.length) * 1000) / 1000 }
      : {}),
    elapsed_s: Math.round(((task.finished ?? Date.now()) - task.started) / 100) / 10,
    ...(settings && typeof settings === "object" ? { settings } : {}),
    ...(verified && typeof done === "object" ? { result: done } : {}),
    ...(state === "failed"
      ? {
          error:
            unverified ??
            (typeof failed === "string" ? failed.slice(-1500) : undefined) ??
            (errors.join(" | ") || `the render Blender stopped without finishing; see ${task.log}`),
        }
      : {}),
    ...(errors.length && state !== "failed" ? { log_errors: errors } : {}),
    log: task.log,
    ...(task.working_copy ? { working_copy: task.working_copy } : {}),
  }
}

/** The files a finished render must have written: the image or video, or every frame of the pattern. */
export const expectedFiles = (task: RenderStart) => (task.kind === "frames" ? task.targets : [task.output])

export function cancelTask(task: RenderTask) {
  task.cancelled = true
  try {
    process.kill(task.pid)
  } catch {}
  return taskStatus(task)
}
