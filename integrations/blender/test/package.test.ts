// The package's static content: generated host artifacts up to date, the payload named by the marker,
// the host methods the plugin uses, licences of the parts, no bundled Blender documentation, and the
// inventory of what 0.2.0 had.

import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { existsSync, readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"
import { describe, it } from "node:test"
import { addonRoot, kitServicePath, payloadBytes, pluginRoot, sha256 } from "../plugin/scripts/host-payload.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { readStoredZip } from "../plugin/src/zip.ts"

type Manifest = { name: string; kind: string; execution: { method?: string }; requires?: { hostMethods?: string[] } }

const toolManifests = () =>
  readdirSync(join(pluginRoot, "methods", "tools")).map(
    (file) => JSON.parse(readFileSync(join(pluginRoot, "methods", "tools", file), "utf8")) as Manifest,
  )

describe("generated host artifacts", () => {
  it("the vendored discovery client and the payload are up to date", () => {
    execFileSync(process.execPath, [join(pluginRoot, "scripts", "build-host.ts"), "--check"], { stdio: "pipe" })
    const vendored = readFileSync(join(addonRoot, "opencode_service.py"), "utf8").replace(/\r\n/g, "\n")
    assert.equal(vendored, readFileSync(kitServicePath, "utf8").replace(/\r\n/g, "\n"))
  })

  it("the marker names the payload, its digest and Blender 5.3", () => {
    const pkg = readPackage()
    const host = pkg.opencodeIntegration.host as NonNullable<typeof pkg.opencodeIntegration.host>
    const archive = readFileSync(join(pluginRoot, ...host.payload.split("/")))
    assert.equal(sha256(archive), host.sha256)
    assert.ok(archive.equals(payloadBytes()), "the archive is reproducible")
    const entries = readStoredZip(archive)
    const manifest = entries.find((entry) => entry.name === "blender_manifest.toml")?.data.toString("utf8") ?? ""
    assert.match(manifest, new RegExp(`^version = "${pkg.version}"$`, "m"))
    assert.match(manifest, /^blender_version_min = "5\.3\.0"$/m)
    assert.equal(host.appVersions, ">=5.3")
    assert.equal(host.range, `^${pkg.version}`)
    assert.ok(!entries.some((entry) => /__pycache__|\.pyc$/.test(entry.name)))
  })

  it("the tools need four host methods of the add-on (its hello lists them: blender.test.ts)", () => {
    const used = new Set(toolManifests().flatMap((manifest) => manifest.requires?.hostMethods ?? []))
    assert.deepEqual([...used].sort(), ["apply", "execute_code", "lab_tool", "scene"])
  })
})

describe("licences and content (PKG-10)", () => {
  it("the GPL parts say so; the plugin is MIT; no Blender documentation is bundled", () => {
    const pkg = JSON.parse(readFileSync(join(pluginRoot, "package.json"), "utf8")) as {
      license: string
      files: string[]
    }
    assert.equal(pkg.license, "MIT AND GPL-3.0-or-later")
    assert.match(readFileSync(join(pluginRoot, "LICENSE"), "utf8"), /^MIT License/)
    assert.match(readFileSync(join(addonRoot, "LICENSE"), "utf8"), /GNU GENERAL PUBLIC LICENSE\s+Version 3/)
    for (const file of readdirSync(join(pluginRoot, "programs")))
      assert.match(
        readFileSync(join(pluginRoot, "programs", file), "utf8"),
        /^# SPDX-License-Identifier: GPL-3\.0-or-later\n/,
        file,
      )
    const lab = join(addonRoot, "lab")
    for (const file of readdirSync(lab).filter((name) => name.endsWith("_toolcode.py"))) {
      const text = readFileSync(join(lab, file), "utf8")
      assert.match(text, /^# Copied from Blender Lab blender_mcp v1\.0\.3 \(commit [0-9a-f]{40}\),/, file)
      assert.match(text, /# SPDX-License-Identifier: GPL-3\.0-or-later/, file)
    }
    const notice = readFileSync(join(pluginRoot, "NOTICE.md"), "utf8")
    for (const part of ["`programs/`", "Blender Lab", "GPL-3.0-or-later", "MIT"]) assert.ok(notice.includes(part), part)
    assert.deepEqual(pkg.files.sort(), ["LICENSE", "NOTICE.md", "README.md", "dist", "host", "methods", "programs"])
    assert.ok(!existsSync(join(pluginRoot, "data")) && !existsSync(join(pluginRoot, "skills")))
  })
})

describe("method inventory", () => {
  it("names every 0.2.0 tool and catalog entry with what became of it", () => {
    const text = readFileSync(join(pluginRoot, "METHODS.md"), "utf8")
    for (const tool of [
      "blender_attach",
      "blender_detach",
      "blender_status",
      "blender_catalog",
      "blender_docs",
      "blender_audit",
    ])
      assert.match(text, new RegExp(`\\| \`${tool}\` +\\| (replaced|retired) +\\|`))
    const catalog = text.split("\n").filter((line) => /^\| [a-z_]+ +\| `[a-z_]+\.[a-z_]+`/.test(line))
    assert.equal(catalog.length, 79)
    assert.match(text, /git checkout ec99ef2 -- integrations\/blender\/plugin\/methods\/host/)
    for (const manifest of toolManifests()) assert.ok(text.includes(`\`${manifest.name}\``), manifest.name)
  })
})
