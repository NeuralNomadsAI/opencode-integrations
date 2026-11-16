// gaffer_verify saved follows its background Gaffer outside Gaffer's main thread: a result, a crash, or
// timeout_s, after which it says where the background Gaffer was.

import assert from "node:assert/strict"
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"

import { isSavedStart, type SavedStart, waitSaved } from "../plugin/src/saved-verify.ts"

const step = (what: Record<string, unknown>) => `OPENCODE_STEP ${JSON.stringify(what)}`

function started(log: string): SavedStart {
  const work = mkdtempSync(join(tmpdir(), "gaffer-saved-"))
  mkdirSync(work, { recursive: true })
  writeFileSync(join(work, "verify.log"), log)
  return {
    pid: 4242,
    work,
    log: join(work, "verify.log"),
    result: join(work, "result.json"),
    file: "D:/shots/shot.gfr",
    saved_at: "2026-11-16 07:25:51",
    bytes: 48213,
    timeout_s: 5,
  }
}

// A clock that advances with each wait: no real waiting.
function clock() {
  let t = 1_000_000
  return {
    now: () => t,
    sleep: async (ms: number) => {
      t += ms
    },
  }
}

it("a verify that runs out of time is stopped and says what it was doing", async () => {
  const start = started(
    [
      step({ step: "load", file: "D:/shots/shot.gfr", at_s: 0 }),
      step({ step: "loaded", seconds: 0.4, at_s: 0.4 }),
      ...Array.from({ length: 11 }, (_, i) =>
        step({ step: "criterion", index: i + 1, of: 16, label: `check ${i + 1}`, frame: 48, at_s: 0.5 + i * 0.1 }),
      ),
    ].join("\n"),
  )
  const stopped: number[] = []
  try {
    assert.ok(isSavedStart(start))
    await assert.rejects(
      waitSaved(start, { ...clock(), alive: () => true, stop: (pid) => stopped.push(pid) }),
      (error: Error) => {
        assert.match(error.message, /gaffer_verify saved stopped the background Gaffer after 5 s \(timeout_s\)/)
        assert.match(error.message, /it was measuring criterion 11\/16 "check 11" at frame 48/)
        assert.match(error.message, /The file loaded in 0\.4 s; 10 criteria were measured before\./)
        assert.match(error.message, /give more time with timeout_s \(up to 3600\)/)
        return true
      },
    )
    assert.deepEqual(stopped, [4242])
    assert.equal(existsSync(start.work), false)
  } finally {
    rmSync(start.work, { recursive: true, force: true })
  }
})

it("a verify that finishes gives its result and what it read", async () => {
  const start = started(`${step({ step: "load", at_s: 0 })}\n${step({ step: "done", at_s: 1 })}`)
  writeFileSync(start.result, JSON.stringify({ passed: 16, failed: 0, load_seconds: 0.4, load_warnings: [] }))
  try {
    const result = (await waitSaved(start, { ...clock(), alive: () => true })) as {
      passed: number
      read: Record<string, unknown>
    }
    assert.equal(result.passed, 16)
    assert.equal(result.read.file, "D:/shots/shot.gfr")
    assert.equal(result.read.load_seconds, 0.4)
    assert.match(String(result.read.by), /background Gaffer on the file as written on disk, not the open script/)
    assert.equal("load_warnings" in result.read, false)
    assert.equal(existsSync(start.work), false)
  } finally {
    rmSync(start.work, { recursive: true, force: true })
  }
})

it("a verify whose background Gaffer crashed says so, with where it was", async () => {
  const start = started(
    [
      step({ step: "loaded", seconds: 1, at_s: 1 }),
      step({ step: "render", at_s: 2 }),
      "Fatal error: Segmentation fault",
      "CURRENT THREAD 1",
      "+0x1 [IECoreGL::Renderer::render] libIECoreGL.so",
    ].join("\n"),
  )
  try {
    await assert.rejects(waitSaved(start, { ...clock(), alive: () => false }), (error: Error) => {
      assert.match(
        error.message,
        /crashed \(Segmentation fault in IECoreGL::Renderer::render\) while rendering the check image; the open Gaffer is not affected/,
      )
      return true
    })
  } finally {
    rmSync(start.work, { recursive: true, force: true })
  }
})

it("a verify that reports an error says the file it read", async () => {
  const start = started(step({ step: "loaded", seconds: 1, at_s: 1 }))
  writeFileSync(start.result, JSON.stringify({ error: "KeyError: 'Sphere'" }))
  try {
    await assert.rejects(
      waitSaved(start, { ...clock(), alive: () => true }),
      /verify in the background Gaffer reading D:\/shots\/shot\.gfr failed:\nKeyError: 'Sphere'/,
    )
  } finally {
    rmSync(start.work, { recursive: true, force: true })
  }
})
