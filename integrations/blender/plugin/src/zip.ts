// Minimal ZIP archives with stored (uncompressed) entries: a deterministic writer for the host
// payload and a strict reader for its installation. No dependency.

import { crc32 } from "node:zlib"

export type ZipEntry = { name: string; data: Buffer }

// 1980-01-01 00:00:00, the earliest DOS date: archives do not depend on the build time.
const DOS_TIME = 0
const DOS_DATE = (0 << 9) | (1 << 5) | 1
const UTF8_FLAG = 0x0800

export function writeStoredZip(entries: ZipEntry[]): Buffer {
  const locals: Buffer[] = []
  const centrals: Buffer[] = []
  let offset = 0
  for (const entry of entries) {
    const name = Buffer.from(entry.name, "utf8")
    const crc = crc32(entry.data)
    const local = Buffer.alloc(30)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(20, 4)
    local.writeUInt16LE(UTF8_FLAG, 6)
    local.writeUInt16LE(0, 8)
    local.writeUInt16LE(DOS_TIME, 10)
    local.writeUInt16LE(DOS_DATE, 12)
    local.writeUInt32LE(crc, 14)
    local.writeUInt32LE(entry.data.length, 18)
    local.writeUInt32LE(entry.data.length, 22)
    local.writeUInt16LE(name.length, 26)
    local.writeUInt16LE(0, 28)
    locals.push(local, name, entry.data)

    const central = Buffer.alloc(46)
    central.writeUInt32LE(0x02014b50, 0)
    central.writeUInt16LE(20, 4)
    central.writeUInt16LE(20, 6)
    central.writeUInt16LE(UTF8_FLAG, 8)
    central.writeUInt16LE(0, 10)
    central.writeUInt16LE(DOS_TIME, 12)
    central.writeUInt16LE(DOS_DATE, 14)
    central.writeUInt32LE(crc, 16)
    central.writeUInt32LE(entry.data.length, 20)
    central.writeUInt32LE(entry.data.length, 24)
    central.writeUInt16LE(name.length, 28)
    central.writeUInt32LE(offset, 42)
    centrals.push(central, name)
    offset += 30 + name.length + entry.data.length
  }
  const directory = Buffer.concat(centrals)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(entries.length, 8)
  end.writeUInt16LE(entries.length, 10)
  end.writeUInt32LE(directory.length, 12)
  end.writeUInt32LE(offset, 16)
  return Buffer.concat([...locals, directory, end])
}

const SAFE_NAME = /^(?!.*(?:^|\/)\.\.?(?:\/|$))[A-Za-z0-9_.\-/]+$/

// Reads an archive written by writeStoredZip; refuses compression, unsafe names and bad checksums.
export function readStoredZip(archive: Buffer): ZipEntry[] {
  const endOffset = archive.length - 22
  if (endOffset < 0 || archive.readUInt32LE(endOffset) !== 0x06054b50) throw new Error("not a zip archive")
  const count = archive.readUInt16LE(endOffset + 10)
  let cursor = archive.readUInt32LE(endOffset + 16)
  const entries: ZipEntry[] = []
  for (let index = 0; index < count; index += 1) {
    if (archive.readUInt32LE(cursor) !== 0x02014b50) throw new Error("corrupt zip directory")
    const method = archive.readUInt16LE(cursor + 10)
    const crc = archive.readUInt32LE(cursor + 16)
    const size = archive.readUInt32LE(cursor + 24)
    const nameLength = archive.readUInt16LE(cursor + 28)
    const extraLength = archive.readUInt16LE(cursor + 30)
    const commentLength = archive.readUInt16LE(cursor + 32)
    const localOffset = archive.readUInt32LE(cursor + 42)
    const name = archive.subarray(cursor + 46, cursor + 46 + nameLength).toString("utf8")
    cursor += 46 + nameLength + extraLength + commentLength
    if (method !== 0) throw new Error("compressed zip entries are not supported")
    if (!SAFE_NAME.test(name) || name.startsWith("/")) throw new Error("unsafe zip entry name")
    if (archive.readUInt32LE(localOffset) !== 0x04034b50) throw new Error("corrupt zip entry")
    const localName = archive.readUInt16LE(localOffset + 26)
    const localExtra = archive.readUInt16LE(localOffset + 28)
    const start = localOffset + 30 + localName + localExtra
    const data = Buffer.from(archive.subarray(start, start + size))
    if (data.length !== size || crc32(data) !== crc) throw new Error("zip entry checksum mismatch")
    entries.push({ name, data })
  }
  return entries
}
