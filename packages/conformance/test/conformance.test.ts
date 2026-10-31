import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { createHash } from "node:crypto"
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { after, describe, it } from "node:test"
import { deflateRawSync } from "node:zlib"

import {
  CATALOG_MAX_BYTES,
  checkCatalogBytes,
  checkRepository,
  hostPartLicense,
  readZipEntries,
  validateCatalogSchema,
  validateMarkerSchema,
  ZIP_MAX_BYTES,
} from "../src/index.ts"

const roots: string[] = []
after(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true })
})

const payloadBytes = Buffer.from("host payload")
const payloadDigest = createHash("sha256").update(payloadBytes).digest("hex")

function marker(id: string, overrides: Record<string, unknown> = {}) {
  return {
    spec: 0,
    id,
    displayName: `OpenCode for ${id}`,
    family: "native",
    plugin: `neuralnomads.${id}`,
    rpc: `neuralnomads.${id}`,
    host: {
      app: id,
      protocol: 1,
      range: "^0.1.0",
      appVersions: ">=5.2",
      payload: `host/${id}/part-0.1.0.zip`,
      sha256: payloadDigest,
    },
    ...overrides,
  }
}

function packageJson(id: string, overrides: Record<string, unknown> = {}) {
  return {
    name: `@neuralnomads/opencode-${id}`,
    version: "0.1.0",
    type: "module",
    license: "MIT",
    files: ["dist", "host", "skills"],
    keywords: ["opencode-integration"],
    peerDependencies: { "@opencode/plugin": "^2.2.0 || ^0.0.0-dev" },
    peerDependenciesMeta: { "@opencode/plugin": { optional: true } },
    devDependencies: { "@opencode/plugin": "2.2.5" },
    opencodeIntegration: marker(id),
    ...overrides,
  }
}

function catalogEntry(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    package: `@neuralnomads/opencode-${id}`,
    range: "^0.1",
    displayName: `OpenCode for ${id}`,
    family: "native",
    plugin: `neuralnomads.${id}`,
    rpc: `neuralnomads.${id}`,
    summary: { en: "Demo." },
    app: { name: id, versions: ">=5.2" },
    host: { protocol: 1, range: "^0.1.0" },
    docs: `https://github.com/NeuralNomadsAI/opencode-integrations/tree/main/integrations/${id}`,
    status: "preview",
    ...overrides,
  }
}

// A ZIP archive of deflated, or stored, entries.
function zip(entries: Record<string, string>, deflate = true) {
  const locals: Buffer[] = []
  const centrals: Buffer[] = []
  let offset = 0
  for (const [name, text] of Object.entries(entries)) {
    const content = Buffer.from(text)
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

const GPL3_TEXT = "                    GNU GENERAL PUBLIC LICENSE\n                       Version 3, 29 June 2007\n"
const gplPart = (overrides: Record<string, string> = {}, deflate = true) =>
  zip(
    {
      LICENSE: GPL3_TEXT,
      "__init__.py": "# SPDX-License-Identifier: GPL-3.0-or-later\n",
      "blender_manifest.toml": 'license = ["SPDX:GPL-3.0-or-later"]\n',
      "panel.js": "/* SPDX-License-Identifier: GPL-3.0-or-later */\n",
      "strings.json": '{ "header": "// SPDX-License-Identifier: GPL-3.0-or-later" }\n',
      ...overrides,
    },
    deflate,
  )

// A package embedding the given payload, its marker digest matching.
function withPayload(id: string, payload: Buffer, overrides: Record<string, unknown> = {}) {
  const host = { ...marker(id).host, sha256: createHash("sha256").update(payload).digest("hex") }
  return packageJson(id, { opencodeIntegration: marker(id, { host }), ...overrides })
}

type Fixture = {
  packages?: Record<string, unknown>
  payloads?: Record<string, Buffer>
  catalog?: unknown
  files?: Record<string, string>
}

function repository(fixture: Fixture = {}) {
  const root = mkdtempSync(join(tmpdir(), "conformance-"))
  roots.push(root)
  const write = (file: string, content: string | Buffer) => {
    mkdirSync(dirname(join(root, file)), { recursive: true })
    writeFileSync(join(root, file), content)
  }
  write("package.json", JSON.stringify({ name: "fixture", private: true }))
  write("catalog.json", JSON.stringify(fixture.catalog ?? { schemaVersion: 1, integrations: [] }))
  for (const [dir, json] of Object.entries(fixture.packages ?? {})) {
    write(`integrations/${dir}/plugin/package.json`, JSON.stringify(json))
    write(`integrations/${dir}/plugin/host/${dir}/part-0.1.0.zip`, fixture.payloads?.[dir] ?? payloadBytes)
  }
  for (const [file, content] of Object.entries(fixture.files ?? {})) write(file, content)
  execFileSync("git", ["init", "-q"], { cwd: root })
  return root
}

function errorsOf(fixture: Fixture) {
  return checkRepository(repository(fixture)).errors.join("\n")
}

function licenseErrorsOf(fixture: Fixture) {
  return checkRepository(repository(fixture))
    .errors.filter((error) => error.includes('"license"'))
    .join("\n")
}

describe("repository conformance", () => {
  it("accepts a conforming integration listed in the catalog", () => {
    const report = checkRepository(
      repository({
        packages: { demo: packageJson("demo") },
        catalog: { schemaVersion: 1, integrations: [catalogEntry("demo")] },
        files: {
          "integrations/demo/plugin/skills/demo-scene/SKILL.md":
            "---\nname: demo-scene\ndescription: Scenes.\n---\nBody\n",
          "integrations/demo/test/conformance.test.ts":
            'import { checkCodeExecutionGate, checkHostListener, runSentinelScenario } from "@neuralnomads/integration-conformance"\n',
        },
      }),
    )
    assert.deepEqual(report.errors, [])
    assert.deepEqual(report.integrations, ["@neuralnomads/opencode-demo"])
  })

  it("accepts this repository", () => {
    const report = checkRepository(join(import.meta.dirname, "..", "..", ".."))
    assert.deepEqual(report.errors, [])
  })

  it("fails a package without a marker", () => {
    const { opencodeIntegration: _, ...withoutMarker } = packageJson("demo")
    assert.match(errorsOf({ packages: { demo: withoutMarker } }), /missing opencodeIntegration marker/)
  })

  it("fails an rpc other than neuralnomads.<id>", () => {
    const json = packageJson("demo", { opencodeIntegration: marker("demo", { rpc: "neuralnomads.other" }) })
    assert.match(errorsOf({ packages: { demo: json } }), /marker rpc must be "neuralnomads.demo"/)
  })

  it("fails an ID that differs from the package name", () => {
    const json = packageJson("demo", { opencodeIntegration: marker("other") })
    assert.match(errorsOf({ packages: { demo: json } }), /marker id must be "demo"/)
  })

  it("fails a host payload with a wrong digest", () => {
    const wrong = marker("demo")
    wrong.host.sha256 = "0".repeat(64)
    assert.match(
      errorsOf({ packages: { demo: packageJson("demo", { opencodeIntegration: wrong }) } }),
      /sha256 does not match/,
    )
  })

  it("requires MIT AND GPL-3.0-or-later exactly when the embedded host part is GPL-3.0-or-later", () => {
    const gpl = "MIT AND GPL-3.0-or-later"
    for (const payload of [gplPart(), gplPart({}, false)]) {
      assert.deepEqual(hostPartLicense(payload), { license: "GPL-3.0-or-later" })
      const errors = (license?: string) =>
        licenseErrorsOf({
          packages: { demo: withPayload("demo", payload, license ? { license } : {}) },
          payloads: { demo: payload },
        })
      assert.equal(errors(gpl), "")
      assert.match(errors(), /"license" must be "MIT AND GPL-3.0-or-later": the embedded host part is GPL-3.0-or-later/)
      for (const license of ["GPL-3.0-or-later AND MIT", "MIT OR GPL-3.0-or-later", "(MIT AND GPL-3.0-or-later)"]) {
        assert.match(errors(license), /"license" must be "MIT AND GPL-3.0-or-later"/, license)
      }
    }
  })

  it("keeps requiring MIT when no GPL-3.0-or-later host part is embedded", () => {
    const gpl = "MIT AND GPL-3.0-or-later"
    const mustBeMit = /"license" must be "MIT" \(only a package embedding/
    // A package without a host part.
    const { host: _, ...api } = marker("demo", { family: "api" })
    assert.match(
      licenseErrorsOf({ packages: { demo: packageJson("demo", { license: gpl, opencodeIntegration: api }) } }),
      mustBeMit,
    )
    // Payloads that do not establish GPL-3.0-or-later, and the reason the error gives.
    const notGpl: Record<string, [Buffer, RegExp]> = {
      "not an archive": [Buffer.from("host payload"), /not a readable ZIP archive \(no end of central directory\)/],
      "no LICENSE": [zip({ "__init__.py": "# SPDX-License-Identifier: GPL-3.0-or-later\n" }), /no GNU GPL version 3/],
      "LICENSE in a folder": [
        zip({ "part/LICENSE": GPL3_TEXT, "part/a.py": "# SPDX-License-Identifier: GPL-3.0-or-later\n" }),
        /no GNU GPL version 3/,
      ],
      "GPL-2 text": [
        gplPart({ LICENSE: "GNU GENERAL PUBLIC LICENSE\nVersion 2, June 1991\n" }),
        /no GNU GPL version 3/,
      ],
      "no SPDX declaration": [zip({ LICENSE: GPL3_TEXT, "__init__.py": "\n" }), /declares no SPDX licence/],
      "GPL-3.0-only": [
        gplPart({ "__init__.py": "# SPDX-License-Identifier: GPL-3.0-only\n" }),
        /SPDX declarations are "GPL-3.0-only", "GPL-3.0-or-later"/,
      ],
      "a second licence": [gplPart({ "vendor.py": "# SPDX-License-Identifier: Apache-2.0\n" }), /"Apache-2.0"/],
      "a compound header": [
        gplPart({ "vendor.py": "# SPDX-License-Identifier: GPL-3.0-or-later AND MIT\n" }),
        /"GPL-3.0-or-later AND MIT"/,
      ],
      "an exception": [
        gplPart({ "vendor.c": "/* SPDX-License-Identifier: GPL-3.0-or-later WITH GCC-exception-3.1 */\n" }),
        /"GPL-3.0-or-later WITH GCC-exception-3.1"/,
      ],
      "a compound manifest entry": [
        gplPart({ "blender_manifest.toml": 'license = ["SPDX:GPL-3.0-or-later OR MIT"]\n' }),
        /"GPL-3.0-or-later OR MIT"/,
      ],
      "BSD part": [
        zip({ LICENSE: "BSD 3-Clause License\n", "a.py": "# SPDX-License-Identifier: BSD-3-Clause\n" }),
        /no GNU GPL version 3/,
      ],
    }
    for (const [label, [payload, reason]] of Object.entries(notGpl)) {
      const result = hostPartLicense(payload)
      assert.equal(result.license, null, label)
      const errors = (license?: string) =>
        licenseErrorsOf({
          packages: { demo: withPayload("demo", payload, license ? { license } : {}) },
          payloads: { demo: payload },
        })
      assert.equal(errors(), "", label)
      assert.match(errors(gpl), mustBeMit, label)
      assert.match(errors(gpl), reason, label)
    }
  })

  it("refuses payload archives over the decompressed size bound", () => {
    const huge = Buffer.from(zip({ LICENSE: GPL3_TEXT }))
    // Claim an uncompressed size over the bound in the central directory entry.
    huge.writeUInt32LE(ZIP_MAX_BYTES + 1, huge.readUInt32LE(huge.length - 6) + 24)
    assert.throws(() => readZipEntries(huge), /bytes once decompressed/)
    const result = hostPartLicense(huge)
    assert.ok(result.license === null && /not a readable ZIP archive/.test(result.reason))
  })

  it("fails two integrations sharing a plugin or rpc ID", () => {
    const clash = packageJson("other", {
      opencodeIntegration: marker("other", { plugin: "neuralnomads.demo", rpc: "neuralnomads.demo" }),
    })
    const errors = errorsOf({ packages: { demo: packageJson("demo"), other: clash } })
    assert.match(errors, /marker plugin "neuralnomads.demo" is already used/)
    assert.match(errors, /marker rpc "neuralnomads.demo" is already used/)
  })

  it("enforces the optional SDK peer, the pinned dev version and no install scripts", () => {
    const json = packageJson("demo", {
      peerDependencies: { "@opencode/plugin": "^2.0.0" },
      peerDependenciesMeta: {},
      devDependencies: { "@opencode/plugin": "2.0.22" },
      scripts: { postinstall: "node setup.js" },
    })
    const errors = errorsOf({ packages: { demo: json } })
    assert.match(errors, /peerDependencies\["@opencode\/plugin"\] must be/)
    assert.match(errors, /optional must be true/)
    assert.match(errors, /devDependencies\["@opencode\/plugin"\] must be "2.2.5"/)
    assert.match(errors, /install script "postinstall"/)
  })

  it("fails the legacy SDK, _runtime copies and committed node_modules", () => {
    const errors = errorsOf({
      files: {
        "packages/old/package.json": JSON.stringify({ dependencies: { "@opencode-ai/plugin": "beta" } }),
        "integrations/demo/plugin/_runtime/index.ts": "export {}\n",
        "packages/old/node_modules/x/index.js": "\n",
      },
    })
    assert.match(errors, /@opencode-ai\/plugin is not allowed/)
    assert.match(errors, /_runtime copies are replaced/)
    assert.match(errors, /node_modules must not be committed/)
  })

  it("fails skills without a valid header or prefix", () => {
    const errors = errorsOf({
      packages: { demo: packageJson("demo") },
      files: {
        "integrations/demo/plugin/skills/demo-a/SKILL.md": "No header\n",
        "integrations/demo/plugin/skills/scene/SKILL.md": "---\nname: scene\ndescription: x\n---\n",
      },
    })
    assert.match(errors, /demo-a\/SKILL.md has no front-matter header/)
    assert.match(errors, /skill scene must be named demo-<topic>/)
  })

  it("fails a catalog entry that differs from the marker or has no package", () => {
    const errors = errorsOf({
      packages: { demo: packageJson("demo") },
      catalog: {
        schemaVersion: 1,
        integrations: [catalogEntry("demo", { host: { protocol: 2, range: "^0.1.0" } }), catalogEntry("ghost")],
      },
    })
    assert.match(errors, /catalog entry demo: host.protocol differs/)
    assert.match(errors, /catalog entry ghost: no conforming package/)
  })
})

describe("catalog", () => {
  const bytes = (value: unknown) => Buffer.from(JSON.stringify(value))

  it("accepts the repository catalog", () => {
    const own = readFileSync(join(import.meta.dirname, "..", "..", "..", "catalog.json"))
    assert.deepEqual(checkCatalogBytes(own).errors, [])
  })

  it("rejects duplicate id, package, plugin and rpc", () => {
    const entry = catalogEntry("demo")
    const { errors } = checkCatalogBytes(bytes({ schemaVersion: 1, integrations: [entry, entry] }))
    for (const field of ["id", "package", "plugin", "rpc"]) {
      assert.ok(
        errors.some((error) => error.includes(`two entries with ${field}`)),
        field,
      )
    }
  })

  it("rejects more than 64 integrations and documents over 256 KiB", () => {
    const many = Array.from({ length: 65 }, (_, index) => catalogEntry(`app${index}`))
    assert.match(
      checkCatalogBytes(bytes({ schemaVersion: 1, integrations: many })).errors.join("\n"),
      /must NOT have more than 64 items/,
    )
    const big = Buffer.alloc(CATALOG_MAX_BYTES + 1, 0x20)
    assert.match(checkCatalogBytes(big).errors.join("\n"), /over the 262144 byte cap/)
  })

  it("rejects non-JSON, unknown fields, missing host and foreign docs", () => {
    assert.match(checkCatalogBytes(Buffer.from("{")).errors.join("\n"), /not valid UTF-8 JSON/)
    const errors = (overrides: Record<string, unknown>) =>
      checkCatalogBytes(bytes({ schemaVersion: 1, integrations: [catalogEntry("demo", overrides)] })).errors
    assert.notDeepEqual(errors({ extra: true }), [])
    assert.notDeepEqual(errors({ host: undefined }), [])
    assert.notDeepEqual(errors({ docs: "https://example.com/" }), [])
    assert.notDeepEqual(errors({ summary: { fr: "Sans anglais" } }), [])
  })
})

describe("marker schema", () => {
  it("accepts the documented examples", () => {
    const schema = JSON.parse(
      readFileSync(join(import.meta.dirname, "..", "..", "..", "spec", "schemas", "marker.schema.json"), "utf8"),
    )
    for (const example of schema.examples) assert.equal(validateMarkerSchema(example), true)
    const catalogSchema = JSON.parse(
      readFileSync(join(import.meta.dirname, "..", "..", "..", "spec", "schemas", "catalog.schema.json"), "utf8"),
    )
    for (const example of catalogSchema.examples) assert.equal(validateCatalogSchema(example), true)
  })

  it("rejects spec 1 during the draft, host outside native and vendor outside vendor-mcp", () => {
    assert.equal(validateMarkerSchema(marker("demo", { spec: 1 })), false)
    assert.equal(validateMarkerSchema(marker("demo", { family: "api" })), false)
    const { host: _, ...api } = marker("demo", { family: "api" })
    assert.equal(validateMarkerSchema(api), true)
    assert.equal(validateMarkerSchema({ ...api, vendor: { server: "X" } }), false)
    assert.equal(validateMarkerSchema(marker("demo", { host: undefined })), false)
  })
})
