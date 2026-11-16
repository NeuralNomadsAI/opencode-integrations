// Render tasks: a background Gaffer that crashed is said as a crash, with its first frames, and with the render
// program's risk note when it had one; a job that says it finished without its files is a failure.

import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"

import { isAlive } from "../plugin/src/kit.ts"
import { addTask, cancelTask, crashOf, isRenderStart, taskStatus } from "../plugin/src/tasks.ts"

// The head of a background Gaffer render log whose renderer crashed.
const CRASHED = [
  'OPENCODE_RENDER {"node": "/Render", "type": "GafferCycles::CyclesRender", "frames": [1, 1], "count": 1}',
  "Fatal error: Segmentation fault",
  "CURRENT THREAD 6560",
  "+0x7ff8 [ccl::BVH2::build] libcycles.so",
  "+0x7ff9 [ccl::Device::build_bvh] libcycles.so",
  "+0x7ffa [IECoreCycles::Renderer::render] libIECoreCycles.so",
  "+0x7ffb [ccl::BVH2::build] libcycles.so",
].join("\n")

function started(dir: string, log: string, extra: Record<string, unknown> = {}) {
  const file = join(dir, "render.log")
  writeFileSync(file, log)
  return addTask({
    // This test process, alive: a crash is said even while the crashed Gaffer is still being torn down.
    pid: process.pid,
    log: file,
    kind: "render",
    targets: [join(dir, "beauty.0001.exr")],
    node: "/Render",
    start: 1,
    ...extra,
  })
}

it("a crashed render Gaffer is said as a crash with its first frames and the render's risk note", () => {
  assert.equal(
    crashOf(CRASHED),
    "Segmentation fault in ccl::BVH2::build < ccl::Device::build_bvh < IECoreCycles::Renderer::render",
  )
  assert.equal(crashOf("Caught signal 11\n"), "signal 11")
  assert.equal(crashOf("OPENCODE_PROGRESS {}\nfine"), undefined)
  const dir = mkdtempSync(join(tmpdir(), "gaffer-task-"))
  try {
    const task = started(dir, CRASHED, { facts: { crash_risk: "This scene has 3 million instances" } })
    const status = taskStatus(task) as { state: string; error?: string; kind: string; frames?: number[] }
    assert.equal(status.kind, "render")
    assert.equal(status.state, "failed")
    assert.deepEqual(status.frames, [1, 1])
    assert.match(
      String(status.error),
      /^the background Gaffer crashed \(Segmentation fault in ccl::BVH2::build < .*\); the open Gaffer is not affected\. This scene has 3 million instances\. See /,
    )
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

it("a render is done only when it says so and every expected file is there, not empty", () => {
  const dir = mkdtempSync(join(tmpdir(), "gaffer-task-"))
  try {
    const rendering = started(dir, 'OPENCODE_PROGRESS {"done": 0, "total": 1}\n')
    assert.deepEqual(
      [taskStatus(rendering).state, taskStatus(rendering).progress],
      ["rendering", { done: 0, total: 1 }],
    )
    const claimed = started(dir, 'OPENCODE_DONE {"output": "beauty"}\n')
    const failed = taskStatus(claimed) as { state: string; error?: string }
    assert.equal(failed.state, "failed")
    assert.match(String(failed.error), /1 of 1 expected file\(s\) are missing or empty: beauty\.0001\.exr/)
    writeFileSync(join(dir, "beauty.0001.exr"), "exr")
    const done = taskStatus(claimed) as { state: string; result?: unknown }
    assert.equal(done.state, "done")
    assert.deepEqual(done.result, { output: "beauty" })
    // Only render tasks: a PDG-like start is not one.
    assert.equal(isRenderStart({ pid: 1, log: "x", kind: "pdg", targets: [] }), false)
    assert.equal(isRenderStart({ pid: 1, log: "x", kind: "render", targets: [] }), true)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

// gaffer.cmd runs gaffer.exe under cmd.exe: cancelling the task (the launcher's pid) stops the process it started too.
it("a cancelled render stops the launcher and the process it started", {
  skip: process.platform !== "win32",
}, async () => {
  const dir = mkdtempSync(join(tmpdir(), "gaffer-cancel-"))
  try {
    const log = join(dir, "render.log")
    writeFileSync(log, 'OPENCODE_RENDER {"node": "/ImageWriter"}\n')
    const launcher = spawn("cmd.exe", ["/d", "/c", "ping -n 60 127.0.0.1 > nul"], { windowsHide: true })
    await new Promise((done) => setTimeout(done, 1_000))
    const { execFileSync } = await import("node:child_process")
    const children = execFileSync(
      "powershell.exe",
      ["-NoProfile", "-Command", `(Get-CimInstance Win32_Process -Filter "ParentProcessId=${launcher.pid}").ProcessId`],
      { encoding: "utf8", windowsHide: true },
    )
      .split(/\s+/)
      .filter(Boolean)
      .map(Number)
    assert.ok(children.length > 0, "the launcher started its process")
    const task = addTask({
      pid: launcher.pid as number,
      log,
      kind: "render",
      targets: [join(dir, "x.exr")],
      node: "/ImageWriter",
    })
    assert.equal((cancelTask(task) as { state: string }).state, "cancelled")
    await new Promise((done) => setTimeout(done, 500))
    assert.equal(isAlive(launcher.pid as number), false)
    for (const child of children) assert.equal(isAlive(child), false, `process ${child} still runs`)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})
