// Offer registry of a native integration (spec/host-handshake.md, HST-11 to
// HST-16). OpenCode instantiates a plugin per Location and evaluates
// a locally installed module once per Location, so the daemon-wide registry
// lives on globalThis under Symbol.for("neuralnomads.<app>.offers").

import { randomBytes, timingSafeEqual } from "node:crypto"

import {
  type AdvertiseOutcome,
  type AttachOutcome,
  type BindingAuthority,
  type BindingReadOutput,
  HELLO_TIMEOUT_MS,
  type HostOffer,
  MAX_OFFERS,
  OFFER_LEASE_MS,
  parseAdvertiseInput,
  parseWithdrawInput,
  type WithdrawOutcome,
} from "./contract.ts"
import {
  documentLabel,
  evaluateHello,
  type Hello,
  type HostCompatibility,
  type HostExpectation,
  parseHello,
} from "./hello.ts"
import { type HostEndpoint, requestHost } from "./host-client.ts"
import type { LiveHello } from "./host-state.ts"

export type HelloProbe = (endpoint: HostEndpoint) => Promise<unknown>

export type OfferRegistryOptions = {
  expected: HostExpectation
  // Injectable clock and hello transport, for tests.
  now?: () => number
  hello?: HelloProbe
  newOfferID?: () => string
  isCurrent?: () => boolean
}

export type OfferChange = "hosts" | "status"

// What tools need to reach the attached host; never leaves the plugin. `methods` is its hello's method table.
export type HostBinding = { endpoint: HostEndpoint; scriptHandle: string; offerID: string; methods: string[] }

// The schemas registered with OpenCode carry no `pattern` (see rpcDefinition): the registry checks IDs itself.
const OFFER_ID = /^[A-Za-z0-9_-]{8,64}$/
const SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/
// The contract bounds attachedSessions to 10000.
export const MAX_SESSIONS = 10_000

export type OfferRegistry = {
  authority(): BindingAuthority | null
  current(authority: BindingAuthority): boolean
  read(sessionID: string): BindingReadOutput
  retire(): void
  advertise(input: unknown): Promise<{ outcome: AdvertiseOutcome }>
  withdraw(input: unknown): { outcome: WithdrawOutcome }
  hosts(): { offers: HostOffer[] }
  attach(offerID: string, sessionID: string): { outcome: AttachOutcome }
  detach(sessionID: string): { outcome: "detached" | "none" }
  binding(sessionID: string): HostBinding | null
  liveHellos(): LiveHello[]
  counts(): { offers: number; attachedSessions: number }
  sweep(): void
  onChange(listener: (change: OfferChange) => void): () => void
}

type OfferRecord = {
  offerID: string
  key: string
  endpoint: HostEndpoint
  scriptHandle: string
  label: string
  hello: Hello
  // Display fields of the hello, dropped when they mention a secret.
  document?: string
  appVersion?: string
  compatibility: HostCompatibility
  expiresAt: number
}
type PendingAnnouncement = { key: string; token: string; cancelled: boolean }

const defaultHello: HelloProbe = (endpoint) => requestHost(endpoint, "hello", {}, { timeoutMs: HELLO_TIMEOUT_MS })

const opaqueGeneration = () => randomBytes(24).toString("base64url")
const SERVICE_KEY = Symbol.for("neuralnomads.integrations.serviceGeneration")

function serviceGeneration(): string {
  const store = globalThis as unknown as Record<symbol, unknown>
  const existing = store[SERVICE_KEY]
  if (existing !== undefined && (typeof existing !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(existing))) {
    throw new Error("Unverifiable integration service generation.")
  }
  if (existing !== undefined) return existing as string
  const generation = opaqueGeneration()
  store[SERVICE_KEY] = generation
  return generation
}

// Rotate the instance before a safe integer could overflow.
export function advanceBindingAuthority(authority: BindingAuthority): BindingAuthority {
  return authority.revision === Number.MAX_SAFE_INTEGER
    ? { ...authority, registryGeneration: opaqueGeneration(), revision: 0 }
    : { ...authority, revision: authority.revision + 1 }
}

// Whether display text reveals the token, the offer key, the script handle or the port.
function revealsSecret(text: string, input: { token: string; offer_key: string; script_handle: string; port: number }) {
  if ([input.token, input.offer_key, input.script_handle].some((secret) => text.includes(secret))) return true
  return new RegExp(`(^|\\D)${input.port}(\\D|$)`).test(text)
}

function sameToken(a: string, b: string) {
  const left = Buffer.from(a)
  const right = Buffer.from(b)
  return left.length === right.length && timingSafeEqual(left, right)
}

export function createOfferRegistry(options: OfferRegistryOptions): OfferRegistry {
  const now = options.now ?? Date.now
  const probe = options.hello ?? defaultHello
  const newOfferID = options.newOfferID ?? (() => `offer_${randomBytes(16).toString("base64url")}`)
  const offers = new Map<string, OfferRecord>()
  const sessions = new Map<string, string>()
  const listeners = new Set<(change: OfferChange) => void>()
  const inFlight = new Map<string, Promise<unknown>>()
  const announcements = new Set<PendingAnnouncement>()
  let retired = false
  let authority: BindingAuthority = {
    serviceGeneration: serviceGeneration(),
    registryGeneration: opaqueGeneration(),
    revision: 0,
  }
  const active = () =>
    !retired &&
    (globalThis as unknown as Record<symbol, unknown>)[SERVICE_KEY] === authority.serviceGeneration &&
    (options.isCurrent?.() ?? true)

  const emit = (...changes: OfferChange[]) => {
    for (const change of changes) for (const listener of listeners) listener(change)
  }
  const changed = (...changes: OfferChange[]) => {
    authority = advanceBindingAuthority(authority)
    emit(...changes)
  }

  const drop = (record: OfferRecord) => {
    offers.delete(record.key)
    for (const [sessionID, offerID] of sessions) if (offerID === record.offerID) sessions.delete(sessionID)
  }

  const sweep = () => {
    if (!active()) return
    const at = now()
    let changed = false
    for (const record of [...offers.values()]) {
      if (record.expiresAt <= at) {
        drop(record)
        changed = true
      }
    }
    if (changed) {
      authority = advanceBindingAuthority(authority)
      emit("hosts", "status")
    }
  }

  const verify = async (endpoint: HostEndpoint, scriptHandle: string): Promise<Hello | null> => {
    let result: unknown
    try {
      result = await probe(endpoint)
    } catch {
      return null
    }
    const hello = parseHello(result)
    if (!hello || hello.protocol !== options.expected.protocol || hello.script_handle !== scriptHandle) return null
    return hello
  }

  const advertiseOnce = async (raw: unknown, pending: PendingAnnouncement): Promise<{ outcome: AdvertiseOutcome }> => {
    const refused = { outcome: "refused" as const }
    const input = parseAdvertiseInput(raw)
    if (!active() || pending.cancelled || !input || input.protocol !== options.expected.protocol) return refused
    if (revealsSecret(input.label, input)) return refused
    sweep()
    const current = offers.get(input.offer_key)
    if (
      current &&
      sameToken(current.endpoint.token, input.token) &&
      current.endpoint.port === input.port &&
      current.scriptHandle === input.script_handle
    ) {
      current.expiresAt = now() + OFFER_LEASE_MS
      if (current.label !== input.label) {
        current.label = input.label
        changed("hosts", "status")
      }
      return { outcome: "renewed" }
    }
    if (!current && offers.size >= MAX_OFFERS) return refused

    const endpoint = { host: input.host, port: input.port, token: input.token }
    const capturedAuthority = { ...authority }
    const hello = await verify(endpoint, input.script_handle)
    sweep()
    if (!hello || !registry.current(capturedAuthority) || pending.cancelled || offers.get(input.offer_key) !== current)
      return refused
    // A replacement (new token, port or document) takes effect only after its hello.
    const previous = offers.get(input.offer_key)
    if (!previous && offers.size >= MAX_OFFERS) return refused
    const document = documentLabel(hello)
    const appVersion = hello.app_version
    const offerID = newOfferID()
    if (!OFFER_ID.test(offerID) || [...offers.values()].some((record) => record.offerID === offerID)) return refused
    if (previous) drop(previous)
    offers.set(input.offer_key, {
      offerID,
      key: input.offer_key,
      endpoint,
      scriptHandle: input.script_handle,
      label: input.label,
      hello,
      document: document && !revealsSecret(document, input) ? document : undefined,
      appVersion: revealsSecret(appVersion, input) ? undefined : appVersion,
      compatibility: evaluateHello(hello, options.expected),
      expiresAt: now() + OFFER_LEASE_MS,
    })
    changed("hosts", "status")
    return { outcome: "accepted" }
  }

  const registry: OfferRegistry = {
    authority() {
      sweep()
      return active() ? { ...authority } : null
    },
    current(expected) {
      const current = registry.authority()
      return (
        current !== null &&
        current.serviceGeneration === expected.serviceGeneration &&
        current.registryGeneration === expected.registryGeneration &&
        current.revision === expected.revision
      )
    },
    read(sessionID) {
      const current = registry.authority()
      if (!current || !SESSION_ID.test(sessionID)) return { state: "unknown" }
      const offerID = sessions.get(sessionID)
      const record = [...offers.values()].find((candidate) => candidate.offerID === offerID)
      return record
        ? { state: "attached", authority: current, binding: { offerID: record.offerID, label: record.label } }
        : { state: "none", authority: current }
    },
    retire() {
      if (retired) return
      retired = true
      offers.clear()
      sessions.clear()
      changed("hosts", "status")
    },
    async advertise(raw) {
      const input = parseAdvertiseInput(raw)
      if (!input || !active()) return { outcome: "refused" }
      // Serialize announcements of one offer key; other keys run concurrently.
      const key = input.offer_key
      const pending = { key, token: input.token, cancelled: false }
      announcements.add(pending)
      const previous = inFlight.get(key) ?? Promise.resolve()
      const run = previous.then(
        () => advertiseOnce(input, pending),
        () => advertiseOnce(input, pending),
      )
      inFlight.set(key, run)
      try {
        return await run
      } finally {
        announcements.delete(pending)
        if (inFlight.get(key) === run) inFlight.delete(key)
      }
    },

    withdraw(raw) {
      if (!active()) return { outcome: "none" }
      const input = parseWithdrawInput(raw)
      if (input)
        for (const pending of announcements) {
          if (pending.key === input.offer_key && sameToken(pending.token, input.token)) pending.cancelled = true
        }
      const record = input ? offers.get(input.offer_key) : undefined
      if (!input || !record || !sameToken(record.endpoint.token, input.token)) return { outcome: "none" }
      drop(record)
      changed("hosts", "status")
      return { outcome: "withdrawn" }
    },

    hosts() {
      sweep()
      if (!active()) return { offers: [] }
      return {
        offers: [...offers.values()].map((record) => {
          const offer: HostOffer = {
            offerID: record.offerID,
            label: record.label,
            protocol: record.compatibility === "incompatible" ? "incompatible" : "ok",
          }
          if (record.document) offer.document = record.document
          if (record.appVersion) offer.appVersion = record.appVersion
          return offer
        }),
      }
    },

    attach(offerID, sessionID) {
      if (!active()) return { outcome: "refused" }
      if (typeof sessionID !== "string" || !SESSION_ID.test(sessionID)) return { outcome: "refused" }
      if (typeof offerID !== "string" || !OFFER_ID.test(offerID)) return { outcome: "gone" }
      sweep()
      const record = [...offers.values()].find((candidate) => candidate.offerID === offerID)
      if (!record) return { outcome: "gone" }
      if (record.compatibility === "incompatible") return { outcome: "refused" }
      const bound = sessions.get(sessionID)
      if (bound === offerID) return { outcome: "attached" }
      if (bound !== undefined) return { outcome: "busy" }
      if (sessions.size >= MAX_SESSIONS) return { outcome: "refused" }
      sessions.set(sessionID, offerID)
      changed("status")
      return { outcome: "attached" }
    },

    detach(sessionID) {
      if (!active()) return { outcome: "none" }
      if (typeof sessionID !== "string" || !sessions.delete(sessionID)) return { outcome: "none" }
      changed("status")
      return { outcome: "detached" }
    },

    binding(sessionID) {
      sweep()
      if (!active()) return null
      const offerID = sessions.get(sessionID)
      const record = [...offers.values()].find((candidate) => candidate.offerID === offerID)
      return record
        ? {
            endpoint: { ...record.endpoint },
            scriptHandle: record.scriptHandle,
            offerID: record.offerID,
            methods: [...record.hello.capabilities.methods],
          }
        : null
    },

    liveHellos() {
      sweep()
      if (!active()) return []
      return [...offers.values()].map((record) => ({
        compatibility: record.compatibility,
        partVersion: record.hello.host_version,
        appVersion: record.appVersion,
        methods: [...record.hello.capabilities.methods],
      }))
    },

    counts() {
      sweep()
      if (!active()) return { offers: 0, attachedSessions: 0 }
      return { offers: offers.size, attachedSessions: sessions.size }
    },

    sweep,

    onChange(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
  return registry
}

// Bumped when the registry's shape changes; a newer generation replaces an
// older one, whose offers come back with the next renewal (OFFER_RENEW_MS).
const REGISTRY_GENERATION = 2

type RegistrySlot = { generation: number; registry: OfferRegistry }

export function offerRegistryKey(app: string) {
  return Symbol.for(`neuralnomads.${app}.offers`)
}

// The registry shared by every Location of the daemon process.
export function sharedOfferRegistry(app: string, options: OfferRegistryOptions): OfferRegistry {
  const key = offerRegistryKey(app)
  const store = globalThis as unknown as Record<symbol, RegistrySlot | undefined>
  const slot = store[key]
  if (slot?.generation === REGISTRY_GENERATION) return slot.registry
  // A captured legacy object must be explicitly retireable before replacement.
  if (slot && typeof slot.registry.retire !== "function") throw new Error("Unverifiable integration registry.")
  slot?.registry.retire()
  const registry = createOfferRegistry({ ...options, isCurrent: () => store[key]?.registry === registry })
  store[key] = { generation: REGISTRY_GENERATION, registry }
  return registry
}
