// The fixed programs, the hints and the installed part itself (execute_code with its undo, status, saves) in a
// real Houdini: opt-in, with HOUDINI_TEST_BIN naming hython or Houdini's bin folder. The run uses private
// preferences, packages and temp folders (programs_hython.py), never the user's.

import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { join } from "node:path"
import { it } from "node:test"

it("programs, hints and the installed part in a real Houdini (HOUDINI_TEST_BIN)", {
  skip: !process.env.HOUDINI_TEST_BIN,
}, () => {
  const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
  const run = spawnSync(python, ["-I", join(import.meta.dirname, "programs_hython.py")], {
    windowsHide: true,
    encoding: "utf8",
    timeout: 1_800_000,
    maxBuffer: 64 * 1024 * 1024,
  })
  const report = (run.stdout ?? "")
    .split(/\r?\n/)
    .filter((line) => / (ok|FAIL) +\d/.test(line) || /passed, \d+ failed/.test(line))
  assert.equal(run.status, 0, `${report.join("\n")}\n${(run.stdout ?? "").slice(-4000)}${run.stderr ?? ""}`)
})
