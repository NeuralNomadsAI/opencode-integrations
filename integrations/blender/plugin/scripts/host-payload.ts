// Generated host artifacts: the add-on's vendored copy of the kit's Python discovery client, and the
// deterministic payload archive named by the marker. The add-on's own files (its catalog.json included)
// are taken as they are.

import { createHash } from "node:crypto"
import { readdirSync, readFileSync } from "node:fs"
import { join, relative, sep } from "node:path"
import { fileURLToPath } from "node:url"

import { writeStoredZip } from "../src/zip.ts"

export const pluginRoot = fileURLToPath(new URL("..", import.meta.url))
export const hostRoot = join(pluginRoot, "..", "host")
export const addonRoot = join(hostRoot, "opencode_link")
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

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true })
    .flatMap((entry) => (entry.isDirectory() ? walk(join(dir, entry.name)) : [join(dir, entry.name)]))
    .sort()
}

function lf(bytes: Buffer): Buffer {
  return Buffer.from(bytes.toString("utf8").replace(/\r\n/g, "\n"), "utf8")
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

// The add-on files, with the vendored discovery client taken from the kit, at the archive root.
export function payloadEntries(): Array<{ name: string; data: Buffer }> {
  const files = walk(addonRoot)
    .map((file) => relative(addonRoot, file).split(sep).join("/"))
    .filter((name) => !name.split("/").includes("__pycache__") && !name.endsWith(".pyc"))
  if (!files.includes("opencode_service.py")) files.push("opencode_service.py")
  return files.sort().map((name) => ({
    name,
    data: name === "opencode_service.py" ? serviceBytes() : lf(readFileSync(join(addonRoot, name))),
  }))
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
