import { inflateRawSync } from "node:zlib"

// Licence of a package, whose plugin is MIT, and of one that also embeds a GPL application part (PKG-10).
export const PLUGIN_LICENSE = "MIT"
export const GPL_HOST_LICENSE = "GPL-3.0-or-later"
export const GPL_HOST_PACKAGE_LICENSE = `${PLUGIN_LICENSE} AND ${GPL_HOST_LICENSE}`
// Bound on the decompressed size of a payload archive read for its licence.
export const ZIP_MAX_BYTES = 256 * 1024 * 1024

// Entries of a ZIP archive (stored or deflated, no ZIP64), by name; throws on a corrupt archive.
export function readZipEntries(bytes: Uint8Array): Map<string, Buffer> {
  const buffer = Buffer.from(bytes)
  let end = -1
  for (let i = buffer.length - 22; i >= Math.max(0, buffer.length - 65_557); i--) {
    if (buffer.readUInt32LE(i) === 0x06054b50) {
      end = i
      break
    }
  }
  if (end < 0) throw new Error("no end of central directory")
  const count = buffer.readUInt16LE(end + 10)
  let offset = buffer.readUInt32LE(end + 16)
  if (count === 0xffff || offset === 0xffffffff) throw new Error("ZIP64 archives are not supported")
  const entries = new Map<string, Buffer>()
  let total = 0
  for (let entry = 0; entry < count; entry++) {
    if (offset + 46 > buffer.length || buffer.readUInt32LE(offset) !== 0x02014b50) {
      throw new Error("corrupt central directory")
    }
    const method = buffer.readUInt16LE(offset + 10)
    const compressed = buffer.readUInt32LE(offset + 20)
    const size = buffer.readUInt32LE(offset + 24)
    const nameLength = buffer.readUInt16LE(offset + 28)
    const local = buffer.readUInt32LE(offset + 42)
    const name = buffer.subarray(offset + 46, offset + 46 + nameLength).toString("utf8")
    offset += 46 + nameLength + buffer.readUInt16LE(offset + 30) + buffer.readUInt16LE(offset + 32)
    if (name.endsWith("/")) continue
    total += size
    if (total > ZIP_MAX_BYTES) throw new Error(`more than ${ZIP_MAX_BYTES} bytes once decompressed`)
    if (local + 30 > buffer.length || buffer.readUInt32LE(local) !== 0x04034b50) {
      throw new Error(`corrupt local header for ${name}`)
    }
    const start = local + 30 + buffer.readUInt16LE(local + 26) + buffer.readUInt16LE(local + 28)
    const data = buffer.subarray(start, start + compressed)
    if (method === 0) entries.set(name, Buffer.from(data))
    else if (method === 8) entries.set(name, inflateRawSync(data, { maxOutputLength: Math.max(size, 1) }))
    else throw new Error(`unsupported compression method ${method} for ${name}`)
  }
  return entries
}

export type HostPartLicense = { license: typeof GPL_HOST_LICENSE } | { license: null; reason: string }

// The application part's own licence, read from its payload archive: GPL-3.0-or-later when the
// archive carries the GNU GPL version 3 text as its root LICENSE and every SPDX declaration in it
// (`SPDX-License-Identifier: <expression>` headers, `"SPDX:<expression>"` manifest entries), at
// least one, is exactly GPL-3.0-or-later. Otherwise null, with the reason.
export function hostPartLicense(payload: Uint8Array): HostPartLicense {
  let entries: Map<string, Buffer>
  try {
    entries = readZipEntries(payload)
  } catch (error) {
    return { license: null, reason: `the payload is not a readable ZIP archive (${(error as Error).message})` }
  }
  const text = entries.get("LICENSE")?.toString("utf8") ?? ""
  if (!/GNU GENERAL PUBLIC LICENSE\s+Version 3, 29 June 2007/.test(text)) {
    return { license: null, reason: "the payload has no GNU GPL version 3 text as its root LICENSE" }
  }
  const declared = new Set<string>()
  for (const [name, content] of entries) {
    if (name === "LICENSE") continue
    const source = content.toString("utf8")
    // An expression is identifiers, operators and parentheses; a comment end or a quote ends it.
    for (const match of source.matchAll(/SPDX-License-Identifier:[ \t]*([A-Za-z0-9.+:() \t-]+)/g)) {
      declared.add((match[1] ?? "").trim())
    }
    for (const match of source.matchAll(/["']SPDX:([^"'\r\n]+)["']/g)) declared.add((match[1] ?? "").trim())
  }
  if (declared.size === 1 && declared.has(GPL_HOST_LICENSE)) return { license: GPL_HOST_LICENSE }
  return {
    license: null,
    reason:
      declared.size === 0
        ? "the payload declares no SPDX licence"
        : `the payload's SPDX declarations are ${[...declared].map((id) => `"${id}"`).join(", ")}`,
  }
}
