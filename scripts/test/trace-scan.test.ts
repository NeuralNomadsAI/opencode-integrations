import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { randomUUID } from "node:crypto"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { after, describe, it } from "node:test"
import { deflateRawSync, gzipSync } from "node:zlib"

import { guidFingerprint, TraceScanner } from "../trace-scan-lib.ts"

// Planted true positives are assembled at runtime so that this file stays clean.
const PLANTED = ["Studio ", "Pag", "ecran"].join("")
const CLI = join(import.meta.dirname, "..", "trace-scan.ts")
const roots: string[] = []
after(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true })
})

function zip(entries: Record<string, Buffer>, deflate = true) {
  const locals: Buffer[] = []
  const centrals: Buffer[] = []
  let offset = 0
  for (const [name, content] of Object.entries(entries)) {
    const data = deflate ? deflateRawSync(content) : content
    const nameBytes = Buffer.from(name)
    const local = Buffer.alloc(30)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(deflate ? 8 : 0, 8)
    local.writeUInt32LE(data.length, 18)
    local.writeUInt32LE(content.length, 22)
    local.writeUInt16LE(nameBytes.length, 26)
    const central = Buffer.alloc(46)
    central.writeUInt32LE(0x02014b50, 0)
    central.writeUInt16LE(deflate ? 8 : 0, 10)
    central.writeUInt32LE(data.length, 20)
    central.writeUInt32LE(content.length, 24)
    central.writeUInt16LE(nameBytes.length, 28)
    central.writeUInt32LE(offset, 42)
    locals.push(local, nameBytes, data)
    centrals.push(central, nameBytes)
    offset += 30 + nameBytes.length + data.length
  }
  const directory = Buffer.concat(centrals)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(Object.keys(entries).length, 8)
  end.writeUInt16LE(Object.keys(entries).length, 10)
  end.writeUInt32LE(directory.length, 12)
  end.writeUInt32LE(offset, 16)
  return Buffer.concat([...locals, directory, end])
}

function tgz(entries: Record<string, Buffer>) {
  const blocks: Buffer[] = []
  for (const [name, content] of Object.entries(entries)) {
    const header = Buffer.alloc(512)
    header.write(name, 0, "latin1")
    header.write(`${content.length.toString(8).padStart(11, "0")}\0`, 124, "latin1")
    header.write("0", 156, "latin1")
    blocks.push(header, content, Buffer.alloc((512 - (content.length % 512)) % 512))
  }
  return gzipSync(Buffer.concat([...blocks, Buffer.alloc(1024)]))
}

function scan(location: string, bytes: Buffer, fingerprints = new Set<string>()) {
  const scanner = new TraceScanner(fingerprints)
  scanner.scanBytes(location, bytes)
  return scanner
}

function repo(files: Record<string, string | Buffer>) {
  const root = mkdtempSync(join(tmpdir(), "trace-scan-"))
  roots.push(root)
  for (const [file, content] of Object.entries(files)) {
    mkdirSync(dirname(join(root, file)), { recursive: true })
    writeFileSync(join(root, file), content)
  }
  const git = (...args: string[]) => spawnSync("git", args, { cwd: root, encoding: "utf8" })
  git("init", "-q")
  git("add", ".")
  git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
  return root
}

function cli(cwd: string, args: string[], env: Record<string, string> = {}) {
  const result = spawnSync(process.execPath, [CLI, ...args], {
    cwd,
    encoding: "utf8",
    env: { ...process.env, TRACE_SCAN_FINGERPRINTS: "", ...env },
  })
  return { status: result.status, output: `${result.stdout}${result.stderr}` }
}

describe("trace scanner rules", () => {
  it("flags the former studio name in any case, with its line", () => {
    const scanner = scan("notes.md", Buffer.from(`ok\n${PLANTED.toUpperCase()}\n`))
    assert.deepEqual(scanner.findings, [{ location: "notes.md", line: 2, rule: "former studio name" }])
  })

  it("flags the NAS host, drive, UNC and machine user paths", () => {
    const text = [
      ["\\\\", "true", "nas01", "\\share"].join(""),
      ["R", ":\\projects"].join(""),
      ["C", ":/Users/someone/file"].join(""),
      ["\\\\", "fileserver\\share"].join(""),
    ].join("\n")
    const rules = scan("paths.txt", Buffer.from(text)).findings.map((finding) => finding.rule)
    for (const rule of ["former NAS host", "studio drive path", "machine user path", "UNC network path"]) {
      assert.ok(rules.includes(rule), rule)
    }
  })

  it("ignores ordinary escapes, URLs and state directories", () => {
    const text = [
      'const pattern = "\\\\n" + "\\\\t"',
      "https://example.com/path",
      "%LOCALAPPDATA%\\NeuralNomads\\opencode-blender\\",
      'JSON: "%LOCALAPPDATA%\\\\NeuralNomads\\\\opencode-blender"',
      "PR:/123 and FOR:/x",
      "/(?<![A-Za-z0-9_])[Rr]:(?:\\\\|\\/)/g",
    ].join("\n")
    assert.deepEqual(scan("clean.ts", Buffer.from(text)).findings, [])
  })

  it("flags a fingerprinted GUID only when fingerprints are supplied", () => {
    const guid = randomUUID()
    const bytes = Buffer.from(`client = "${guid.toUpperCase()}"\nother = "${randomUUID()}"`)
    assert.deepEqual(scan("auth.ts", bytes).findings, [])
    const full = scan("auth.ts", bytes, new Set([guidFingerprint(guid)]))
    assert.deepEqual(full.findings, [{ location: "auth.ts", line: 1, rule: "known Entra GUID" }])
    assert.equal(guidFingerprint(guid.replace(/-/g, "")), guidFingerprint(guid))
  })

  it("finds a planted trace inside zip, nested tgz and file names", () => {
    const inner = tgz({ "package/readme.md": Buffer.from(PLANTED) })
    for (const archive of [
      zip({ "addon/__init__.py": Buffer.from(`# ${PLANTED}`) }),
      zip({ "addon/__init__.py": Buffer.from(`# ${PLANTED}`) }, false),
      zip({ "inner.tgz": inner }),
      tgz({ [`package/${PLANTED.replace(" ", "-")}.txt`]: Buffer.from("x") }),
    ]) {
      const name = archive[0] === 0x1f ? "pack.tgz" : "host.zip"
      assert.equal(scan(name, archive).findings.length > 0, true, name)
    }
    assert.deepEqual(scan("clean.zip", zip({ "a.txt": Buffer.from("clean") })).findings, [])
  })

  it("fails closed on an archive it cannot read", () => {
    const scanner = scan("broken.zip", Buffer.from("not a zip"))
    assert.equal(scanner.errors.length, 1)
  })
})

describe("trace-scan command", () => {
  it("passes a clean tree and history, as a partial scan without fingerprints", () => {
    const root = repo({ "README.md": "clean\n" })
    const result = cli(root, ["--tree", "--history"])
    assert.equal(result.status, 0, result.output)
    assert.match(result.output, /partial scan/)
    assert.equal(cli(root, ["--tree", "--require-fingerprints"]).status, 1)
    assert.equal(cli(root, ["--tree", "--require-fingerprints"], { TRACE_SCAN_FINGERPRINTS: "0".repeat(64) }).status, 0)
  })

  it("fails a planted trace in the tree, in a committed archive and in history", () => {
    const tree = repo({ "test/planted.txt": PLANTED })
    const treeResult = cli(tree, ["--tree"])
    assert.equal(treeResult.status, 1)
    assert.match(treeResult.output, /test\/planted.txt:1: former studio name/)

    const archive = repo({ "host/part.zip": zip({ "addon.py": Buffer.from(PLANTED) }) })
    assert.match(cli(archive, ["--tree"]).output, /host\/part.zip!addon.py:1: former studio name/)

    const history = repo({ "a.txt": PLANTED })
    spawnSync("git", ["rm", "-q", "a.txt"], { cwd: history })
    spawnSync("git", ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "remove"], {
      cwd: history,
    })
    assert.equal(cli(history, ["--tree"]).status, 0)
    assert.match(cli(history, ["--history"]).output, /git history:\d+: former studio name/)
  })

  it("fails a planted trace inside the npm pack tarball of a workspace", () => {
    const root = repo({
      "package.json": JSON.stringify({ name: "fixture-root", private: true, workspaces: ["packages/*"] }),
      "packages/demo/package.json": JSON.stringify({ name: "demo", version: "0.0.0", files: ["dist"] }),
      ".gitignore": "dist/\n",
    })
    // Ignored by Git, so only the tarball carries it.
    mkdirSync(join(root, "packages", "demo", "dist"), { recursive: true })
    writeFileSync(join(root, "packages", "demo", "dist", "index.js"), `// ${PLANTED}\n`)
    assert.equal(cli(root, ["--tree"]).status, 0)
    const packs = cli(root, ["--packs"])
    assert.equal(packs.status, 1, packs.output)
    assert.match(packs.output, /npm pack demo-0.0.0.tgz!package\/dist\/index.js:1: former studio name/)
  })
})
