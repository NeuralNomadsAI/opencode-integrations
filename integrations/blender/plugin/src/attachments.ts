// Attachments that survive an OpenCode restart. The offer registry lives in memory (HST-12): a restarted daemon
// forgets which conversation used which Blender, while the Blender keeps offering itself under the same offer key
// for its whole process life. Each Location's plugin keeps the attachments of its own conversations in its storage
// (`attachment/<sessionID>`, the offer key only as a SHA-256 digest, never a token, port or script handle) and, when
// the same Blender offers itself again, attaches the conversation back after the exact-session ownership check of
// RPC-12. A stored attachment lives while its Blender is offered and LEASE_MS after it stops; an explicit detach
// removes it. Restoring happens when the plugin loads and when offers change, never inside a read (RPC-13).

import { createHash } from "node:crypto"

import type { BindingAuthority, OfferRegistry } from "./kit.ts"

export const LEASE_MS = 120_000
const PERSIST_EVERY_MS = 30_000
const PREFIX = "attachment/"
const PAGES = 100
const SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/
const HOST = /^[0-9a-f]{64}$/

export type AttachmentStorage = {
  get(key: string): Promise<unknown>
  set(key: string, value: never): Promise<void>
  remove(key: string): Promise<void>
  scan(options: { prefix: string; after?: string; limit?: number }): Promise<{
    entries: readonly { key: string; value: unknown }[]
    next?: string
  }>
}

type Stored = { session: string; host: string; label: string; since: number; seen: number }

// Per daemon process, per registry: the digest of each live offer's key, and the conversations attachments were
// recorded for (all Locations), so that a list can say which conversation holds which Blender.
type Shared = {
  hosts: WeakMap<OfferRegistry, Map<string, string>>
  sessions: Set<string>
  advertising: Promise<unknown>
}
const SHARED = Symbol.for("neuralnomads.blender.attachments")
const store = globalThis as unknown as Record<symbol, Shared | undefined>
store[SHARED] ??= { hosts: new WeakMap(), sessions: new Set(), advertising: Promise.resolve() }
const shared = store[SHARED]

export function hostDigest(offerKey: string) {
  return createHash("sha256").update(`neuralnomads.blender.offer\0${offerKey}`).digest("hex")
}

function digests(registry: OfferRegistry) {
  let map = shared.hosts.get(registry)
  if (!map) {
    map = new Map()
    shared.hosts.set(registry, map)
  }
  return map
}

function valid(value: unknown): value is Stored {
  const entry = value as Stored
  return (
    typeof entry === "object" &&
    entry !== null &&
    typeof entry.session === "string" &&
    SESSION_ID.test(entry.session) &&
    typeof entry.host === "string" &&
    HOST.test(entry.host) &&
    typeof entry.label === "string" &&
    entry.label.length <= 120 &&
    Number.isFinite(entry.since)
  )
}

// The advertise handler, serialized per daemon process: the offer an accepted announcement created is the one
// that appeared during the call, so its key's digest names it (the registry keeps keys private).
export function trackAdvertise(
  registry: OfferRegistry,
  advertise: (input: unknown) => Promise<unknown>,
): (input: unknown) => Promise<unknown> {
  return (input) => {
    const run = shared.advertising.then(async () => {
      const before = new Set(registry.hosts().offers.map((offer) => offer.offerID))
      const result = (await advertise(input)) as { outcome?: string }
      const key = (input as { offer_key?: unknown } | null)?.offer_key
      if (result?.outcome === "accepted" && typeof key === "string") {
        const created = registry.hosts().offers.filter((offer) => !before.has(offer.offerID))
        if (created.length === 1 && created[0]) digests(registry).set(hostDigest(key), created[0].offerID)
      }
      return result
    })
    shared.advertising = run.catch(() => {})
    return run
  }
}

export type Attachments = ReturnType<typeof createAttachments>

export function createAttachments(options: {
  registry: OfferRegistry
  storage?: AttachmentStorage
  now?: () => number
  // A fresh ownership snapshot of the session at this Location (null when it is not verifiably ours).
  owns: (sessionID: string) => Promise<BindingAuthority | null>
}) {
  const { registry, storage } = options
  const now = options.now ?? Date.now
  const records = new Map<string, Stored>()
  const persisted = new Map<string, number>()

  const live = () => new Map(registry.hosts().offers.map((offer) => [offer.offerID, offer]))
  const offerOf = (host: string) => digests(registry).get(host)
  const hostOf = (offerID: string) => [...digests(registry)].find(([, id]) => id === offerID)?.[0]

  // Storage failures never fail a Blender call: the attachment then lasts until OpenCode stops.
  const write = async (record: Stored) => {
    persisted.set(record.session, now())
    try {
      await storage?.set(PREFIX + record.session, record as never)
    } catch {}
  }

  const forget = async (sessionID: string) => {
    if (!records.delete(sessionID)) return
    persisted.delete(sessionID)
    try {
      await storage?.remove(PREFIX + sessionID)
    } catch {}
  }

  return {
    // The stored attachments, once; their lease starts again now.
    async load() {
      if (!storage) return
      let after: string | undefined
      for (let page = 0; page < PAGES; page += 1) {
        let result: Awaited<ReturnType<AttachmentStorage["scan"]>> | undefined
        try {
          result = await storage.scan({ prefix: PREFIX, ...(after ? { after } : {}), limit: 100 })
        } catch {}
        const entries = Array.isArray(result?.entries) ? result.entries : []
        for (const entry of entries) {
          if (!valid(entry?.value) || entry.key !== PREFIX + entry.value.session) continue
          records.set(entry.value.session, { ...entry.value, seen: now() })
          shared.sessions.add(entry.value.session)
        }
        if (typeof result?.next !== "string" || entries.length === 0) break
        after = result.next
      }
    },

    // The conversation is attached now (tool, automatic or RPC attachment): keep it.
    async remember(sessionID: string) {
      const bound = registry.binding(sessionID)
      const host = bound && hostOf(bound.offerID)
      const offer = bound && live().get(bound.offerID)
      if (!bound || !host || !offer) return
      shared.sessions.add(sessionID)
      const previous = records.get(sessionID)
      const record = {
        session: sessionID,
        host,
        label: offer.label,
        since: previous?.host === host ? previous.since : now(),
        seen: now(),
      }
      records.set(sessionID, record)
      await write(record)
    },

    forget,

    // Attaches back each stored conversation (or only `sessionID`) whose Blender is offered again.
    async restore(sessionID?: string) {
      const offers = live()
      for (const record of [...records.values()]) {
        if (sessionID !== undefined && record.session !== sessionID) continue
        if (registry.binding(record.session)) continue
        const offerID = offerOf(record.host)
        if (!offerID || offers.get(offerID)?.protocol !== "ok") continue
        const authority = await options.owns(record.session)
        // Synchronous after the snapshot (RPC-12): the same registry state, still stored (a detach may have
        // forgotten it meanwhile), still no binding, the offer still live.
        if (!authority || !registry.current(authority) || records.get(record.session) !== record) continue
        if (registry.binding(record.session)) continue
        if (!live().has(offerID)) continue
        if (registry.attach(offerID, record.session).outcome === "attached") record.seen = now()
      }
    },

    // Renews the lease of the stored attachments whose Blender is offered; forgets those gone longer than it.
    async sweep() {
      const offers = live()
      for (const record of [...records.values()]) {
        const offerID = offerOf(record.host)
        const offer = offerID ? offers.get(offerID) : undefined
        if (offer) {
          record.seen = now()
          record.label = offer.label
          if (now() - (persisted.get(record.session) ?? 0) > PERSIST_EVERY_MS) await write(record)
        } else if (now() - record.seen > LEASE_MS) await forget(record.session)
      }
    },

    // Conversations other than `except` attached to the offer (those this daemon recorded, all Locations).
    heldBy(offerID: string, except?: string) {
      return [...shared.sessions].some(
        (session) => session !== except && registry.binding(session)?.offerID === offerID,
      )
    },

    has(sessionID: string) {
      return records.has(sessionID)
    },
  }
}

export function resetAttachmentsForTests() {
  shared.sessions.clear()
  shared.advertising = Promise.resolve()
}
