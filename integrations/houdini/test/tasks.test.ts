// Render and PDG tasks: a background hython that crashed is said as a crash, with its first frames, and with the
// render program's risk note when it had one (the Sol trial's ex07 render log said only "stopped").

import assert from "node:assert/strict"
import { mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"

import { addTask, crashOf, taskStatus } from "../plugin/src/tasks.ts"

// The head of the ex07 render job's log (Houdini 22.0.429, an OpenGL ROP with High Quality Lighting off).
const CRASHED = [
  'OPENCODE_RENDER {"node": "/out/preview", "type": "Driver/opengl", "frames": [1, 1], "count": 1}',
  "20052: Fatal error: Segmentation fault",
  "-- TRACEBACK BEGIN --",
  "Traceback from hython 22.0.429 (Compiled on windows-x86_64-cl19.42):",
  "CURRENT THREAD 6560",
  "+0x117291600 [RV_VKImage::getInfo] C:\\PROGRA~1\\SIDEEF~1\\HOUDIN~1.429\\bin\\libRV.dll",
  "+0x118342303 [DM_RenderVulkan::resizeTransparentBuffer] C:\\PROGRA~1\\SIDEEF~1\\HOUDIN~1.429\\bin\\libDM.dll",
  "+0x11833a127 [DM_RenderVulkan::renderHeadlight] C:\\PROGRA~1\\SIDEEF~1\\HOUDIN~1.429\\bin\\libDM.dll",
  "+0x11833b296 [DM_RenderVulkan::renderLighting] C:\\PROGRA~1\\SIDEEF~1\\HOUDIN~1.429\\bin\\libDM.dll",
  "-- TRACEBACK END --",
].join("\n")

it("a crashed render hython is said as a crash with its first frames and the ROP's risk note", () => {
  assert.equal(
    crashOf(CRASHED),
    "Segmentation fault in RV_VKImage::getInfo < DM_RenderVulkan::resizeTransparentBuffer < DM_RenderVulkan::renderHeadlight",
  )
  assert.equal(crashOf("OPENCODE_PROGRESS {}\nfine"), undefined)
  const dir = mkdtempSync(join(tmpdir(), "houdini-task-"))
  try {
    const log = join(dir, "render.log")
    writeFileSync(log, CRASHED)
    const task = addTask({
      // This test process, alive: a crash is said even while the crashed hython is still being torn down.
      pid: process.pid,
      log,
      kind: "render",
      targets: [join(dir, "preview.png")],
      node: "/out/preview",
      facts: { crash_risk: "High Quality Lighting is off on this OpenGL ROP (hqlighting 0): set hqlighting to 1" },
    })
    const status = taskStatus(task) as { state: string; error?: string }
    assert.equal(status.state, "failed")
    assert.match(
      String(status.error),
      /^the background hython crashed \(Segmentation fault in RV_VKImage::getInfo < .*\); the open Houdini is not affected\. High Quality Lighting is off on this OpenGL ROP .* See /,
    )
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})
