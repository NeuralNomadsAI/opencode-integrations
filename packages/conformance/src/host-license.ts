import { inflateRawSync } from "node:zlib"

// Licence of a package, whose plugin is MIT, and of one that also embeds a GPL application part (PKG-10).
export const PLUGIN_LICENSE = "MIT"
export const GPL_HOST_LICENSE = "GPL-3.0-or-later"
export const GPL_HOST_PACKAGE_LICENSE = `${PLUGIN_LICENSE} AND ${GPL_HOST_LICENSE}`

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
  for (let entry = 0; entry < count; entry++) {
    if (buffer.readUInt32LE(offset) !== 0x02014b50) throw new Error("corrupt central directory")
    const method = buffer.readUInt16LE(offset + 10)
    const compressed = buffer.readUInt32LE(offset + 20)
    const nameLength = buffer.readUInt16LE(offset + 28)
    const local = buffer.readUInt32LE(offset + 42)
    const name = buffer.subarray(offset + 46, offset + 46 + nameLength).toString("utf8")
    offset += 46 + nameLength + buffer.readUInt16LE(offset + 30) + buffer.readUInt16LE(offset + 32)
    if (name.endsWith("/")) continue
    if (buffer.readUInt32LE(local) !== 0x04034b50) throw new Error(`corrupt local header for ${name}`)
    const start = local + 30 + buffer.readUInt16LE(local + 26) + buffer.readUInt16LE(local + 28)
    const data = buffer.subarray(start, start + compressed)
    if (method === 0) entries.set(name, Buffer.from(data))
    else if (method === 8) entries.set(name, inflateRawSync(data))
    else throw new Error(`unsupported compression method ${method} for ${name}`)
  }
  return entries
}

// The application part's own licence, read from its payload archive: GPL-3.0-or-later when the
// archive carries the GNU GPL version 3 text as its root LICENSE and every SPDX declaration in it
// (`SPDX-License-Identifier: <id>` headers, `"SPDX:<id>"` manifest entries), at least one, names
// GPL-3.0-or-later. Anything else, an unreadable archive included, is null.
export function hostPartLicense(payload: Uint8Array): typeof GPL_HOST_LICENSE | null {
  let entries: Map<string, Buffer>
  try {
    entries = readZipEntries(payload)
  } catch {
    return null
  }
  const licence = entries.get("LICENSE")?.toString("utf8") ?? ""
  if (!/GNU GENERAL PUBLIC LICENSE\s+Version 3, 29 June 2007/.test(licence)) return null
  const declared = new Set<string>()
  for (const [name, content] of entries) {
    if (name === "LICENSE") continue
    const text = content.toString("utf8")
    for (const match of text.matchAll(/SPDX-License-Identifier:\s*([^\s*]+)/g)) declared.add(match[1] ?? "")
    for (const match of text.matchAll(/["']SPDX:([^"']+)["']/g)) declared.add(match[1] ?? "")
  }
  return declared.size === 1 && declared.has(GPL_HOST_LICENSE) ? GPL_HOST_LICENSE : null
}
