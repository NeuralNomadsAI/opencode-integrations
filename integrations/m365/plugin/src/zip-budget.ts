import type { Readable } from "node:stream"
import type JSZip from "jszip"

// Reject oversized Office archives from central-directory metadata before JSZip inflates XML.
// ZIP64 and encrypted/multi-disk archives are outside the small-file helpers.
export function checkZipBudget(bytes: Uint8Array, maxBytes = 100 * 1024 * 1024) {
  const data = Buffer.from(bytes)
  let end = data.length - 22
  const first = Math.max(0, data.length - 65557)
  while (end >= first && data.readUInt32LE(end) !== 0x06054b50) end--
  if (end < first) throw new Error("Unsupported Office archive.")
  const entries = data.readUInt16LE(end + 10)
  if (
    data.readUInt16LE(end + 4) ||
    data.readUInt16LE(end + 6) ||
    entries === 0xffff ||
    entries !== data.readUInt16LE(end + 8) ||
    entries > 10000
  )
    throw new Error("Unsupported Office archive.")
  const centralSize = data.readUInt32LE(end + 12)
  let cursor = data.readUInt32LE(end + 16)
  const centralEnd = cursor + centralSize
  if (centralEnd !== end) throw new Error("Unsupported Office archive.")
  let total = 0
  for (let i = 0; i < entries; i++) {
    if (cursor + 46 > centralEnd || data.readUInt32LE(cursor) !== 0x02014b50 || data.readUInt16LE(cursor + 8) & 1)
      throw new Error("Unsupported Office archive.")
    total += data.readUInt32LE(cursor + 24)
    if (total > maxBytes) throw new Error("Expanded Office archive exceeds the size limit.")
    cursor += 46 + data.readUInt16LE(cursor + 28) + data.readUInt16LE(cursor + 30) + data.readUInt16LE(cursor + 32)
  }
  if (cursor !== centralEnd) throw new Error("Unsupported Office archive.")
}

// Verify the actual expanded byte count too: untrusted central-directory sizes can lie.
export async function checkExpandedBudget(zip: JSZip, maxBytes = 100 * 1024 * 1024) {
  let remaining = maxBytes
  for (const file of Object.values(zip.files)) {
    if (file.dir) continue
    await new Promise<void>((done, fail) => {
      const stream = file.nodeStream() as Readable
      stream.on("data", (chunk: Buffer) => {
        remaining -= chunk.length
        if (remaining < 0) {
          stream.destroy()
          fail(new Error("Expanded Office archive exceeds the size limit."))
        }
      })
      stream.on("error", fail)
      stream.on("end", done)
    })
  }
}
