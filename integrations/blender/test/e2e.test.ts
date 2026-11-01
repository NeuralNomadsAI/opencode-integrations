// End to end on an isolated OpenCode service (AC-B8, AC-B15): the built plugin loads from the global
// configuration's `plugins` list, a background Blender with "Offer this session to OpenCode" ticked
// discovers the service from the fixture's XDG_STATE_HOME and advertises, `hosts` lists the offer,
// `attachHost` binds a session, and unticking the consent removes the offer within the lease.
// Skipped unless OPENCODE_TEST_BIN (OpenCode 2.2.5) and BLENDER_TEST_BIN are set; run after `npm run build`.
// Never touches the shared daemon or the user's configuration: everything lives in a temporary root.

import assert from "node:assert/strict"
import { join } from "node:path"
import { describe, it } from "node:test"

import { readPackage } from "../plugin/src/integration.ts"
import { blenderBin, installAddon, isolatedRoot, runBlender } from "./helpers/blender.ts"
import { nativeSkip, startPrivateService, until } from "./helpers/opencode.ts"

const skip = nativeSkip || (!blenderBin ? "BLENDER_TEST_BIN is not set" : false)

describe("OpenCode 2.2.5 service with a background Blender", { skip }, () => {
  it("advertises, lists, attaches and withdraws an offer", async () => {
    const iso = isolatedRoot("blender-e2e-")
    let service: Awaited<ReturnType<typeof startPrivateService>> | undefined
    let blender: ReturnType<typeof runBlender> | undefined
    try {
      service = await startPrivateService(iso)
      const running = service
      const rpc = (method: string, input: unknown = {}) => running.rpc(running.globalConfig, method, input)
      const session = (
        await service.request("POST", "/api/session", {
          location: { directory: service.globalConfig },
          title: "Blender e2e",
        })
      ).data

      const status = await until("the plugin's status", 90_000, () => rpc("status"))
      assert.deepEqual(status.integration, { id: "blender", version: readPackage().version, family: "native", spec: 0 })
      assert.deepEqual(await rpc("hosts"), { offers: [] })
      assert.equal((await rpc("bindingRead", { sessionID: session.id })).state, "none")

      installAddon(iso)
      blender = runBlender(iso, join(import.meta.dirname, "blender", "serve.py"), [
        "--offer",
        "--seconds",
        "90",
        "--off-after",
        "25",
      ])
      const offers = await until("an offer", 30_000, async () => {
        const listed = (await rpc("hosts")) as { offers: Array<{ offerID: string; label: string; protocol: string }> }
        return listed.offers.length > 0 ? listed.offers : undefined
      })
      assert.equal(offers.length, 1)
      assert.equal(offers[0]?.label, "Blender: Untitled")
      assert.equal(offers[0]?.protocol, "ok")
      assert.deepEqual(await rpc("attachHost", { offerID: offers[0]?.offerID, sessionID: session.id }), {
        outcome: "attached",
      })
      const binding = await rpc("bindingRead", { sessionID: session.id })
      assert.equal(binding.state, "attached")
      assert.deepEqual(binding.binding, { offerID: offers[0]?.offerID, label: offers[0]?.label })
      const attached = await rpc("status")
      assert.deepEqual(attached.connection, { state: "attached", offers: 1, attachedSessions: 1 })
      assert.equal((attached.host as { state: string }).state, "ok")

      await until("the consent switch", 40_000, async () =>
        blender?.output().includes("CONSENT_OFF") ? true : undefined,
      )
      const off = Date.now()
      await until("the withdrawn offer", 16_000, async () => {
        const listed = (await rpc("hosts")) as { offers: unknown[] }
        return listed.offers.length === 0 ? true : undefined
      })
      assert.ok(Date.now() - off < 15_500, "gone within the lease")
      assert.deepEqual((await rpc("status")).connection, { state: "none", offers: 0, attachedSessions: 0 })
      assert.equal((await rpc("bindingRead", { sessionID: session.id })).state, "none")
    } catch (error) {
      throw new Error(
        `${(error as Error).message}\n--- blender\n${blender?.output().slice(-2000) ?? ""}\n--- daemon\n${service?.logs().slice(-3000) ?? ""}`,
      )
    } finally {
      blender?.child.kill()
      await blender?.exited
      await service?.close()
      iso.cleanup()
    }
  })
})
