import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { createHash } from "node:crypto"
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { after, describe, it } from "node:test"

import {
  CATALOG_MAX_BYTES,
  checkCatalogBytes,
  checkRepository,
  validateCatalogSchema,
  validateMarkerSchema,
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

type Fixture = { packages?: Record<string, unknown>; catalog?: unknown; files?: Record<string, string> }

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
    write(`integrations/${dir}/plugin/host/${dir}/part-0.1.0.zip`, payloadBytes)
  }
  for (const [file, content] of Object.entries(fixture.files ?? {})) write(file, content)
  execFileSync("git", ["init", "-q"], { cwd: root })
  return root
}

function errorsOf(fixture: Fixture) {
  return checkRepository(repository(fixture)).errors.join("\n")
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
