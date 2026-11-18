// Background Godots started by the plugin on this computer, with the attached editor's own executable: godot_run (the
// project or a scene for some frames, headless, or with Movie Maker for a capture) as a task, and godot_verify's reads
// of a scene file (background.gd) with bounded time. Each writes its log with --log-file; a crash is written there
// ("Program crashed") and never touches the open editor.

import { type ChildProcess, execFileSync, spawn } from "node:child_process"
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { CRASHED, logTail } from "./closed.ts"

export type Background = {
  id: string
  kind: "run" | "capture" | "verify"
  pid: number
  work: string
  log: string
  scene: string
  started: number
  frames: number
  movie?: string
  finished?: number
  exit?: number | null
  cancelled?: boolean
  error?: string
}

const shared = globalThis as { __neuralnomadsGodotTasks?: Map<string, Background> }
shared.__neuralnomadsGodotTasks ??= new Map<string, Background>()
const tasks = shared.__neuralnomadsGodotTasks

export const workDir = (kind: string) => mkdtempSync(join(tmpdir(), `opencode-godot-${kind}-`))

export type Spawner = (command: string, args: string[]) => ChildProcess

const defaultSpawn: Spawner = (command, args) => spawn(command, args, { windowsHide: true, stdio: "ignore" })

// Starts a background Godot on the project: `--path <project> --log-file <log> ...args`.
export function startBackground(
  input: {
    kind: Background["kind"]
    executable: string
    project: string
    args: string[]
    work: string
    scene: string
    frames: number
    movie?: string
  },
  run: Spawner = defaultSpawn,
): Background {
  const log = join(input.work, "godot.log")
  const child = run(input.executable, ["--path", input.project, "--log-file", log, ...input.args])
  const id = `${input.kind}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
  const task: Background = {
    id,
    kind: input.kind,
    pid: child.pid ?? 0,
    work: input.work,
    log,
    scene: input.scene,
    started: Date.now(),
    frames: input.frames,
    ...(input.movie ? { movie: input.movie } : {}),
  }
  child.once("error", (error) => {
    task.error = `Godot did not start: ${error.message}`
    task.exit ??= null
    task.finished ??= Date.now()
  })
  child.once("exit", (code) => {
    task.exit = code
    task.finished ??= Date.now()
  })
  tasks.set(id, task)
  return task
}

export const getTask = (id: string) => tasks.get(id)

const running = (task: Background) => task.finished === undefined

// The errors Godot wrote: "ERROR: ..." and "SCRIPT ERROR: ..." lines with the "at:" line after each.
export function errorsOf(text: string, limit = 10) {
  const lines = text.split(/\r?\n/)
  const found: string[] = []
  for (let index = 0; index < lines.length && found.length < limit; index++) {
    const line = lines[index] ?? ""
    if (!/^(USER )?(SCRIPT )?ERROR: /.test(line)) continue
    const at = lines[index + 1]?.trim().startsWith("at:") ? ` (${lines[index + 1]?.trim()})` : ""
    found.push(`${line}${at}`.slice(0, 400))
  }
  return found
}

// A crash's first lines: what crashed and the innermost frames.
export function crashOf(text: string) {
  const at = text.search(CRASHED)
  if (at < 0) return undefined
  return text
    .slice(text.lastIndexOf("\n", at) + 1)
    .split(/\r?\n/)
    .filter((line) => line.trim() && !/^=+$/.test(line.trim()))
    .slice(0, 8)
    .join(" | ")
    .slice(0, 800)
}

const frameFiles = (task: Background) => {
  if (!task.movie || !existsSync(task.work)) return []
  const base = "frame"
  return readdirSync(task.work)
    .filter((name) => name.startsWith(base) && name.endsWith(".png"))
    .sort()
    .map((name) => join(task.work, name))
}

export function taskStatus(task: Background) {
  const text = logTail(task.log, 256 * 1024)
  const crash = crashOf(text)
  const errors = errorsOf(text)
  const frames = frameFiles(task)
  const failedExit = task.exit !== undefined && task.exit !== 0
  const state = task.cancelled
    ? "cancelled"
    : running(task)
      ? "running"
      : crash || failedExit || task.error || (task.kind === "capture" && !frames.length)
        ? "failed"
        : "done"
  return {
    task: task.id,
    kind: task.kind,
    state,
    scene: task.scene,
    frames: task.frames,
    elapsed_s: Math.round(((task.finished ?? Date.now()) - task.started) / 100) / 10,
    ...(task.exit !== undefined ? { exit_code: task.exit } : {}),
    ...(crash ? { crash: `the background Godot crashed: ${crash}. The open editor is not affected.` } : {}),
    ...(task.error ? { error: task.error } : {}),
    ...(state === "failed" && !crash && !task.error && task.kind === "capture" && !frames.length
      ? { error: "Movie Maker wrote no frame: see the log." }
      : {}),
    ...(errors.length ? { errors } : {}),
    ...(task.movie
      ? { movie: { folder: task.work, written: frames.length, first: frames[0], last: frames.at(-1) } }
      : {}),
    log: task.log,
  }
}

// The frames a capture shows: the first, the middle and the last.
export function previewFrames(task: Background) {
  const frames = frameFiles(task)
  if (frames.length <= 3) return frames
  return [frames[0], frames[Math.floor(frames.length / 2)], frames.at(-1)] as string[]
}

// A background Godot and the processes it started.
export function stopTree(pid: number) {
  // A process that never started has no PID: never signal 0 (a process group).
  if (!Number.isInteger(pid) || pid <= 0) return
  if (process.platform === "win32") {
    try {
      execFileSync("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" })
      return
    } catch {}
  }
  try {
    process.kill(pid)
  } catch {}
}

export function cancelTask(task: Background) {
  task.cancelled = true
  if (running(task)) stopTree(task.pid)
  return taskStatus(task)
}

type Step = { step?: string; at_s?: number; [key: string]: unknown }

export function stepsOf(text: string): Step[] {
  return text
    .split(/\r?\n/)
    .filter((line) => line.startsWith("OPENCODE_STEP "))
    .flatMap((line) => {
      try {
        return [JSON.parse(line.slice("OPENCODE_STEP ".length)) as Step]
      } catch {
        return []
      }
    })
}

// What a step says the background Godot was doing, in words.
export function describeStep(step: Step | undefined, scene: string) {
  switch (step?.step) {
    case undefined:
      return "starting (Godot had not loaded the scene yet)"
    case "load":
      return `loading ${scene}`
    case "loaded":
      return `loaded ${scene} in ${step.seconds} s`
    case "running":
      return `running ${step.frames} frames of ${scene}`
    case "criterion":
      return `measuring criterion ${step.index}/${step.of} "${step.label}"`
    case "done":
      return "writing its result"
    default:
      return String(step?.step)
  }
}

export type WaitDeps = { now?: () => number; sleep?: (ms: number) => Promise<void>; signal?: AbortSignal }

// Waits for a background Godot's result file, at most timeoutS from now; stopped past it, and the answer says where it
// was. Its folder is removed once it has exited.
export async function waitResult(task: Background, result: string, timeoutS: number, deps: WaitDeps = {}) {
  const now = deps.now ?? Date.now
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((done) => setTimeout(done, ms)))
  const deadline = now() + timeoutS * 1_000
  try {
    for (;;) {
      if (existsSync(result) && statSync(result).size > 0) {
        const got = JSON.parse(readFileSync(result, "utf8")) as Record<string, unknown>
        if (typeof got.error === "string") throw new Error(`the background Godot reading ${task.scene}: ${got.error}`)
        return got
      }
      if (!running(task)) {
        await sleep(200)
        if (existsSync(result)) continue
        const text = logTail(task.log)
        const crash = crashOf(text)
        const last = describeStep(stepsOf(text).at(-1), task.scene)
        throw new Error(
          crash
            ? `the background Godot crashed while ${last}: ${crash}. The open editor is not affected. Log: ${task.log}`
            : `the background Godot stopped without a result (exit code ${task.exit}) while ${last}${task.error ? `: ${task.error}` : ""}. ${errorsOf(text, 5).join(" | ")}`,
        )
      }
      if (deps.signal?.aborted || now() >= deadline) {
        stopTree(task.pid)
        const last = describeStep(stepsOf(logTail(task.log)).at(-1), task.scene)
        throw new Error(
          `${deps.signal?.aborted ? "godot_verify was cancelled" : `godot_verify stopped the background Godot after ${timeoutS} s (timeout_s)`}: it was ${last}. Give more time with timeout_s (up to 3600), or fewer frames.`,
        )
      }
      await sleep(250)
    }
  } finally {
    void (async () => {
      for (let attempt = 0; attempt < 50 && running(task); attempt++) await sleep(200)
      rmSync(task.work, { recursive: true, force: true })
    })()
  }
}

export const ensureDir = (dir: string) => mkdirSync(dir, { recursive: true })
