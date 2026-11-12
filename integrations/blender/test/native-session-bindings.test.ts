// Real OpenCode 2.2.5 Promise session.get ownership contract, no Blender installation or model calls.
// Run after build with OPENCODE_TEST_BIN; this service has private config, state and credentials.
import assert from "node:assert/strict"
import { mkdirSync } from "node:fs"
import { join } from "node:path"
import { it } from "node:test"

import { startFakeHost } from "../../../packages/conformance/src/index.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { isolatedRoot } from "./helpers/blender.ts"
import { nativeSkip, startPrivateService, until } from "./helpers/opencode.ts"
import { announcement } from "./helpers/plugin.ts"

it("an attachment survives a restart of the daemon and comes back when its Blender offers itself again", {
  skip: nativeSkip,
  timeout: 240_000,
}, async () => {
  const iso = isolatedRoot("blender-native-restart-")
  let service: Awaited<ReturnType<typeof startPrivateService>> | undefined
  // Two Blenders: only the stored attachment, never the automatic one, can explain the binding after the restart.
  const kept = await startFakeHost({ hostVersion: readPackage().version, appVersion: "5.3.0" })
  const other = await startFakeHost({ hostVersion: readPackage().version, appVersion: "5.3.0" })
  const offers = [
    { ...announcement(kept.endpoint, kept.scriptHandle, "offer-kept"), label: "Blender: kept.blend" },
    { ...announcement(other.endpoint, other.scriptHandle, "offer-other"), label: "Blender: other.blend" },
  ]
  try {
    service = await startPrivateService(iso)
    const location = join(iso.root, "location")
    mkdirSync(location)
    const session = (
      await service.request("POST", "/api/session", { location: { directory: location }, title: "restart" })
    ).data
    const advertise = async (running: NonNullable<typeof service>) => {
      for (const offer of offers) await running.rpc(running.globalConfig, "advertise", offer)
    }
    await advertise(service)
    const offerID = (await service.rpc(location, "hosts")).offers.find(
      (entry: { label: string }) => entry.label === "Blender: kept.blend",
    ).offerID
    assert.deepEqual(await service.rpc(location, "attachHost", { offerID, sessionID: session.id }), {
      outcome: "attached",
    })

    await service.close()
    service = await startPrivateService(iso)
    const running = service
    const read = () => running.rpc(location, "bindingRead", { sessionID: session.id })
    assert.equal((await read()).state, "none", "nothing is offered yet")
    // The add-ons offer their sessions again (every 5 s); the conversation is attached back without a client call.
    await advertise(running)
    const restored = await until("the restored attachment", 20_000, async () => {
      const binding = await read()
      return binding.state === "attached" ? binding : undefined
    })
    assert.equal(restored.binding.label, "Blender: kept.blend")
  } catch (error) {
    throw new Error(`${String(error)}\n--- private daemon\n${service?.logs() ?? "startup failed"}`)
  } finally {
    await service?.close()
    await kept.close()
    await other.close()
    iso.cleanup()
  }
})

it("native exact-session binding: two Locations, move, delete, revocation and service generations", {
  skip: nativeSkip,
  timeout: 240_000,
}, async () => {
  const iso = isolatedRoot("blender-native-binding-")
  let service: Awaited<ReturnType<typeof startPrivateService>> | undefined
  const host = await startFakeHost({ hostVersion: readPackage().version, appVersion: "5.3.0" })
  try {
    service = await startPrivateService(iso)
    const a = join(iso.root, "location-a")
    const b = join(iso.root, "location-b")
    mkdirSync(a)
    mkdirSync(b)
    const { request, rpc } = service
    const first = (await request("POST", "/api/session", { location: { directory: a }, title: "native binding A" }))
      .data
    const second = (await request("POST", "/api/session", { location: { directory: b }, title: "native binding B" }))
      .data
    assert.equal(first.location.directory, a)
    assert.equal(second.location.directory, b)
    const read = (directory: string, sessionID: string) => rpc(directory, "bindingRead", { sessionID })
    const none = await read(a, first.id)
    assert.equal(none.state, "none")
    assert.equal((await read(b, second.id)).state, "none")
    assert.deepEqual(await read(a, second.id), { state: "unknown" })
    assert.deepEqual(await read(b, first.id), { state: "unknown" })
    assert.deepEqual(await read(a, "ses_native_missing"), { state: "unknown" })

    const offer = announcement(host.endpoint, host.scriptHandle)
    assert.deepEqual(await rpc(a, "advertise", offer), { outcome: "accepted" })
    const offerID = (await rpc(b, "hosts")).offers[0].offerID
    assert.deepEqual(await rpc(a, "attachHost", { offerID, sessionID: second.id }), { outcome: "refused" })
    assert.deepEqual(await rpc(a, "attachHost", { offerID, sessionID: first.id }), { outcome: "attached" })
    const attached = await read(a, first.id)
    assert.equal(attached.state, "attached")
    assert.deepEqual(attached.binding, { offerID, label: offer.label })
    assert.equal(attached.authority.serviceGeneration, none.authority.serviceGeneration)
    assert.equal(attached.authority.registryGeneration, none.authority.registryGeneration)
    assert.ok(attached.authority.revision > none.authority.revision)
    assert.equal(
      (await read(b, second.id)).state,
      "none",
      "a daemon-wide attached count is not an exact-session binding",
    )

    await request("POST", `/api/session/${first.id}/move`, { directory: b })
    assert.equal((await request("GET", `/api/session/${first.id}`)).data.location.directory, b)
    assert.deepEqual(await read(a, first.id), { state: "unknown" })
    await assert.rejects(rpc(a, "detachHost", { sessionID: first.id }))
    assert.deepEqual(await read(b, first.id), attached, "move preserves session-ID binding without detach")
    assert.deepEqual(await rpc(b, "attachHost", { offerID, sessionID: second.id }), { outcome: "attached" })
    await request("DELETE", `/api/session/${first.id}`)
    assert.deepEqual(await read(b, first.id), { state: "unknown" })
    await assert.rejects(rpc(b, "detachHost", { sessionID: first.id }))

    const beforeLabel = await read(b, second.id)
    assert.deepEqual(await rpc(a, "advertise", { ...offer, label: "Blender: native renamed.blend" }), {
      outcome: "renewed",
    })
    const renamed = await read(b, second.id)
    assert.equal(renamed.binding.label, "Blender: native renamed.blend")
    assert.ok(renamed.authority.revision > beforeLabel.authority.revision)
    assert.deepEqual(await rpc(b, "detachHost", { sessionID: second.id }), { outcome: "detached" })
    assert.equal((await read(b, second.id)).state, "none")
    await rpc(b, "attachHost", { offerID, sessionID: second.id })
    await rpc(a, "withdraw", { offer_key: offer.offer_key, token: offer.token })
    const withdrawn = await read(b, second.id)
    assert.equal(withdrawn.state, "none")
    assert.ok(withdrawn.authority.revision > renamed.authority.revision)
    assert.ok(
      host.requests.every((entry) => entry.authorized),
      "hello uses the fake host's token",
    )

    // Restart only this owned child. Persisted native sessions survive; live registry bindings do not.
    await service.close()
    service = await startPrivateService(iso)
    const restarted = await service.rpc(b, "bindingRead", { sessionID: second.id })
    assert.equal(restarted.state, "none")
    assert.notEqual(restarted.authority.serviceGeneration, withdrawn.authority.serviceGeneration)
    assert.notEqual(restarted.authority.registryGeneration, withdrawn.authority.registryGeneration)
    assert.deepEqual(await service.rpc(b, "hosts"), { offers: [] })
  } catch (error) {
    throw new Error(`${String(error)}\n--- private daemon\n${service?.logs() ?? "startup failed"}`)
  } finally {
    await service?.close()
    await host.close()
    iso.cleanup()
  }
})
