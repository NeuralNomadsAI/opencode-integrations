import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { it } from "node:test"
import { readStoredZip } from "../../../packages/integration-kit/src/zip.ts"
import marker from "../plugin/package.json" with { type: "json" }

it("the installed Gaffer panel opens automatically, queues early input and follows Save As", () => {
  const payload = mkdtempSync(join(tmpdir(), "gaffer-panel-"))
  try {
    for (const entry of readStoredZip(
      readFileSync(join(import.meta.dirname, "../plugin", marker.opencodeIntegration.host.payload)),
    )) {
      const file = join(payload, entry.name)
      mkdirSync(dirname(file), { recursive: true })
      writeFileSync(file, entry.data)
    }
    const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
    const probe = spawnSync(python, ["-I", join(import.meta.dirname, "panel_probe.py"), payload], {
      encoding: "utf8",
      windowsHide: true,
    })
    assert.equal(probe.status, 0, probe.stderr)
  } finally {
    rmSync(payload, { recursive: true, force: true })
  }
})
