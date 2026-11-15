// An upgrade without restarting OpenCode: the daemon loaded an older OpenCode for Houdini, whose offer list lives
// on globalThis under the application's name alone, then this version in the same process. This version's list
// never reuses that one, which judges hellos by the older range and calls this version's host incompatible.

import assert from "node:assert/strict"
import { after, it } from "node:test"

import {
  createOfferRegistry,
  offerRegistryKey,
  offerRegistryName,
} from "../../../packages/integration-kit/src/index.ts"
import { startFakeHost } from "../../../packages/testing/src/index.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { loadPlugin } from "./helpers.ts"

const marker = readPackage().opencodeIntegration
const host = marker.host as NonNullable<typeof marker.host>
const expected = { protocol: host.protocol, range: host.range, appVersions: host.appVersions }
const store = globalThis as unknown as Record<symbol, unknown>
const keys = [offerRegistryKey("houdini"), offerRegistryKey(offerRegistryName("houdini", expected))]
after(() => {
  for (const key of keys) delete store[key]
})

const announce = (fake: Awaited<ReturnType<typeof startFakeHost>>, offerKey: string) => ({
  ...fake.endpoint,
  script_handle: fake.scriptHandle,
  label: "Houdini",
  protocol: 1,
  offer_key: offerKey,
})

it("a plugin loaded after an older version in the same process judges its own host ok", async () => {
  const fake = await startFakeHost({ hostVersion: readPackage().version, appVersion: "22.0.429" })
  // What an older version created when it loaded: the kit's shared list under "houdini", with its own range.
  const older = createOfferRegistry({ expected: { ...expected, range: "^0.1.0" } })
  store[offerRegistryKey("houdini")] = { generation: 2, registry: older }
  const plugin = await loadPlugin({ shared: true })
  try {
    assert.notEqual(plugin.registry, older, "this version keeps its own list")
    assert.deepEqual(await plugin.rpc("advertise", announce(fake, "after-upgrade")), { outcome: "accepted" })
    assert.deepEqual(
      plugin.registry.hosts().offers.map((offer) => offer.protocol),
      ["ok"],
    )
    await older.advertise(announce(fake, "after-upgrade"))
    assert.equal(older.hosts().offers[0]?.protocol, "incompatible")
  } finally {
    await plugin.cleanup()
    await fake.close()
  }
})

it("a tool's error carries the host's own message and data", async () => {
  const fake = await startFakeHost({
    hostVersion: readPackage().version,
    appVersion: "22.0.429",
    methods: {
      context: () => {
        throw {
          code: "stale_revision",
          message: "The Houdini graph changed after the intent was prepared: /obj/geo1",
          data: { reason: "revision_conflict", expected_revision: 3, current_revision: 5 },
        }
      },
    },
  })
  const plugin = await loadPlugin()
  try {
    await plugin.rpc("advertise", announce(fake, "errors"))
    const offerID = plugin.registry.hosts().offers[0]?.offerID as string
    assert.deepEqual(await plugin.rpc("attachHost", { offerID, sessionID: "ses_test" }), { outcome: "attached" })
    await assert.rejects(plugin.tool("context", { script_handle: fake.scriptHandle }), (error: Error) => {
      assert.match(
        error.message,
        /\(stale_revision\): The Houdini graph changed after the intent was prepared: \/obj\/geo1/,
      )
      assert.match(error.message, /"reason":"revision_conflict","expected_revision":3,"current_revision":5/)
      return true
    })
  } finally {
    await plugin.cleanup()
    await fake.close()
  }
})
