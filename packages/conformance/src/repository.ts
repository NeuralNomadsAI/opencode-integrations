import { execFileSync } from "node:child_process"
import { createHash } from "node:crypto"
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs"
import { dirname, join, posix, relative, sep } from "node:path"

import semver from "semver"

import { type CatalogEntry, checkCatalogBytes } from "./catalog.ts"
import { checkDangerousManifests, type ManifestLike } from "./code-execution.ts"
import { checkPluginLoad } from "./load.ts"
import { formatSchemaErrors, validateMarkerSchema } from "./schemas.ts"

// Peer range of the OpenCode plugin SDK, declared optional: the OpenCode
// binary provides the SDK, and a required peer would install it (~130 MB).
export const PLUGIN_SDK = "@opencode/plugin"
export const PLUGIN_SDK_PEER_RANGE = "^2.2.0 || ^0.0.0-dev"
export const PLUGIN_SDK_DEV_VERSION = "2.2.5"
export const LEGACY_PLUGIN_SDK = "@opencode-ai/plugin"
export const NPM_KEYWORD = "opencode-integration"
// Behaviour checks every native integration's tests run (CNF-1).
export const NATIVE_BEHAVIOUR_CHECKS = ["checkHostListener", "checkCodeExecutionGate", "runSentinelScenario"]

type Marker = {
  spec: 0
  id: string
  displayName: string
  family: "native" | "vendor-mcp" | "api"
  plugin: string
  rpc: string
  host?: { app: string; protocol: number; range: string; appVersions: string; payload: string; sha256: string }
  vendor?: { server: string; minAppVersion?: string }
}

type PackageJson = {
  name?: string
  version?: string
  exports?: unknown
  main?: string
  type?: string
  license?: string
  files?: string[]
  keywords?: string[]
  scripts?: Record<string, string>
  dependencies?: Record<string, string>
  devDependencies?: Record<string, string>
  peerDependencies?: Record<string, string>
  peerDependenciesMeta?: Record<string, { optional?: boolean }>
  opencodeIntegration?: unknown
}

export type IntegrationPackage = { dir: string; packageJson: PackageJson; marker: Marker | null }

export type RepositoryReport = { ok: boolean; integrations: string[]; errors: string[] }

const INSTALL_SCRIPTS = ["preinstall", "install", "postinstall", "prepare"]

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, "utf8")) as T
}

// Files of the repository, relative and with forward slashes: tracked and
// untracked-but-not-ignored files in a Git checkout, else a directory walk.
export function listRepositoryFiles(root: string): string[] {
  try {
    const output = execFileSync("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], {
      cwd: root,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    })
    if (existsSync(join(root, ".git"))) {
      return output
        .split("\0")
        .filter(Boolean)
        .filter((file) => existsSync(join(root, file)))
    }
  } catch {
    // Not a Git checkout: walk the directory.
  }
  const files: string[] = []
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === ".git" || entry.name === "node_modules" || entry.name === "dist") continue
      const full = join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (entry.isFile()) files.push(relative(root, full).split(sep).join(posix.sep))
    }
  }
  walk(root)
  return files
}

export function findIntegrationPackages(root: string): IntegrationPackage[] {
  const integrationsDir = join(root, "integrations")
  if (!existsSync(integrationsDir)) return []
  return readdirSync(integrationsDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && existsSync(join(integrationsDir, entry.name, "plugin", "package.json")))
    .map((entry) => {
      const dir = join(integrationsDir, entry.name, "plugin")
      const packageJson = readJson<PackageJson>(join(dir, "package.json"))
      const marker = validateMarkerSchema(packageJson.opencodeIntegration)
        ? (packageJson.opencodeIntegration as Marker)
        : null
      return { dir, packageJson, marker }
    })
    .sort((a, b) => a.dir.localeCompare(b.dir))
}

function checkSkills(pkg: IntegrationPackage, label: string, errors: string[]) {
  const skillsDir = join(pkg.dir, "skills")
  if (!existsSync(skillsDir)) return
  for (const entry of readdirSync(skillsDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const file = join(skillsDir, entry.name, "SKILL.md")
    if (!existsSync(file)) {
      errors.push(`${label}: skills/${entry.name} has no SKILL.md`)
      continue
    }
    const text = readFileSync(file, "utf8").replace(/\r\n/g, "\n")
    const header = /^---\n([\s\S]*?)\n---\n/.exec(text)
    const fields = new Map(
      (header?.[1] ?? "")
        .split("\n")
        .map((line) => /^([a-z][a-z0-9_-]*):\s*(.*)$/i.exec(line))
        .filter((match): match is RegExpExecArray => match !== null)
        .map((match) => [match[1] ?? "", (match[2] ?? "").trim()]),
    )
    if (!header) errors.push(`${label}: skills/${entry.name}/SKILL.md has no front-matter header`)
    else {
      if (fields.get("name") !== entry.name) {
        errors.push(`${label}: skills/${entry.name}/SKILL.md header name must be "${entry.name}"`)
      }
      if (!fields.get("description")) errors.push(`${label}: skills/${entry.name}/SKILL.md header needs a description`)
    }
    if (pkg.marker && !entry.name.startsWith(`${pkg.marker.id}-`)) {
      errors.push(`${label}: skill ${entry.name} must be named ${pkg.marker.id}-<topic>`)
    }
  }
}

// Method manifests under methods/**.json; unreadable files are reported as such.
function readManifests(dir: string): ManifestLike[] {
  const root = join(dir, "methods")
  if (!existsSync(root)) return []
  const manifests: ManifestLike[] = []
  const walk = (current: string) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const full = join(current, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (entry.isFile() && entry.name.endsWith(".json")) {
        try {
          manifests.push(readJson<ManifestLike>(full))
        } catch {
          manifests.push({ name: `unreadable manifest ${relative(dir, full)}`, dangerous: false })
        }
      }
    }
  }
  walk(root)
  return manifests
}

function checkPackage(pkg: IntegrationPackage, errors: string[]) {
  const json = pkg.packageJson
  const label = json.name ?? relative(process.cwd(), pkg.dir)
  const match = /^@neuralnomads\/opencode-([a-z][a-z0-9-]{0,31})$/.exec(json.name ?? "")
  if (!match) errors.push(`${label}: package name must be @neuralnomads/opencode-<app>`)

  if (json.opencodeIntegration === undefined) errors.push(`${label}: missing opencodeIntegration marker`)
  else if (!pkg.marker) {
    validateMarkerSchema(json.opencodeIntegration)
    errors.push(...formatSchemaErrors(`${label}: marker`, validateMarkerSchema.errors))
  }

  const marker = pkg.marker
  if (marker && match) {
    const expected = `neuralnomads.${match[1]}`
    if (marker.id !== match[1]) errors.push(`${label}: marker id must be "${match[1]}"`)
    if (marker.plugin !== expected) errors.push(`${label}: marker plugin must be "${expected}"`)
    if (marker.rpc !== expected) errors.push(`${label}: marker rpc must be "${expected}"`)
  }
  if (marker?.host) {
    const payload = join(pkg.dir, ...marker.host.payload.split("/"))
    if (!existsSync(payload) || !statSync(payload).isFile()) {
      errors.push(`${label}: host payload ${marker.host.payload} does not exist`)
    } else {
      const digest = createHash("sha256").update(readFileSync(payload)).digest("hex")
      if (digest !== marker.host.sha256) errors.push(`${label}: host payload sha256 does not match the marker`)
    }
  }

  if (!json.version || semver.valid(json.version) !== json.version) {
    errors.push(`${label}: "version" must be a semver version`)
  } else if (marker?.host && !semver.satisfies(json.version, marker.host.range, { includePrerelease: true })) {
    // The embedded host part has the package's version.
    errors.push(`${label}: host.range "${marker.host.range}" must contain the embedded host part ${json.version}`)
  }
  if (marker)
    errors.push(...checkDangerousManifests(marker.id, readManifests(pkg.dir)).map((error) => `${label}: ${error}`))

  if (json.type !== "module") errors.push(`${label}: "type" must be "module"`)
  if (json.license !== "MIT") errors.push(`${label}: "license" must be "MIT"`)
  if (!Array.isArray(json.files) || json.files.length === 0)
    errors.push(`${label}: "files" must list the package contents`)
  if (!json.keywords?.includes(NPM_KEYWORD)) errors.push(`${label}: keywords must include "${NPM_KEYWORD}"`)
  if (json.peerDependencies?.[PLUGIN_SDK] !== PLUGIN_SDK_PEER_RANGE) {
    errors.push(`${label}: peerDependencies["${PLUGIN_SDK}"] must be "${PLUGIN_SDK_PEER_RANGE}"`)
  }
  if (json.peerDependenciesMeta?.[PLUGIN_SDK]?.optional !== true) {
    errors.push(`${label}: peerDependenciesMeta["${PLUGIN_SDK}"].optional must be true`)
  }
  if (json.devDependencies?.[PLUGIN_SDK] !== PLUGIN_SDK_DEV_VERSION) {
    errors.push(`${label}: devDependencies["${PLUGIN_SDK}"] must be "${PLUGIN_SDK_DEV_VERSION}"`)
  }
  if (json.dependencies?.[PLUGIN_SDK] || json.dependencies?.effect) {
    errors.push(`${label}: the plugin SDK and effect must not be runtime dependencies`)
  }
  for (const script of INSTALL_SCRIPTS) {
    if (json.scripts?.[script]) errors.push(`${label}: install script "${script}" is not allowed`)
  }
  checkSkills(pkg, label, errors)
}

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
  const files = listRepositoryFiles(root)

  for (const file of files) {
    const segments = file.split("/")
    if (segments.includes("node_modules")) errors.push(`${file}: node_modules must not be committed`)
    if (segments.slice(0, -1).includes("_runtime"))
      errors.push(`${file}: _runtime copies are replaced by integration-kit`)
    if (segments.at(-1) === "package.json" && !segments.includes("node_modules")) {
      const text = readFileSync(join(root, file), "utf8")
      if (text.includes(`"${LEGACY_PLUGIN_SDK}"`)) errors.push(`${file}: ${LEGACY_PLUGIN_SDK} is not allowed`)
    }
  }

  const packages = findIntegrationPackages(root)
  for (const pkg of packages) checkPackage(pkg, errors)

  // CNF-1: a native integration's own tests run the behaviour checks against its plugin and host.
  for (const pkg of packages) {
    if (pkg.marker?.family !== "native") continue
    const folder = `${relative(root, dirname(pkg.dir)).split(sep).join(posix.sep)}/`
    const tests = files.filter((file) => file.startsWith(folder) && /\.test\.[cm]?[jt]s$/.test(file))
    const text = tests.map((file) => readFileSync(join(root, file), "utf8")).join("\n")
    for (const check of NATIVE_BEHAVIOUR_CHECKS) {
      if (!text.includes(check)) errors.push(`${pkg.packageJson.name}: the tests under ${folder} must run ${check}`)
    }
  }

  for (const field of ["plugin", "rpc"] as const) {
    const owners = new Map<string, string>()
    for (const pkg of packages) {
      const value = pkg.marker?.[field]
      if (!value) continue
      const owner = owners.get(value)
      if (owner) errors.push(`${pkg.packageJson.name}: marker ${field} "${value}" is already used by ${owner}`)
      else owners.set(value, pkg.packageJson.name ?? pkg.dir)
    }
  }

  const catalogPath = join(root, "catalog.json")
  if (!existsSync(catalogPath)) errors.push("catalog.json is missing")
  else {
    const { catalog, errors: catalogErrors } = checkCatalogBytes(readFileSync(catalogPath))
    errors.push(...catalogErrors)
    for (const entry of catalog?.integrations ?? []) {
      const pkg = packages.find((candidate) => candidate.packageJson.name === entry.package)
      if (!pkg?.marker) {
        errors.push(`catalog entry ${entry.id}: no conforming package ${entry.package} in integrations/`)
        continue
      }
      for (const field of catalogMismatches(entry, pkg.marker)) {
        errors.push(`catalog entry ${entry.id}: ${field} differs from the package marker`)
      }
    }
  }

  return {
    ok: errors.length === 0,
    integrations: packages.map((pkg) => pkg.packageJson.name ?? pkg.dir),
    errors,
  }
}

// Loads every integration package whose marker conforms (built entries required).
export async function checkRepositoryLoads(root: string): Promise<string[]> {
  const errors: string[] = []
  for (const pkg of findIntegrationPackages(root)) {
    if (!pkg.marker) continue
    errors.push(...(await checkPluginLoad(pkg)).errors)
  }
  return errors
}
