// Blocking scan for traces of the previous studio deployment: its name, its
// NAS host, studio drive and UNC paths, machine user paths and known Entra
// GUIDs. The GUIDs are only known through SHA-256 fingerprints supplied
// outside the repository (TRACE_SCAN_FINGERPRINTS), so no identifier and no
// forbidden word is ever written here: the words are assembled at runtime.
import { createHash } from "node:crypto"
import { gunzipSync, inflateRawSync } from "node:zlib"

export type Finding = { location: string; line: number; rule: string }

type Rule = { name: string; pattern: RegExp }

const word = (...parts: string[]) => parts.join("")

export const RULES: readonly Rule[] = [
  { name: "former studio name", pattern: new RegExp(word("pag", "ecran"), "gi") },
  { name: "former NAS host", pattern: new RegExp(word("true", "nas"), "gi") },
  { name: "studio drive path", pattern: /(?<![A-Za-z0-9_])[Rr]:(?:\\|\/)/g },
  { name: "UNC network path", pattern: /(?<![\\A-Za-z0-9:%_)\]}>.$-])\\{2,}[A-Za-z0-9][A-Za-z0-9._-]{1,62}\\/g },
  { name: "machine user path", pattern: /(?<![A-Za-z0-9_])[A-Za-z]:(?:\\|\/)+Users(?:\\|\/)/gi },
]

const GUID = /\b[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\b/gi

// Fingerprint of a GUID: SHA-256 of its lowercase dashed form, in hex.
export function guidFingerprint(guid: string) {
  const hex = guid.toLowerCase().replace(/-/g, "")
  const dashed = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  return createHash("sha256").update(dashed).digest("hex")
}

export function parseFingerprints(value: string | undefined): Set<string> {
  return new Set(
    (value ?? "")
      .split(/[\s,;]+/)
      .map((item) => item.trim().toLowerCase())
      .filter((item) => /^[0-9a-f]{64}$/.test(item)),
  )
}

function lineAt(text: string, index: number) {
  let line = 1
  for (let i = text.indexOf("\n"); i !== -1 && i < index; i = text.indexOf("\n", i + 1)) line++
  return line
}

export class TraceScanner {
  readonly findings: Finding[] = []
  readonly errors: string[] = []
  scannedEntries = 0
  private readonly fingerprints: Set<string>

  constructor(fingerprints: Set<string>) {
    this.fingerprints = fingerprints
  }

  get partial() {
    return this.fingerprints.size === 0
  }

  scanText(location: string, text: string) {
    for (const rule of RULES) {
      rule.pattern.lastIndex = 0
      for (const match of text.matchAll(rule.pattern)) {
        this.findings.push({ location, line: lineAt(text, match.index ?? 0), rule: rule.name })
      }
    }
    if (this.fingerprints.size === 0) return
    for (const match of text.matchAll(GUID)) {
      if (this.fingerprints.has(guidFingerprint(match[0]))) {
        this.findings.push({ location, line: lineAt(text, match.index ?? 0), rule: "known Entra GUID" })
      }
    }
  }

  // Scans a file name and its bytes; archives are opened and scanned entry by entry.
  scanBytes(location: string, bytes: Uint8Array, depth = 0) {
    this.scannedEntries++
    this.scanText(`${location} (name)`, location)
    const lower = location.toLowerCase()
    try {
      if (lower.endsWith(".zip")) return this.scanZip(location, bytes, depth)
      if (lower.endsWith(".tgz") || lower.endsWith(".tar.gz")) return this.scanTar(location, gunzipSync(bytes), depth)
      if (lower.endsWith(".tar")) return this.scanTar(location, bytes, depth)
    } catch (error) {
      this.errors.push(`${location}: cannot open archive (${(error as Error).message})`)
      return
    }
    this.scanText(location, Buffer.from(bytes).toString("latin1"))
  }

  private nested(location: string, name: string, bytes: Uint8Array, depth: number) {
    if (depth >= 4) {
      this.errors.push(`${location}!${name}: archives nested too deeply`)
      return
    }
    this.scanBytes(`${location}!${name}`, bytes, depth + 1)
  }

  private scanTar(location: string, tar: Uint8Array, depth: number) {
    const buffer = Buffer.from(tar)
    let offset = 0
    while (offset + 512 <= buffer.length) {
      const header = buffer.subarray(offset, offset + 512)
      if (header.every((byte) => byte === 0)) break
      const field = (start: number, length: number) =>
        header
          .subarray(start, start + length)
          .toString("latin1")
          .replace(/\0.*$/s, "")
      const size = Number.parseInt(field(124, 12).trim() || "0", 8)
      const type = field(156, 1)
      const prefix = field(345, 155)
      const name = prefix ? `${prefix}/${field(0, 100)}` : field(0, 100)
      const body = buffer.subarray(offset + 512, offset + 512 + size)
      if (type === "0" || type === "" || type === "\0") this.nested(location, name, body, depth)
      else this.scanText(`${location}!${name} (header)`, body.toString("latin1"))
      offset += 512 + Math.ceil(size / 512) * 512
    }
  }

  private scanZip(location: string, zip: Uint8Array, depth: number) {
    const buffer = Buffer.from(zip)
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
    for (let entry = 0; entry < count; entry++) {
      if (buffer.readUInt32LE(offset) !== 0x02014b50) throw new Error("corrupt central directory")
      const method = buffer.readUInt16LE(offset + 10)
      const compressed = buffer.readUInt32LE(offset + 20)
      const nameLength = buffer.readUInt16LE(offset + 28)
      const extraLength = buffer.readUInt16LE(offset + 30)
      const commentLength = buffer.readUInt16LE(offset + 32)
      const local = buffer.readUInt32LE(offset + 42)
      const name = buffer.subarray(offset + 46, offset + 46 + nameLength).toString("utf8")
      this.scanText(
        `${location}!${name} (comment)`,
        buffer
          .subarray(offset + 46 + nameLength + extraLength, offset + 46 + nameLength + extraLength + commentLength)
          .toString("latin1"),
      )
      offset += 46 + nameLength + extraLength + commentLength
      if (name.endsWith("/")) {
        this.scanText(`${location}!${name} (name)`, name)
        continue
      }
      if (buffer.readUInt32LE(local) !== 0x04034b50) throw new Error(`corrupt local header for ${name}`)
      const start = local + 30 + buffer.readUInt16LE(local + 26) + buffer.readUInt16LE(local + 28)
      const data = buffer.subarray(start, start + compressed)
      if (method === 0) this.nested(location, name, data, depth)
      else if (method === 8) this.nested(location, name, inflateRawSync(data), depth)
      else throw new Error(`unsupported compression method ${method} for ${name}`)
    }
  }
}
