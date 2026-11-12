// The markers of this repository's integrations and the catalog that copies them (spec/README.md, sections 1
// and 4): what CodeNomad needs to recognise an integration, and what the plugin needs to tell its application
// part's version.

import { createHash } from "node:crypto"
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs"
import { join } from "node:path"

import semver from "semver"

import { checkPluginLoad } from "./load.ts"

type Family = "native" | "vendor-mcp" | "api"

export type Marker = {
  spec: 0
  id: string
  displayName: string
  family: Family
  plugin: string
  rpc: string
  host?: { app: string; protocol: number; range: string; appVersions: string; payload: string; sha256: string }
  vendor?: { server: string; minAppVersion?: string }
}

type PackageJson = { name?: string; version?: string; exports?: unknown; main?: string; opencodeIntegration?: unknown }

export type IntegrationPackage = { dir: string; packageJson: PackageJson; marker: Marker | null; errors: string[] }

export type RepositoryReport = { ok: boolean; integrations: string[]; errors: string[] }

// Bounds of the catalog CodeNomad reads.
export const CATALOG_MAX_BYTES = 256 * 1024
export const CATALOG_MAX_INTEGRATIONS = 64

const FAMILIES: Family[] = ["native", "vendor-mcp", "api"]
const ID = /^[a-z][a-z0-9-]{0,31}$/
const DISPLAY_NAME = /^OpenCode for \S.*$/
const SHA256 = /^[0-9a-f]{64}$/
const PAYLOAD = /^host\/[a-z][a-z0-9-]*\/[A-Za-z0-9._-]+$/
const RANGE = /^[0-9xX*^~<>=|. -]+$/
const LOCALE = /^[a-z]{2}(-[A-Z][A-Za-z]{1,3})?$/
const DOCS = "https://github.com/NeuralNomadsAI/opencode-integrations/"
const GIT = "github:NeuralNomadsAI/opencode-integrations#"

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)
const isText = (value: unknown, max: number, pattern?: RegExp) =>
  typeof value === "string" && [...value].length >= 1 && [...value].length <= max && (!pattern || pattern.test(value))
const isRange = (value: unknown) => isText(value, 64, RANGE) && semver.validRange(value as string) !== null
const unknownKeys = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).filter((key) => !keys.includes(key))

function readMarker(value: unknown, packageName: string | undefined): { marker: Marker | null; errors: string[] } {
  if (!isObject(value)) return { marker: null, errors: ["no opencodeIntegration marker"] }
  const errors: string[] = []
  const id = /^@neuralnomads\/opencode-([a-z][a-z0-9-]{0,31})$/.exec(packageName ?? "")?.[1]
  if (!id) errors.push("the package name is not @neuralnomads/opencode-<id>")
  if (value.spec !== 0) errors.push("marker spec is not 0")
  if (!isText(value.id, 32, ID) || (id && value.id !== id)) errors.push(`marker id is not "${id ?? "<id>"}"`)
  if (!isText(value.displayName, 60, DISPLAY_NAME)) errors.push('marker displayName is not "OpenCode for <app>"')
  if (!FAMILIES.includes(value.family as Family)) errors.push("marker family is not native, vendor-mcp or api")
  for (const field of ["plugin", "rpc"]) {
    if (value[field] !== `neuralnomads.${value.id}`) errors.push(`marker ${field} is not "neuralnomads.${value.id}"`)
  }
  if ((value.family === "native") !== (value.host !== undefined))
    errors.push("marker host is present exactly for a native integration")
  if (value.host !== undefined) {
    const host = isObject(value.host) ? value.host : {}
    if (!isText(host.app, 32, ID)) errors.push("marker host.app is not an ID")
    if (!Number.isInteger(host.protocol) || (host.protocol as number) < 1)
      errors.push("marker host.protocol is not a positive integer")
    for (const field of ["range", "appVersions"])
      if (!isRange(host[field])) errors.push(`marker host.${field} is not a version range`)
    if (!isText(host.payload, 200, PAYLOAD)) errors.push("marker host.payload is not host/<app>/<file>")
    if (!isText(host.sha256, 64, SHA256)) errors.push("marker host.sha256 is not a sha256")
  }
  if (value.vendor !== undefined && (!isObject(value.vendor) || !isText(value.vendor.server, 80)))
    errors.push("marker vendor.server is not a name")
  return { marker: errors.length === 0 ? (value as Marker) : null, errors }
}

export function findIntegrationPackages(root: string): IntegrationPackage[] {
  const integrationsDir = join(root, "integrations")
  if (!existsSync(integrationsDir)) return []
  return readdirSync(integrationsDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && existsSync(join(integrationsDir, entry.name, "plugin", "package.json")))
    .map((entry) => {
      const dir = join(integrationsDir, entry.name, "plugin")
      const packageJson = JSON.parse(readFileSync(join(dir, "package.json"), "utf8")) as PackageJson
      return { dir, packageJson, ...readMarker(packageJson.opencodeIntegration, packageJson.name) }
    })
    .sort((a, b) => a.dir.localeCompare(b.dir))
}

// The embedded application part: the payload the marker names, and a range that contains its version.
function checkHostPart(pkg: IntegrationPackage, label: string): string[] {
  const host = pkg.marker?.host
  if (!host) return []
  const errors: string[] = []
  const payload = join(pkg.dir, ...host.payload.split("/"))
  if (!existsSync(payload) || !statSync(payload).isFile())
    errors.push(`${label}: host payload ${host.payload} is missing`)
  else if (createHash("sha256").update(readFileSync(payload)).digest("hex") !== host.sha256)
    errors.push(`${label}: host payload sha256 differs from the marker`)
  const version = pkg.packageJson.version ?? ""
  if (semver.valid(version) !== version) errors.push(`${label}: "version" is not a semver version`)
  else if (!semver.satisfies(version, host.range, { includePrerelease: true }))
    errors.push(`${label}: host.range "${host.range}" does not contain the embedded part ${version}`)
  return errors
}

const ENTRY_KEYS = [
  "id",
  "package",
  "range",
  "displayName",
  "family",
  "plugin",
  "rpc",
  "summary",
  "app",
  "host",
].concat(["vendor", "docs", "status", "sources"])

// One catalog entry in the form CodeNomad accepts (it refuses the whole catalog otherwise).
function checkCatalogEntry(entry: unknown, index: number): string[] {
  const label = `catalog entry ${index}`
  if (!isObject(entry)) return [`${label}: not an object`]
  const errors = unknownKeys(entry, ENTRY_KEYS).map((key) => `${label}: unknown field ${key}`)
  if (!isText(entry.id, 32, ID)) errors.push(`${label}: id is not an ID`)
  if (entry.package !== `@neuralnomads/opencode-${entry.id}`) errors.push(`${label}: package is not its npm package`)
  if (!isRange(entry.range)) errors.push(`${label}: range is not a version range`)
  if (!isText(entry.displayName, 60, DISPLAY_NAME)) errors.push(`${label}: displayName is not "OpenCode for <app>"`)
  if (!FAMILIES.includes(entry.family as Family)) errors.push(`${label}: family is unknown`)
  for (const field of ["plugin", "rpc"])
    if (!isText(entry[field], 46, /^neuralnomads\.[a-z][a-z0-9-]{0,31}$/))
      errors.push(`${label}: ${field} is not an ID`)
  const summary = isObject(entry.summary) ? entry.summary : {}
  const locales = Object.keys(summary)
  if (!locales.includes("en") || locales.length > 16) errors.push(`${label}: summary needs en, at most 16 locales`)
  for (const locale of locales)
    if (!LOCALE.test(locale) || !isText(summary[locale], 300)) errors.push(`${label}: summary ${locale} is invalid`)
  const app = isObject(entry.app) ? entry.app : {}
  if (
    !isText(app.name, 60) ||
    (app.versions !== undefined && !isRange(app.versions)) ||
    unknownKeys(app, ["name", "versions"]).length
  )
    errors.push(`${label}: app is not { name, versions? }`)
  if ((entry.family === "native") !== (entry.host !== undefined))
    errors.push(`${label}: host is present exactly for native`)
  if (entry.host !== undefined) {
    const host = isObject(entry.host) ? entry.host : {}
    if (!Number.isInteger(host.protocol) || !isRange(host.range) || unknownKeys(host, ["protocol", "range"]).length)
      errors.push(`${label}: host is not { protocol, range }`)
  }
  if (entry.vendor !== undefined) {
    const vendor = isObject(entry.vendor) ? entry.vendor : {}
    if (
      entry.family !== "vendor-mcp" ||
      !isText(vendor.server, 80) ||
      unknownKeys(vendor, ["server", "minAppVersion"]).length
    )
      errors.push(`${label}: vendor is not { server, minAppVersion? } of a vendor-mcp integration`)
  }
  if (!isText(entry.docs, 300) || !(entry.docs as string).startsWith(DOCS))
    errors.push(`${label}: docs is not in this repository`)
  if (!["preview", "stable", "deprecated"].includes(entry.status as string)) errors.push(`${label}: status is unknown`)
  if (entry.sources !== undefined) {
    const git = isObject(entry.sources) ? entry.sources.git : undefined
    if (!Array.isArray(git) || git.length > 8 || !git.every((source) => isText(source, 200) && source.startsWith(GIT)))
      errors.push(`${label}: sources is not { git: [${GIT}…] }`)
  }
  return errors
}

export type CatalogEntry = {
  id: string
  package: string
  displayName: string
  family: Family
  plugin: string
  rpc: string
  app: { name: string; versions?: string }
  host?: { protocol: number; range: string }
  vendor?: { server: string; minAppVersion?: string }
}

export function checkCatalogBytes(bytes: Uint8Array): {
  catalog: { integrations: CatalogEntry[] } | null
  errors: string[]
} {
  if (bytes.byteLength > CATALOG_MAX_BYTES)
    return { catalog: null, errors: [`catalog.json is over ${CATALOG_MAX_BYTES} bytes`] }
  let document: unknown
  try {
    document = JSON.parse(Buffer.from(bytes).toString("utf8"))
  } catch {
    return { catalog: null, errors: ["catalog.json is not JSON"] }
  }
  if (!isObject(document) || document.schemaVersion !== 1 || !Array.isArray(document.integrations))
    return { catalog: null, errors: ["catalog.json is not { schemaVersion: 1, integrations: [...] }"] }
  const entries = document.integrations
  const errors = unknownKeys(document, ["schemaVersion", "integrations"]).map(
    (key) => `catalog.json: unknown field ${key}`,
  )
  if (entries.length > CATALOG_MAX_INTEGRATIONS)
    errors.push(`catalog.json lists over ${CATALOG_MAX_INTEGRATIONS} integrations`)
  for (const [index, entry] of entries.entries()) errors.push(...checkCatalogEntry(entry, index))
  for (const field of ["id", "package", "plugin", "rpc"]) {
    const values = entries.map((entry) => (isObject(entry) ? entry[field] : undefined))
    if (new Set(values).size !== values.length) errors.push(`catalog.json: two entries share a ${field}`)
  }
  return { catalog: errors.length === 0 ? (document as { integrations: CatalogEntry[] }) : null, errors }
}

// The fields a catalog entry copies from its package's marker.
function catalogMismatches(entry: CatalogEntry, marker: Marker): string[] {
  const pairs: [string, unknown, unknown][] = [
    ["displayName", entry.displayName, marker.displayName],
    ["family", entry.family, marker.family],
    ["plugin", entry.plugin, marker.plugin],
    ["rpc", entry.rpc, marker.rpc],
    ["host.protocol", entry.host?.protocol, marker.host?.protocol],
    ["host.range", entry.host?.range, marker.host?.range],
    ["app.versions", entry.app.versions, marker.host?.appVersions ?? entry.app.versions],
    ["vendor.server", entry.vendor?.server, marker.vendor?.server],
    ["vendor.minAppVersion", entry.vendor?.minAppVersion, marker.vendor?.minAppVersion],
  ]
  return pairs.filter(([, a, b]) => a !== b).map(([field]) => field)
}

export function checkRepository(root: string): RepositoryReport {
  const errors: string[] = []
  const packages = findIntegrationPackages(root)
  for (const pkg of packages) {
    const label = pkg.packageJson.name ?? pkg.dir
    errors.push(...pkg.errors.map((error) => `${label}: ${error}`), ...checkHostPart(pkg, label))
  }
  const catalogPath = join(root, "catalog.json")
  if (!existsSync(catalogPath)) errors.push("catalog.json is missing")
  else {
    const { catalog, errors: catalogErrors } = checkCatalogBytes(readFileSync(catalogPath))
    errors.push(...catalogErrors)
    for (const entry of catalog?.integrations ?? []) {
      const pkg = packages.find((candidate) => candidate.packageJson.name === entry.package)
      if (!pkg?.marker) errors.push(`catalog entry ${entry.id}: no package ${entry.package} with a marker`)
      else
        for (const field of catalogMismatches(entry, pkg.marker))
          errors.push(`catalog entry ${entry.id}: ${field} differs from the marker`)
    }
  }
  return { ok: errors.length === 0, integrations: packages.map((pkg) => pkg.packageJson.name ?? pkg.dir), errors }
}

// Loads every built integration package with a marker and reads its status.
export async function checkRepositoryLoads(root: string): Promise<string[]> {
  const errors: string[] = []
  for (const pkg of findIntegrationPackages(root)) if (pkg.marker) errors.push(...(await checkPluginLoad(pkg)).errors)
  return errors
}
