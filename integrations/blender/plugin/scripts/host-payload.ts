// Generated host artifacts: the add-on's catalog.json (from methods/host), its vendored copy of
// the kit's Python discovery client, and the deterministic payload archive named by the marker.

import { createHash } from "node:crypto"
import { readdirSync, readFileSync } from "node:fs"
import { join, relative, sep } from "node:path"
import { fileURLToPath } from "node:url"

import { writeStoredZip } from "../src/zip.ts"

export const pluginRoot = fileURLToPath(new URL("..", import.meta.url))
export const addonRoot = join(pluginRoot, "..", "host", "opencode_link")
export const kitServicePath = join(
  pluginRoot,
  "..",
  "..",
  "..",
  "packages",
  "integration-kit",
  "python",
  "opencode_service.py",
)

type Manifest = { name: string; kind: string; [key: string]: unknown }

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true })
    .flatMap((entry) => (entry.isDirectory() ? walk(join(dir, entry.name)) : [join(dir, entry.name)]))
    .sort()
}

function lf(bytes: Buffer): Buffer {
  return Buffer.from(bytes.toString("utf8").replace(/\r\n/g, "\n"), "utf8")
}

// Fields of a host manifest the add-on needs to validate and run it.
const HOST_FIELDS = ["name", "description", "undoable", "network", "sideEffect", "capture", "args", "additionalArgs"]

export function catalogBytes(): Buffer {
  const manifests = walk(join(pluginRoot, "methods", "host"))
    .filter((file) => file.endsWith(".json"))
    .map((file) => JSON.parse(readFileSync(file, "utf8")) as Manifest)
    .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
  const pick = (manifest: Manifest) => {
    const entry: Record<string, unknown> = {}
    for (const field of HOST_FIELDS) if (manifest[field] !== undefined) entry[field] = manifest[field]
    entry.execution = { handler: (manifest.execution as { handler: string }).handler }
    return entry
  }
  const catalog = {
    reads: manifests.filter((manifest) => manifest.kind === "host-read").map(pick),
    operations: manifests.filter((manifest) => manifest.kind === "host-operation").map(pick),
  }
  return Buffer.from(`${JSON.stringify(catalog, null, 2)}\n`, "utf8")
}

export function serviceBytes(): Buffer {
  return lf(readFileSync(kitServicePath))
}

export function addonVersion(): string {
  const manifest = readFileSync(join(addonRoot, "blender_manifest.toml"), "utf8")
  const match = /^version\s*=\s*"([^"]+)"/m.exec(manifest)
  if (!match?.[1]) throw new Error("blender_manifest.toml has no version")
  return match[1]
}

// The add-on files, with the generated files taken from their sources, at the archive root.
export function payloadEntries(): Array<{ name: string; data: Buffer }> {
  const generated = new Map([
    ["catalog.json", catalogBytes()],
    ["opencode_service.py", serviceBytes()],
  ])
  const files = walk(addonRoot)
    .map((file) => relative(addonRoot, file).split(sep).join("/"))
    .filter((name) => !name.split("/").includes("__pycache__") && !name.endsWith(".pyc"))
  for (const name of generated.keys()) if (!files.includes(name)) files.push(name)
  return files.sort().map((name) => ({ name, data: generated.get(name) ?? lf(readFileSync(join(addonRoot, name))) }))
}

export function payloadBytes(): Buffer {
  return writeStoredZip(payloadEntries())
}

export function payloadName(version: string) {
  return `host/blender/opencode_link-${version}.zip`
}

export function sha256(bytes: Buffer) {
  return createHash("sha256").update(bytes).digest("hex")
}
