// The application part on disk: read-only inspection of Blender's extensions repository (HST-5)
// and installation of the embedded add-on, by explicit request only (HST-1, RPC-3).

import { createHash, randomBytes } from "node:crypto"
import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  readSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs"
import { dirname, join } from "node:path"

import type { HostInspection, InstallHostOutput, IntegrationMarker } from "./kit.ts"

import { ADDON_ID, defaultExtensionsDir, type PathEnv, packageRoot } from "./paths.ts"
import { readStoredZip } from "./zip.ts"

const MANIFEST_MAX = 1024 * 1024
const VERSION = /^version\s*=\s*"([^"]{1,64})"/m

// The repository folder to use: the plugin option, else the documented per-platform default.
// `base` must exist for an inspection to count: the folder itself, or the Blender version folder.
export type ExtensionsDir = { dir: string; base: string }

export function extensionsDir(option: string | undefined, paths: PathEnv): ExtensionsDir | null {
  return option ? { dir: option, base: option } : defaultExtensionsDir(paths)
}

function readBounded(file: string): string | null {
  let descriptor: number | undefined
  try {
    descriptor = openSync(file, "r")
    const buffer = Buffer.alloc(MANIFEST_MAX)
    const length = readSync(descriptor, buffer, 0, MANIFEST_MAX, 0)
    return buffer.subarray(0, length).toString("utf8")
  } catch {
    return null
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
  }
}

// One folder, one level deep, at most 1 MiB of manifest read; never returns the path.
export function inspectHost(target: ExtensionsDir | null): HostInspection {
  if (!target || !existsSync(target.base)) return { kind: "not-inspected" }
  const dir = target.dir
  const manifest = readBounded(join(dir, ADDON_ID, "blender_manifest.toml"))
  if (manifest === null) return { kind: "not-found" }
  const version = VERSION.exec(manifest)?.[1]
  return version ? { kind: "found", version } : { kind: "not-found" }
}

function compareVersions(a: string, b: string) {
  const parse = (value: string) =>
    value
      .split(/[.+-]/)
      .slice(0, 3)
      .map((part) => Number.parseInt(part, 10) || 0)
  const [left, right] = [parse(a), parse(b)]
  for (let index = 0; index < 3; index += 1) {
    const difference = (left[index] ?? 0) - (right[index] ?? 0)
    if (difference !== 0) return difference
  }
  return 0
}

export function installHost(marker: IntegrationMarker, target: ExtensionsDir | null): InstallHostOutput {
  const host = marker.host
  if (!host) return { outcome: "refused" }
  if (!target || !existsSync(target.base)) {
    return {
      outcome: "refused",
      detail: "No Blender 5.2 user folder was found: start Blender 5.2 once, or set the hostInspectionDir option.",
    }
  }
  const archive = readFileSync(join(packageRoot, ...host.payload.split("/")))
  if (createHash("sha256").update(archive).digest("hex") !== host.sha256) {
    return { outcome: "failed", detail: "The embedded add-on does not match its checksum; reinstall the plugin." }
  }
  const entries = readStoredZip(archive)
  const manifest = entries.find((entry) => entry.name === "blender_manifest.toml")
  const version = manifest ? VERSION.exec(manifest.data.toString("utf8"))?.[1] : undefined
  if (!version) return { outcome: "failed", detail: "The embedded add-on has no manifest." }

  const dir = target.dir
  const addon = join(dir, ADDON_ID)
  const current = inspectHost(target)
  if (current.kind === "found" && current.version === version) {
    const same = entries.every((entry) => {
      const file = join(addon, ...entry.name.split("/"))
      return existsSync(file) && readFileSync(file).equals(entry.data)
    })
    if (same) return { outcome: "unchanged", detail: `OpenCode Link ${version} is already installed.` }
  }

  mkdirSync(dir, { recursive: true })
  const suffix = randomBytes(6).toString("hex")
  const staging = join(dir, `.${ADDON_ID}-${suffix}`)
  const previous = join(dir, `.${ADDON_ID}-old-${suffix}`)
  try {
    for (const entry of entries) {
      const file = join(staging, ...entry.name.split("/"))
      mkdirSync(dirname(file), { recursive: true })
      writeFileSync(file, entry.data)
    }
    if (existsSync(addon)) renameSync(addon, previous)
    renameSync(staging, addon)
  } catch {
    if (!existsSync(addon) && existsSync(previous)) renameSync(previous, addon)
    rmSync(staging, { recursive: true, force: true })
    return { outcome: "failed", detail: "The add-on could not be written; close Blender and try again." }
  }
  rmSync(previous, { recursive: true, force: true })
  const updated = current.kind === "found"
  const older = updated && compareVersions(current.version, version) < 0
  return {
    outcome: updated ? "updated" : "installed",
    detail: `${older || !updated ? "Installed" : "Reinstalled"} OpenCode Link ${version}. In Blender: Preferences, Add-ons, enable OpenCode Link.`,
  }
}
