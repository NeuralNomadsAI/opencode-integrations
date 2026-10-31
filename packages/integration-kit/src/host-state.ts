// Normative derivation of `status.host` (spec/host-handshake.md, HST-4 and HST-5).

import { readFileSync } from "node:fs"

import semver from "semver"

import { displayLabel, type HostState, type HostStatus } from "./contract.ts"
import { comparePartVersion, type HostCompatibility } from "./hello.ts"

// Result of the read-only inspection of the application's add-on folder.
export type HostInspection = { kind: "not-inspected" } | { kind: "not-found" } | { kind: "found"; version: string }

// `methods` is the hello's method table: a plugin offers a host-dependent tool only when a live host lists it.
export type LiveHello = {
  compatibility: HostCompatibility
  partVersion: string
  appVersion?: string
  methods?: string[]
}

export type HostStateInput = {
  unsupported: boolean
  // Hellos of validated offers received within LIVE_HELLO_MS.
  live: LiveHello[]
  inspection: HostInspection
  range: string
}

const SEVERITY: Record<HostCompatibility, number> = { ok: 0, outdated: 1, incompatible: 2 }

export function deriveHostState(input: HostStateInput): HostStatus {
  const expectedRange = input.range
  if (input.unsupported) return { state: "unsupported", expectedRange }

  if (input.live.length > 0) {
    const worst = input.live.reduce((a, b) => (SEVERITY[b.compatibility] > SEVERITY[a.compatibility] ? b : a))
    const status: HostStatus = { state: worst.compatibility, expectedRange, partVersion: worst.partVersion }
    const appVersion = displayLabel(worst.appVersion, 64)
    if (appVersion) status.appVersion = appVersion
    if (worst.compatibility === "outdated") status.action = "update"
    return status
  }

  if (input.inspection.kind === "not-found") return { state: "absent", expectedRange, action: "install" }
  if (input.inspection.kind === "found" && semver.valid(input.inspection.version)) {
    const partVersion = input.inspection.version
    const compatibility = comparePartVersion(partVersion, input.range)
    if (compatibility === "outdated") return { state: "outdated", expectedRange, partVersion, action: "update" }
    if (compatibility === "incompatible") return { state: "incompatible", expectedRange, partVersion }
    // An installed, matching part without a live hello: the application is closed.
    return { state: "unknown", expectedRange, partVersion }
  }
  return { state: "unknown", expectedRange }
}

export type WslProbe = { platform: NodeJS.Platform; env: NodeJS.ProcessEnv; procVersion: () => string | null }

const defaultProbe: WslProbe = {
  platform: process.platform,
  env: process.env,
  procVersion: () => {
    try {
      return readFileSync("/proc/version", "utf8")
    } catch {
      return null
    }
  },
}

// The daemon runs inside WSL: Linux, with a Microsoft kernel or WSL_DISTRO_NAME.
export function isWslDaemon(probe: WslProbe = defaultProbe): boolean {
  if (probe.platform !== "linux") return false
  if (probe.env.WSL_DISTRO_NAME) return true
  return /microsoft/i.test(probe.procVersion() ?? "")
}

// `unsupported` unless an offer was already validated by hello (mirrored networking).
export function hostUnsupported(options: { wsl: boolean; validatedOffer: boolean; platformSupported?: boolean }) {
  if (options.platformSupported === false) return true
  return options.wsl && !options.validatedOffer
}

export const HOST_STATES: HostState[] = ["absent", "incompatible", "outdated", "ok", "unknown", "unsupported"]
