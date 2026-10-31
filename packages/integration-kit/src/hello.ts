// Host handshake (spec/host-handshake.md, HST-3 and HST-25).

import { createHash } from "node:crypto"

import semver from "semver"

import { APP_VERSION_MAX, displayLabel, HANDLE, isDisplayText, LABEL_MAX } from "./contract.ts"

export type UndoLevel = "transactional" | "grouped_best_effort" | "none"

export type HostCapabilities = {
  methods: string[]
  operations: Array<{ name: string; undoable: boolean; network?: boolean }>
  undo: UndoLevel
  background: boolean
}

export type Hello = {
  protocol: number
  host_version: string
  app_version: string
  capabilities: HostCapabilities
  capability_hash: string
  script_handle: string
  revision: number
  document?: string
}

// Compatibility of a host with the marker's `host` block.
export type HostExpectation = { protocol: number; range: string; appVersions: string }
export type HostCompatibility = "ok" | "outdated" | "incompatible"

const NAME = /^[a-z][a-z0-9_.]{0,63}$/
const UNDO_LEVELS: UndoLevel[] = ["transactional", "grouped_best_effort", "none"]

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

// Canonical JSON: object keys sorted by code point, no whitespace. The Python
// reference (`json.dumps(value, sort_keys=True, separators=(",", ":"))`)
// produces the same bytes for the ASCII-only capability documents.
export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`
  if (isObject(value)) {
    const keys = Object.keys(value)
      .filter((key) => value[key] !== undefined)
      .sort()
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`
  }
  return JSON.stringify(value)
}

export function capabilityHash(capabilities: HostCapabilities): string {
  return createHash("sha256").update(canonicalJson(capabilities)).digest("hex")
}

function parseCapabilities(value: unknown): HostCapabilities | null {
  if (!isObject(value)) return null
  if (!Object.keys(value).every((key) => ["methods", "operations", "undo", "background"].includes(key))) return null
  const { methods, operations, undo, background } = value
  if (!Array.isArray(methods) || methods.length > 512) return null
  if (!methods.every((name) => typeof name === "string" && NAME.test(name))) return null
  if (new Set(methods).size !== methods.length) return null
  if (!Array.isArray(operations) || operations.length > 512) return null
  for (const operation of operations) {
    if (!isObject(operation)) return null
    if (!Object.keys(operation).every((key) => ["name", "undoable", "network"].includes(key))) return null
    if (typeof operation.name !== "string" || !NAME.test(operation.name)) return null
    if (typeof operation.undoable !== "boolean") return null
    if (operation.network !== undefined && typeof operation.network !== "boolean") return null
  }
  if (!UNDO_LEVELS.includes(undo as UndoLevel) || typeof background !== "boolean") return null
  return value as HostCapabilities
}

// Validates a hello result against spec/schemas/host.schema.json#/$defs/hello
// and checks its capability hash; null when either fails.
export function parseHello(value: unknown): Hello | null {
  if (!isObject(value)) return null
  const { protocol, host_version, app_version, capability_hash, script_handle, revision, document } = value
  if (typeof protocol !== "number" || !Number.isInteger(protocol) || protocol < 1 || protocol > 1000) return null
  if (typeof host_version !== "string" || host_version.length > 64 || semver.valid(host_version) !== host_version)
    return null
  if (!isDisplayText(app_version, APP_VERSION_MAX)) return null
  const capabilities = parseCapabilities(value.capabilities)
  if (!capabilities) return null
  if (typeof capability_hash !== "string" || capability_hash !== capabilityHash(capabilities)) return null
  if (typeof script_handle !== "string" || [...script_handle].length > 200 || !HANDLE.test(script_handle)) return null
  if (typeof revision !== "number" || !Number.isInteger(revision) || revision < 0) return null
  if (document !== undefined && !isDisplayText(document, LABEL_MAX)) return null
  return value as Hello
}

// Application versions such as Blender's "5.2.0 LTS Beta" compare by their
// leading version numbers.
export function appVersionSatisfies(appVersion: string, range: string): boolean {
  const version = semver.coerce(appVersion)
  return version !== null && semver.satisfies(version, range, { includePrerelease: true })
}

// ok, outdated (host part older than the range) or incompatible (another
// protocol, a newer host part, or an application outside `appVersions`).
export function evaluateHello(hello: Hello, expected: HostExpectation): HostCompatibility {
  if (hello.protocol !== expected.protocol) return "incompatible"
  if (!appVersionSatisfies(hello.app_version, expected.appVersions)) return "incompatible"
  return comparePartVersion(hello.host_version, expected.range)
}

// Prereleases count like releases: 0.1.1-beta.1 is inside ^0.1.0.
export function comparePartVersion(version: string, range: string): HostCompatibility {
  const options = { includePrerelease: true }
  if (semver.satisfies(version, range, options)) return "ok"
  return semver.ltr(version, range, options) ? "outdated" : "incompatible"
}

export function documentLabel(hello: Hello): string | undefined {
  return hello.document === undefined ? undefined : (displayLabel(hello.document) ?? undefined)
}
