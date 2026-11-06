import { createHash, randomUUID } from "node:crypto"
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"

import type {
  HostInspection,
  InstallHostOutput,
  IntegrationMarker,
} from "../../../../packages/integration-kit/src/index.ts"
import { readStoredZip } from "../../../../packages/integration-kit/src/zip.ts"
import { packageRoot, stateDir } from "./paths.ts"

// Houdini's preference folder is version-specific. The user selects it explicitly; never guess a version.
export function inspectHost(preferences?: string): HostInspection {
  if (!preferences || !existsSync(preferences)) return { kind: "not-inspected" }
  try {
    const file = join(preferences, "packages", "neuralnomads_houdini.json")
    if (statSync(file).size > 1024 * 1024) return { kind: "not-inspected" }
    const pkg = JSON.parse(readFileSync(file, "utf8"))
    const version = pkg.neuralnomadsHostVersion
    return typeof version === "string" ? { kind: "found", version } : { kind: "not-inspected" }
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "ENOENT" ? { kind: "not-found" } : { kind: "not-inspected" }
  }
}

export function installHost(marker: IntegrationMarker, preferences?: string, state = stateDir()): InstallHostOutput {
  if (!preferences || !existsSync(preferences))
    return {
      outcome: "refused",
      detail: "Set hostInspectionDir to the existing Houdini user preferences folder, then retry.",
    }
  const host = marker.host
  if (!host) return { outcome: "refused" }
  const archive = readFileSync(join(packageRoot, host.payload))
  if (createHash("sha256").update(archive).digest("hex") !== host.sha256) return { outcome: "failed" }
  const entries = readStoredZip(archive)
  const manifest = JSON.parse(entries.find((entry) => entry.name === "manifest.json")?.data.toString() ?? "null")
  if (!manifest?.version) return { outcome: "failed" }
  const payload = join(state, "hosts", host.sha256)
  const staging = `${payload}-${randomUUID()}`
  const native = join(preferences, "packages", "neuralnomads_houdini.json")
  const config = `${JSON.stringify(
    {
      enable: true,
      neuralnomadsHostVersion: manifest.version,
      env: [
        { NEURALNOMADS_HOUDINI_ROOT: resolve(payload).replaceAll("\\", "/") },
        { PYTHONPATH: { value: "$NEURALNOMADS_HOUDINI_ROOT/scripts", method: "prepend" } },
      ],
      path: "$NEURALNOMADS_HOUDINI_ROOT",
    },
    null,
    2,
  )}\n`
  const current = inspectHost(preferences)
  const same =
    existsSync(payload) &&
    entries.every((entry) => {
      const file = join(payload, entry.name)
      return existsSync(file) && readFileSync(file).equals(entry.data)
    })
  if (same && existsSync(native) && readFileSync(native, "utf8") === config) return { outcome: "unchanged" }
  // Content-addressed immutable payload: a damaged directory is never silently trusted or overwritten.
  if (existsSync(payload) && !same)
    return {
      outcome: "failed",
      detail: "The installed host payload is damaged; remove its installation before retrying.",
    }
  const temporaryConfig = `${native}-${randomUUID()}`
  try {
    if (!same) {
      for (const entry of entries) {
        const file = join(staging, entry.name)
        mkdirSync(dirname(file), { recursive: true })
        writeFileSync(file, entry.data)
      }
      renameSync(staging, payload)
    }
    mkdirSync(dirname(native), { recursive: true })
    writeFileSync(temporaryConfig, config)
    renameSync(temporaryConfig, native)
  } finally {
    rmSync(staging, { recursive: true, force: true })
    rmSync(temporaryConfig, { force: true })
  }
  return {
    outcome: current.kind === "found" ? "updated" : "installed",
    detail: "Open Houdini, then open the OpenCode Link panel and choose Connect.",
  }
}
