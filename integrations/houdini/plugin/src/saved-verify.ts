// houdini_verify saved: the verify program runs in a background hython started from the open Houdini, which only
// starts it (programs/verify_saved.py). This module follows it outside Houdini's main thread: its result, its log's
// steps (OPENCODE_STEP: what it does now), its crash, and timeout_s, after which it is stopped and the answer says
// where it was.

import { execFileSync } from "node:child_process"
import { existsSync, readFileSync, rmSync } from "node:fs"

import { isAlive } from "./kit.ts"
import { crashOf } from "./tasks.ts"

export type SavedStart = {
  pid: number
  work: string
  log: string
  result: string
  file: string
  saved_at: string
  bytes: number
  timeout_s: number
}

export function isSavedStart(value: unknown): value is SavedStart {
  const start = value as Partial<SavedStart> | undefined
  return (
    typeof start?.pid === "number" &&
    typeof start.log === "string" &&
    typeof start.result === "string" &&
    typeof start.file === "string" &&
    typeof start.timeout_s === "number"
  )
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

// What a step says it does, in words.
export function describeStep(step: Step | undefined, file: string) {
  if (step?.step === undefined) return "starting (hython had not loaded the file yet)"
  switch (step.step) {
    case "load":
      return `loading ${file}`
    case "loaded":
      return `loaded ${file} in ${step.seconds} s, before the first criterion`
    case "criterion":
      return `measuring criterion ${step.index}/${step.of} "${step.label}" at frame ${step.frame}`
    case "errors":
      return "starting the error scan"
    case "cook":
      return `cooking ${step.node} for the error scan`
    case "render":
      return "rendering the check image"
    case "done":
      return "writing its result"
    default:
      return String(step.step)
  }
}

const read = (file: string) => {
  try {
    return readFileSync(file, "utf8")
  } catch {
    return ""
  }
}

export type SavedDeps = {
  alive?: (pid: number) => boolean
  stop?: (pid: number) => void
  now?: () => number
  sleep?: (ms: number) => Promise<void>
  signal?: AbortSignal
}

function stopTree(pid: number) {
  if (process.platform === "win32") {
    // hython may have started its own render hython: the whole tree.
    try {
      execFileSync("taskkill", ["/pid", String(pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore",
      })
      return
    } catch {}
  }
  try {
    process.kill(pid)
  } catch {}
}

// Waits for the background hython's result, at most start.timeout_s from now.
export async function waitSaved(start: SavedStart, deps: SavedDeps = {}): Promise<Record<string, unknown>> {
  const alive = deps.alive ?? isAlive
  const stop = deps.stop ?? stopTree
  const now = deps.now ?? Date.now
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((done) => setTimeout(done, ms)))
  const began = now()
  const deadline = began + start.timeout_s * 1_000
  const elapsed = () => Math.round((now() - began) / 100) / 10
  // hython holds its log until it has exited (after its result, or after being stopped): removed once it lets go,
  // without holding the answer back.
  const cleanup = async () => {
    for (let attempt = 0; attempt < 100; attempt++) {
      try {
        rmSync(start.work, { recursive: true, force: true })
        if (!existsSync(start.work)) return
      } catch {}
      await sleep(200)
    }
  }
  try {
    for (;;) {
      if (existsSync(start.result)) {
        const got = JSON.parse(read(start.result)) as Record<string, unknown>
        if (typeof got.error === "string")
          throw new Error(`verify in the background hython reading ${start.file} failed:\n${got.error}`)
        const { load_warnings, load_seconds, ...result } = got
        return {
          ...result,
          read: {
            file: start.file,
            saved_at: start.saved_at,
            bytes: start.bytes,
            seconds: elapsed(),
            ...(typeof load_seconds === "number" ? { load_seconds } : {}),
            by: "a background hython on the file as written on disk, not the open scene",
            ...(Array.isArray(load_warnings) && load_warnings.length ? { load_warnings } : {}),
          },
        }
      }
      if (!alive(start.pid)) {
        // Its result may land just as it exits.
        await sleep(300)
        if (existsSync(start.result)) continue
        const text = read(start.log)
        const crash = crashOf(text)
        const last = describeStep(stepsOf(text).at(-1), start.file)
        throw new Error(
          crash
            ? `the background hython reading ${start.file} crashed (${crash}) while ${last}; the open Houdini is not affected. Log: ${start.log}`
            : `the background hython reading ${start.file} stopped without a result while ${last}:\n${text.slice(-1200)}`,
        )
      }
      if (deps.signal?.aborted || now() >= deadline) {
        stop(start.pid)
        const steps = stepsOf(read(start.log))
        const last = steps.at(-1)
        const measured = steps.filter((step) => step.step === "criterion").length
        const loaded = steps.find((step) => step.step === "loaded")
        throw new Error(
          `${deps.signal?.aborted ? "houdini_verify saved was cancelled" : `houdini_verify saved stopped the background hython after ${start.timeout_s} s (timeout_s)`}: it was ${describeStep(last, start.file)}. ${loaded ? `The file loaded in ${loaded.seconds} s; ` : ""}${
            measured
              ? `${Math.max(0, measured - (last?.step === "criterion" ? 1 : 0))} criteria were measured before. `
              : ""
          }${
            last?.step === "criterion" || last?.step === "cook"
              ? "Reading frames of a simulation that is not cached simulates them: read its cache (a File SOP), or "
              : ""
          }give more time with timeout_s (up to 3600).`,
        )
      }
      await sleep(500)
    }
  } finally {
    void cleanup()
  }
}
