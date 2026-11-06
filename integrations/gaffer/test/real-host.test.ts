import assert from "node:assert/strict"
import { execFile } from "node:child_process"
import { mkdirSync, mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"
import { promisify } from "node:util"

it("real Gaffer: hello, bounded graph read, atomic edit, idempotency, stale revision and undo", {
  skip: !process.env.GAFFER_TEST_BIN,
}, async () => {
  const scratch = mkdtempSync(join(tmpdir(), "gaffer-real-"))
  mkdirSync(join(scratch, "temp"))
  const bin = process.env.GAFFER_TEST_BIN as string
  const script = join(import.meta.dirname, "python", "real_host.py")
  try {
    const env = {
      ...process.env,
      HOME: scratch,
      USERPROFILE: scratch,
      TEMP: join(scratch, "temp"),
      TMP: join(scratch, "temp"),
      XDG_CONFIG_HOME: join(scratch, "config"),
      XDG_STATE_HOME: join(scratch, "state"),
      QT_QPA_PLATFORM: "offscreen",
      GAFFER_EXTENSION_PATHS: "",
      GAFFER_STARTUP_PATHS: "",
    }
    const command = process.platform === "win32" ? (process.env.ComSpec ?? "cmd.exe") : bin
    const args = process.platform === "win32" ? ["/d", "/s", "/c", `""${bin}" python "${script}""`] : ["python", script]
    const { stdout } = await promisify(execFile)(command, args, {
      env,
      cwd: scratch,
      windowsHide: true,
      windowsVerbatimArguments: process.platform === "win32",
      timeout: 90_000,
      maxBuffer: 4 * 1024 * 1024,
    })
    assert.match(stdout, /GAFFER_REAL_OK/)
    assert.match(stdout, /GAFFER_PANEL_OK/)
    assert.match(stdout, /GAFFER_WIRE_OK/)
  } finally {
    rmSync(scratch, { recursive: true, force: true })
  }
})
