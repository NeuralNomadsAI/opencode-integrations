// The installed part: a content-addressed payload in the plugin's state folder, started by a managed loader in
// Gaffer's GUI startup folder (neuralnomadsOpenCode.py). Gaffer runs every startup file of that folder when its
// interface starts.

import { createHash, randomUUID } from "node:crypto"
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs"
import { dirname, isAbsolute, join, resolve } from "node:path"
import { readStoredZip } from "../../../../packages/integration-kit/src/zip.ts"
import type { HostInspection, InstallHostOutput, IntegrationMarker } from "./kit.ts"
import { packageRoot, stateDir } from "./paths.ts"

export const LOADER = "neuralnomadsOpenCode.py"
export const HEADER = "# Managed by neuralnomads.gaffer\n"

// The installed version: the manifest of the payload the managed loader starts.
export function inspectHost(startup?: string): HostInspection {
  if (!startup || !existsSync(startup)) return { kind: "not-inspected" }
  const loader = join(startup, LOADER)
  try {
    if (statSync(loader).size > 64 * 1024) return { kind: "not-inspected" }
    const source = readFileSync(loader, "utf8")
    if (!source.startsWith(HEADER)) return { kind: "not-inspected" }
    const started = /run_path\("([^"]+)\/startup\/gui\/openCode\.py"/.exec(source)?.[1]
    if (!started) return { kind: "not-inspected" }
    const version = JSON.parse(readFileSync(join(started, "manifest.json"), "utf8")).version
    return typeof version === "string" ? { kind: "found", version } : { kind: "not-inspected" }
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "ENOENT" ? { kind: "not-found" } : { kind: "not-inspected" }
  }
}

export function installHost(marker: IntegrationMarker, startup?: string, state = stateDir()): InstallHostOutput {
  if (!startup || !isAbsolute(startup))
    return { outcome: "refused", detail: "hostStartupDir must be an absolute Gaffer GUI startup folder." }
  const loader = join(startup, LOADER)
  if (existsSync(loader) && !readFileSync(loader, "utf8").startsWith(HEADER))
    return { outcome: "refused", detail: `${loader} exists and is not managed by this integration.` }
  const host = marker.host
  if (!host) return { outcome: "refused" }
  const archive = readFileSync(join(packageRoot, host.payload))
  if (createHash("sha256").update(archive).digest("hex") !== host.sha256)
    return { outcome: "failed", detail: "Host payload checksum mismatch." }
  const entries = readStoredZip(archive)
  const payload = join(state, "hosts", host.sha256)
  const staging = `${payload}-${randomUUID()}`
  const source = `${HEADER}import runpy\nrunpy.run_path(${JSON.stringify(
    `${resolve(payload).replaceAll("\\", "/")}/startup/gui/openCode.py`,
  )}, init_globals={"application": application})\n`
  const current = inspectHost(startup)
  const same =
    existsSync(payload) &&
    entries.every((entry) => {
      const file = join(payload, entry.name)
      return existsSync(file) && readFileSync(file).equals(entry.data)
    })
  if (same && existsSync(loader) && readFileSync(loader, "utf8") === source) return { outcome: "unchanged" }
  // Content-addressed immutable payload: a damaged directory is never silently trusted or overwritten.
  if (existsSync(payload) && !same)
    return { outcome: "failed", detail: "The installed host payload is damaged; remove its folder before retrying." }
  const temporary = `${loader}-${randomUUID()}`
  try {
    if (!same) {
      for (const entry of entries) {
        const file = join(staging, entry.name)
        mkdirSync(dirname(file), { recursive: true })
        writeFileSync(file, entry.data)
      }
      renameSync(staging, payload)
    }
    mkdirSync(startup, { recursive: true })
    writeFileSync(temporary, source)
    renameSync(temporary, loader)
  } finally {
    rmSync(staging, { recursive: true, force: true })
    rmSync(temporary, { force: true })
  }
  return {
    outcome: current.kind === "found" ? "updated" : "installed",
    detail: "Restart Gaffer: OpenCode Link offers its scripts to OpenCode when it starts.",
  }
}
