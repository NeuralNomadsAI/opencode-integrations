import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { capabilityHash } from "../src/hello.ts"
import {
  advanceBindingAuthority,
  createOfferRegistry,
  offerRegistryKey,
  sharedOfferRegistry,
} from "../src/offer-registry.ts"
import { createSessionBindings } from "../src/session-bindings.ts"

const expected = { protocol: 1, range: "^0.2.0", appVersions: ">=5.2" }
const capabilities = {
  methods: ["hello", "context", "apply"],
  operations: [],
  undo: "grouped_best_effort" as const,
  background: false,
}
const hello = {
  protocol: 1,
  host_version: "0.2.0",
  app_version: "5.2.0",
  capabilities,
  capability_hash: capabilityHash(capabilities),
  script_handle: "private-handle",
  revision: 0,
}
const offer = {
  host: "127.0.0.1",
  port: 43567,
  token: "a".repeat(64),
  script_handle: hello.script_handle,
  label: "Scene",
  protocol: 1,
  offer_key: "private-key",
}
const location = { directory: import.meta.dirname }
const own = { id: "ses_one", location }
function authorityOf(registry: ReturnType<typeof createOfferRegistry>) {
  const authority = registry.authority()
  assert.ok(authority)
  return authority
}
const deferred = <T>() => {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

async function fixture() {
  let at = 0
  const registry = createOfferRegistry({ expected, now: () => at, hello: async () => hello })
  assert.deepEqual(await registry.advertise(offer), { outcome: "accepted" })
  const offerID = registry.hosts().offers[0]?.offerID as string
  return {
    registry,
    offerID,
    expire: () => {
      at += 15_001
    },
  }
}

describe("binding authority", () => {
  it("advances before invalidation, is idempotent on unchanged renewals, and rolls before overflow", async () => {
    const { registry, offerID, expire } = await fixture()
    const advertised = authorityOf(registry)
    const observations: number[] = []
    registry.onChange(() => observations.push(authorityOf(registry).revision))
    registry.attach(offerID, "ses_one")
    const attached = authorityOf(registry)
    assert.equal(attached.revision, advertised.revision + 1)
    registry.attach(offerID, "ses_one")
    await registry.advertise(offer)
    assert.deepEqual(registry.authority(), attached)
    assert.deepEqual(observations, [attached.revision])
    await registry.advertise({ ...offer, label: "Renamed" })
    const renamed = authorityOf(registry)
    assert.equal(renamed.revision, attached.revision + 1)
    assert.deepEqual(observations, [attached.revision, renamed.revision, renamed.revision])
    assert.equal(registry.read("ses_one").state, "attached")
    expire()
    assert.deepEqual(registry.read("ses_one"), {
      state: "none",
      authority: { ...renamed, revision: renamed.revision + 1 },
    })
    const overflow = advanceBindingAuthority({ ...renamed, revision: Number.MAX_SAFE_INTEGER })
    assert.notEqual(overflow.registryGeneration, renamed.registryGeneration)
    assert.equal(overflow.serviceGeneration, renamed.serviceGeneration)
    assert.equal(overflow.revision, 0)
  })

  it("revocation, replacement and detach each invalidate bound data", async () => {
    const { registry, offerID } = await fixture()
    registry.attach(offerID, "ses_one")
    const first = authorityOf(registry)
    registry.detach("ses_one")
    const detached = authorityOf(registry)
    assert.equal(detached.revision, first.revision + 1)
    registry.detach("ses_one")
    assert.deepEqual(registry.authority(), detached)
    registry.attach(offerID, "ses_one")
    const before = authorityOf(registry)
    await registry.advertise({ ...offer, token: "b".repeat(64) })
    assert.equal(authorityOf(registry).revision, before.revision + 1)
    assert.equal(registry.read("ses_one").state, "none")
    const replacement = registry.hosts().offers[0]?.offerID
    assert.ok(replacement)
    assert.notEqual(replacement, offerID)
    registry.attach(replacement, "ses_one")
    const attached = authorityOf(registry)
    registry.withdraw({ offer_key: offer.offer_key, token: "b".repeat(64) })
    assert.equal(authorityOf(registry).revision, attached.revision + 1)
    assert.equal(registry.read("ses_one").state, "none")
  })

  it("retires captured registries on slot replacement and refuses unretireable legacy slots", async () => {
    const app = "binding-replacement-test"
    const store = globalThis as unknown as Record<
      symbol,
      { generation: number; registry: ReturnType<typeof createOfferRegistry> } | undefined
    >
    const key = offerRegistryKey(app)
    try {
      const old = sharedOfferRegistry(app, { expected, hello: async () => hello })
      await old.advertise(offer)
      const authority = authorityOf(old)
      const slot = store[key]
      assert.ok(slot)
      slot.generation = -1
      const next = sharedOfferRegistry(app, { expected, hello: async () => hello })
      assert.notEqual(old, next)
      assert.equal(old.authority(), null)
      assert.deepEqual(old.read("ses_one"), { state: "unknown" })
      assert.deepEqual(await old.advertise(offer), { outcome: "refused" })
      assert.notEqual(authorityOf(next).registryGeneration, authority.registryGeneration)
      assert.equal(authorityOf(next).serviceGeneration, authority.serviceGeneration)
      assert.deepEqual(next.hosts(), { offers: [] })
      store[key] = { generation: -1, registry: {} as never }
      assert.throws(() => sharedOfferRegistry(app, { expected }), /Unverifiable/)
    } finally {
      delete store[key]
    }
  })
})

describe("native snapshots and asynchronous fencing", () => {
  for (const method of ["read", "attach", "detach", "binding"] as const) {
    for (const mutation of ["label", "detach", "revoke", "replace", "expire", "retire"] as const) {
      it(`${method} refuses ${mutation} while native ownership is pending`, async () => {
        const { registry, offerID, expire } = await fixture()
        registry.attach(offerID, "ses_one")
        const pending = deferred<unknown>()
        const entered = deferred<void>()
        const bindings = createSessionBindings(registry, {
          location,
          get: () => {
            entered.resolve()
            return pending.promise
          },
        })
        const call =
          method === "read"
            ? bindings.read({ sessionID: "ses_one" })
            : method === "attach"
              ? bindings.attach(offerID, "ses_one")
              : bindings[method]("ses_one")
        const settled = call.then(
          (value) => ({ value }),
          (error: unknown) => ({ error }),
        )
        await entered.promise
        if (mutation === "label") await registry.advertise({ ...offer, label: "Changed" })
        if (mutation === "detach") registry.detach("ses_one")
        if (mutation === "revoke") registry.withdraw({ offer_key: offer.offer_key, token: offer.token })
        if (mutation === "replace") await registry.advertise({ ...offer, token: "b".repeat(64) })
        if (mutation === "expire") expire()
        if (mutation === "retire") registry.retire()
        pending.resolve(own)
        const result = await settled
        if (method === "read") assert.deepEqual(result, { value: { state: "unknown" } })
        else if (method === "attach") assert.deepEqual(result, { value: { outcome: "refused" } })
        else assert.match(String((result as { error: unknown }).error), /unverifiable|No verified/)
      })
    }
  }

  it("always performs fresh exact reads and never makes a late mutation after cancellation", async () => {
    const { registry, offerID } = await fixture()
    let calls = 0
    const pending = deferred<unknown>()
    const bindings = createSessionBindings(registry, {
      location,
      get: async () => {
        calls++
        return own
      },
    })
    assert.equal((await bindings.read({ sessionID: "ses_one" })).state, "none")
    assert.equal((await bindings.read({ sessionID: "ses_one" })).state, "none")
    assert.equal(calls, 2)
    const paused = createSessionBindings(registry, { location, get: () => pending.promise })
    const controller = new AbortController()
    const attach = paused.attach(offerID, "ses_one", controller.signal)
    controller.abort()
    assert.deepEqual(await attach, { outcome: "refused" })
    pending.resolve(own)
    await Promise.resolve()
    assert.equal(registry.read("ses_one").state, "none")
    const before = registry.authority()
    assert.deepEqual(await bindings.read({ sessionID: "ses_one", token: "sentinel" }), { state: "unknown" })
    assert.deepEqual(registry.authority(), before)
    assert.equal(calls, 2)
  })

  it("a timed-out mutation never commits when the native response eventually arrives", async () => {
    const { registry, offerID } = await fixture()
    const pending = deferred<unknown>()
    const bindings = createSessionBindings(registry, { location, get: () => pending.promise })
    assert.deepEqual(await bindings.attach(offerID, "ses_one"), { outcome: "refused" })
    pending.resolve(own)
    await Promise.resolve()
    assert.equal(registry.read("ses_one").state, "none")
  })

  for (const mutation of ["revoke", "queued-revoke", "retire", "expired-replacement", "revision"] as const) {
    it(`stale hello cannot insert an offer after ${mutation}`, async () => {
      const pending = deferred<unknown>()
      const entered = deferred<void>()
      let at = 0
      let wait = mutation !== "expired-replacement" && mutation !== "revision"
      const registry = createOfferRegistry({
        expected,
        now: () => at,
        hello: async () => {
          if (!wait) return hello
          entered.resolve()
          return pending.promise
        },
      })
      if (mutation === "expired-replacement" || mutation === "revision") {
        await registry.advertise(offer)
        wait = true
      }
      const advertisement = registry.advertise(
        mutation === "expired-replacement" || mutation === "revision" ? { ...offer, token: "b".repeat(64) } : offer,
      )
      const queued = mutation === "queued-revoke" ? registry.advertise(offer) : null
      await entered.promise
      if (mutation === "retire") registry.retire()
      else if (mutation === "expired-replacement") at = 15_001
      else if (mutation === "revision") registry.attach(registry.hosts().offers[0]?.offerID as string, "ses_one")
      else registry.withdraw({ offer_key: offer.offer_key, token: offer.token })
      pending.resolve(hello)
      assert.deepEqual(await advertisement, { outcome: "refused" })
      if (queued) assert.deepEqual(await queued, { outcome: "refused" })
      if (mutation === "revision") {
        assert.equal(registry.read("ses_one").state, "attached", "an unrelated binding change fences the pending hello")
      } else assert.deepEqual(registry.hosts(), { offers: [] })
    })
  }
})
