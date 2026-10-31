// The package's static content: generated host artifacts up to date, the payload named by the
// marker, capabilities derived from the manifests, the method inventory (AC-B5) and the skills.

import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { describe, it } from "node:test"
import { addonRoot, kitServicePath, payloadBytes, pluginRoot, sha256 } from "../plugin/scripts/host-payload.ts"
import { pathInfo, resolveBlendFile } from "../plugin/src/audit.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { requestHost } from "../plugin/src/kit.ts"
import { readStoredZip } from "../plugin/src/zip.ts"
import { startStubHost } from "./helpers/plugin.ts"

type Manifest = { name: string; kind: string; legacy?: string; undoable?: boolean; network?: boolean }

function manifests(dir = join(pluginRoot, "methods")): Manifest[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory()
      ? manifests(join(dir, entry.name))
      : [JSON.parse(readFileSync(join(dir, entry.name), "utf8")) as Manifest],
  )
}

describe("generated host artifacts", () => {
  it("catalog.json, the vendored discovery client and the payload are up to date", () => {
    execFileSync(process.execPath, [join(pluginRoot, "scripts", "build-host.ts"), "--check"], { stdio: "pipe" })
    const vendored = readFileSync(join(addonRoot, "opencode_service.py"), "utf8").replace(/\r\n/g, "\n")
    assert.equal(vendored, readFileSync(kitServicePath, "utf8").replace(/\r\n/g, "\n"))
  })

  it("the marker names the payload, its digest and Blender 5.2", () => {
    const pkg = readPackage()
    const host = pkg.opencodeIntegration.host as NonNullable<typeof pkg.opencodeIntegration.host>
    const archive = readFileSync(join(pluginRoot, ...host.payload.split("/")))
    assert.equal(sha256(archive), host.sha256)
    assert.ok(archive.equals(payloadBytes()), "the archive is reproducible")
    const entries = readStoredZip(archive)
    const manifest = entries.find((entry) => entry.name === "blender_manifest.toml")?.data.toString("utf8") ?? ""
    assert.match(manifest, new RegExp(`^version = "${pkg.version}"$`, "m"))
    assert.match(manifest, /^blender_version_min = "5\.2\.0"$/m)
    assert.equal(host.appVersions, ">=5.2")
    assert.ok(!entries.some((entry) => /__pycache__|\.pyc$/.test(entry.name)))
  })

  it("hello's operations are the operation manifests", async () => {
    const host = await startStubHost(false)
    try {
      const hello = (await requestHost(host.endpoint, "hello", {})) as {
        capabilities: { methods: string[]; operations: Array<{ name: string; undoable: boolean; network?: boolean }> }
      }
      const expected = manifests()
        .filter((manifest) => manifest.kind === "host-operation")
        .map((manifest) => ({
          name: manifest.name,
          undoable: manifest.undoable === true,
          ...(manifest.network ? { network: true } : {}),
        }))
        .sort((a, b) => (a.name < b.name ? -1 : 1))
      assert.deepEqual(hello.capabilities.operations, expected)
      assert.deepEqual(hello.capabilities.methods, ["hello", "context", "apply"])
    } finally {
      await host.close()
    }
  })
})

describe("method inventory (AC-B5)", () => {
  it("classifies the 89 earlier methods and names a test for each", () => {
    const rows = readFileSync(join(pluginRoot, "METHODS.md"), "utf8")
      .split("\n")
      .filter((line) => /^\| [a-z_]+ +\| `/.test(line))
    assert.equal(rows.length, 89)
    const legacy = new Set(rows.map((row) => /\| `([a-z_]+)` +\|/.exec(row)?.[1]))
    assert.equal(legacy.size, 89)
    for (const row of rows) assert.match(row, /\| (read|operation|side effect|host-less|retired) +\|.*\| \S.* \|$/)
    for (const manifest of manifests().filter((entry) => entry.legacy)) {
      assert.ok(legacy.has(manifest.legacy), `${manifest.name} is in the inventory`)
    }
    const hostManifests = manifests().filter((entry) => entry.kind.startsWith("host-"))
    assert.equal(hostManifests.filter((entry) => entry.kind === "host-read").length, 30)
    assert.equal(hostManifests.filter((entry) => entry.kind === "host-operation").length, 49)
  })
})

describe("skills", () => {
  it("cite only tools, reads and operations that exist", () => {
    const names = new Set(manifests().map((manifest) => manifest.name))
    const root = join(pluginRoot, "skills")
    for (const skill of readdirSync(root)) {
      const text = readFileSync(join(root, skill, "SKILL.md"), "utf8")
      for (const match of text.matchAll(/\bblender_[a-z_]+\b/g)) assert.ok(names.has(match[0]), `${skill}: ${match[0]}`)
      for (const match of text.matchAll(
        /`((?:scene|shader|geometry_nodes|animation|assets|shot_manager|addons|workflows)\.[a-z_]+)`/g,
      )) {
        assert.ok(names.has(match[1] as string), `${skill}: ${match[1]}`)
      }
    }
  })
})

describe("audit path information", () => {
  it("reads a .blend file's metadata without launching Blender", () => {
    const folder = mkdtempSync(join(tmpdir(), "blender-audit-"))
    try {
      const file = join(folder, "scene.blend")
      writeFileSync(file, "BLENDER")
      assert.equal(pathInfo(resolveBlendFile(file)).size_bytes, 7)
      assert.throws(() => resolveBlendFile(join(folder, "scene.txt")), /\.blend/)
    } finally {
      rmSync(folder, { recursive: true, force: true })
    }
  })
})
