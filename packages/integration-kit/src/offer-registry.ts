// Offer registry of a native integration (spec/host-handshake.md, HST-11 to
// HST-16). OpenCode instantiates a plugin per Location and evaluates
// a locally installed module once per Location, so the daemon-wide registry
// lives on globalThis under Symbol.for("neuralnomads.<app>.offers").

import { randomBytes, timingSafeEqual } from "node:crypto"

import {
  type AdvertiseOutcome,
  type AttachOutcome,
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

const defaultHello: HelloProbe = (endpoint) => requestHost(endpoint, "hello", {}, { timeoutMs: HELLO_TIMEOUT_MS })

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

  const emit = (...changes: OfferChange[]) => {
    for (const change of changes) for (const listener of listeners) listener(change)
  }

  const drop = (record: OfferRecord) => {
    offers.delete(record.key)
    for (const [sessionID, offerID] of sessions) if (offerID === record.offerID) sessions.delete(sessionID)
  }

  const sweep = () => {
    const at = now()
    let changed = false
    for (const record of [...offers.values()]) {
      if (record.expiresAt <= at) {
        drop(record)
        changed = true
      }
    }
    if (changed) emit("hosts", "status")
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

  const advertiseOnce = async (raw: unknown): Promise<{ outcome: AdvertiseOutcome }> => {
    const refused = { outcome: "refused" as const }
    const input = parseAdvertiseInput(raw)
    if (!input || input.protocol !== options.expected.protocol) return refused
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
        emit("hosts")
      }
      return { outcome: "renewed" }
    }
    if (!current && offers.size >= MAX_OFFERS) return refused

    const endpoint = { host: input.host, port: input.port, token: input.token }
    const hello = await verify(endpoint, input.script_handle)
    if (!hello) return refused
    // A replacement (new token, port or document) takes effect only after its hello.
    const previous = offers.get(input.offer_key)
    if (previous) drop(previous)
    if (!previous && offers.size >= MAX_OFFERS) return refused
    const document = documentLabel(hello)
    const appVersion = hello.app_version
    offers.set(input.offer_key, {
      offerID: newOfferID(),
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
    emit("hosts", "status")
    return { outcome: "accepted" }
  }

  const registry: OfferRegistry = {
    async advertise(raw) {
      // Serialize announcements of one offer key; other keys run concurrently.
      const key =
        typeof (raw as { offer_key?: unknown })?.offer_key === "string" ? (raw as { offer_key: string }).offer_key : ""
      const previous = inFlight.get(key) ?? Promise.resolve()
      const run = previous.then(
        () => advertiseOnce(raw),
        () => advertiseOnce(raw),
      )
      inFlight.set(key, run)
      try {
        return await run
      } finally {
        if (inFlight.get(key) === run) inFlight.delete(key)
      }
    },

    withdraw(raw) {
      const input = parseWithdrawInput(raw)
      const record = input ? offers.get(input.offer_key) : undefined
      if (!input || !record || !sameToken(record.endpoint.token, input.token)) return { outcome: "none" }
      drop(record)
      emit("hosts", "status")
      return { outcome: "withdrawn" }
    },

    hosts() {
      sweep()
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
      emit("status")
      return { outcome: "attached" }
    },

    detach(sessionID) {
      if (typeof sessionID !== "string" || !sessions.delete(sessionID)) return { outcome: "none" }
      emit("status")
      return { outcome: "detached" }
    },

    binding(sessionID) {
      sweep()
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
      return [...offers.values()].map((record) => ({
        compatibility: record.compatibility,
        partVersion: record.hello.host_version,
        appVersion: record.appVersion,
        methods: [...record.hello.capabilities.methods],
      }))
    },

    counts() {
      sweep()
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
const REGISTRY_GENERATION = 1

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
  const registry = createOfferRegistry(options)
  store[key] = { generation: REGISTRY_GENERATION, registry }
  return registry
}
