// Background Godots: what their log says (errors, a crash), their state as a task, and a read stopped past its time.

import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { existsSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"

import {
  cancelTask,
  crashOf,
  describeStep,
  errorsOf,
  startBackground,
  taskStatus,
  waitResult,
} from "../plugin/src/tasks.ts"

const LOG = `Godot Engine v4.7.2.stable.official
OPENCODE_STEP {"step":"load","at_s":0}
SCRIPT ERROR: Invalid access to property or key 'speed' on a base object of type 'null instance'.
   at: _process (res://player.gd:12)
ERROR: Condition "!is_inside_tree()" is true. Returning: Transform3D()
   at: get_global_transform (scene/3d/node_3d.cpp:348)
OPENCODE_STEP {"step":"running","frames":10,"at_s":0.2}

================================================================
CrashHandlerException: Program crashed with signal 11
Engine version: Godot Engine v4.7.2.stable.official
Dumping the backtrace.
[1] 7ff6 (main+40d4d03)
-- END OF C++ BACKTRACE --
`

it("reads errors and a crash from a background Godot's log", () => {
  assert.deepEqual(errorsOf(LOG), [
    "SCRIPT ERROR: Invalid access to property or key 'speed' on a base object of type 'null instance'. (at: _process (res://player.gd:12))",
    'ERROR: Condition "!is_inside_tree()" is true. Returning: Transform3D() (at: get_global_transform (scene/3d/node_3d.cpp:348))',
  ])
  assert.match(String(crashOf(LOG)), /^CrashHandlerException: Program crashed with signal 11 \| Engine version/)
  assert.equal(crashOf("all fine"), undefined)
  assert.equal(describeStep({ step: "running", frames: 10 }, "res://main.tscn"), "running 10 frames of res://main.tscn")
  assert.equal(describeStep(undefined, "x"), "starting (Godot had not loaded the scene yet)")
})

// A stand-in for Godot: Node, told what to do by its arguments, writing its log where Godot's --log-file says.
const standIn = (behaviour: string) => (_command: string, args: string[]) => {
  const log = args[args.indexOf("--log-file") + 1] as string
  return spawn(process.execPath, ["-e", behaviour, log], { windowsHide: true, stdio: "ignore" })
}

const work = () => mkdtempSync(join(tmpdir(), "godot-task-"))

it("a run is running, then done when Godot exits 0; failed with its crash otherwise", async () => {
  const ok = startBackground(
    { kind: "run", executable: "godot", project: "p", args: [], work: work(), scene: "res://main.tscn", frames: 10 },
    standIn("require('fs').writeFileSync(process.argv[1], 'started\\n'); setTimeout(() => {}, 300)"),
  )
  assert.equal(taskStatus(ok).state, "running")
  await new Promise((done) => setTimeout(done, 1_500))
  assert.equal(taskStatus(ok).state, "done")
  assert.equal(taskStatus(ok).exit_code, 0)

  const crashed = startBackground(
    { kind: "run", executable: "godot", project: "p", args: [], work: work(), scene: "res://crash.tscn", frames: 10 },
    standIn(`require('fs').writeFileSync(process.argv[1], ${JSON.stringify(LOG)}); process.exit(3)`),
  )
  await new Promise((done) => setTimeout(done, 1_500))
  const status = taskStatus(crashed)
  assert.equal(status.state, "failed")
  assert.match(String(status.crash), /Program crashed with signal 11.*The open editor is not affected/)
  assert.equal(status.errors?.length, 2)

  const capture = startBackground(
    {
      kind: "capture",
      executable: "godot",
      project: "p",
      args: [],
      work: work(),
      scene: "res://main.tscn",
      frames: 3,
      movie: "frame.png",
    },
    standIn("require('fs').writeFileSync(process.argv[1], '')"),
  )
  await new Promise((done) => setTimeout(done, 1_500))
  assert.equal(taskStatus(capture).state, "failed")
  assert.match(String(taskStatus(capture).error), /Movie Maker wrote no frame/)
})

it("cancel stops a running Godot", async () => {
  const task = startBackground(
    { kind: "run", executable: "godot", project: "p", args: [], work: work(), scene: "s", frames: 1 },
    standIn("setTimeout(() => {}, 60000)"),
  )
  await new Promise((done) => setTimeout(done, 300))
  assert.equal(cancelTask(task).state, "cancelled")
  await new Promise((done) => setTimeout(done, 1_000))
  assert.notEqual(task.finished, undefined)
})

it("a read past timeout_s is stopped and says what Godot was doing; a result is read back", async () => {
  const slow = work()
  const task = startBackground(
    { kind: "verify", executable: "godot", project: "p", args: [], work: slow, scene: "res://main.tscn", frames: 600 },
    standIn(
      `require('fs').writeFileSync(process.argv[1], 'OPENCODE_STEP {"step":"running","frames":600}\\n'); setTimeout(() => {}, 60000)`,
    ),
  )
  await assert.rejects(
    waitResult(task, join(slow, "result.json"), 1),
    /stopped the background Godot after 1 s \(timeout_s\): it was running 600 frames of res:\/\/main\.tscn/,
  )
  const fast = work()
  const result = join(fast, "result.json")
  const done = startBackground(
    { kind: "verify", executable: "godot", project: "p", args: [], work: fast, scene: "res://main.tscn", frames: 0 },
    standIn(`require('fs').writeFileSync(${JSON.stringify(result)}, JSON.stringify({ passed: true }))`),
  )
  assert.deepEqual(await waitResult(done, result, 30), { passed: true })
  await new Promise((resolve) => setTimeout(resolve, 1_500))
  assert.equal(existsSync(fast), false, "its folder is removed once it has exited")
  const stopped = work()
  writeFileSync(join(stopped, "unused"), "")
  const quits = startBackground(
    { kind: "verify", executable: "godot", project: "p", args: [], work: stopped, scene: "res://main.tscn", frames: 0 },
    standIn(`require('fs').writeFileSync(process.argv[1], ${JSON.stringify(LOG)}); process.exit(1)`),
  )
  await assert.rejects(waitResult(quits, join(stopped, "result.json"), 30), /crashed while running 10 frames/)
})
