// The fixed programs (inspect, verify, verify saved, save, render) in a real Gaffer: opt-in, with GAFFER_TEST_BIN
// naming Gaffer's launcher. The run uses private HOME, USERPROFILE, TEMP and startup paths, never the user's.

import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { mkdirSync, mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"

it("fixed programs in a real Gaffer (GAFFER_TEST_BIN)", { skip: !process.env.GAFFER_TEST_BIN }, () => {
  const scratch = mkdtempSync(join(tmpdir(), "gaffer-programs-"))
  mkdirSync(join(scratch, "temp"))
  const bin = process.env.GAFFER_TEST_BIN as string
  const script = join(import.meta.dirname, "programs_gaffer.py")
  try {
    const env = {
      ...process.env,
      HOME: scratch,
      USERPROFILE: scratch,
      TEMP: join(scratch, "temp"),
      TMP: join(scratch, "temp"),
      TMPDIR: join(scratch, "temp"),
      XDG_CONFIG_HOME: join(scratch, "config"),
      XDG_STATE_HOME: join(scratch, "state"),
      QT_QPA_PLATFORM: "offscreen",
      GAFFER_EXTENSION_PATHS: "",
      GAFFER_STARTUP_PATHS: "",
    }
    const command = process.platform === "win32" ? (process.env.ComSpec ?? "cmd.exe") : bin
    const args = process.platform === "win32" ? ["/d", "/s", "/c", `""${bin}" python "${script}""`] : ["python", script]
    const run = spawnSync(command, args, {
      env,
      cwd: scratch,
      windowsHide: true,
      windowsVerbatimArguments: process.platform === "win32",
      encoding: "utf8",
      timeout: 900_000,
      maxBuffer: 64 * 1024 * 1024,
    })
    const report = (run.stdout ?? "")
      .split(/\r?\n/)
      .filter((line) => / (ok|FAIL) +\d/.test(line) || /passed, \d+ failed/.test(line))
    assert.equal(run.status, 0, `${report.join("\n")}\n${(run.stdout ?? "").slice(-4000)}${run.stderr ?? ""}`)
    assert.match(report.at(-1) ?? "", /^\d+ passed, 0 failed/)
  } finally {
    rmSync(scratch, { recursive: true, force: true })
  }
})
