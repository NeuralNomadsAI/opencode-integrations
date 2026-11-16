// An upgrade without restarting OpenCode: the daemon loaded an older OpenCode for Gaffer, whose offer list lives
// on globalThis under the application's name alone, then this version in the same process. This version's list
// never reuses that one, which judges hellos by the older range and calls this version's host incompatible.

import assert from "node:assert/strict"
import { after, it } from "node:test"

import { startFakeHost } from "../../../packages/testing/src/index.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { createOfferRegistry, offerRegistryKey, offerRegistryName } from "../plugin/src/kit.ts"
import { loadPlugin } from "./helpers.ts"

const marker = readPackage().opencodeIntegration
const host = marker.host as NonNullable<typeof marker.host>
const expected = { protocol: host.protocol, range: host.range, appVersions: host.appVersions }
const store = globalThis as unknown as Record<symbol, unknown>
const keys = [offerRegistryKey("gaffer"), offerRegistryKey(offerRegistryName("gaffer", expected))]
after(() => {
  for (const key of keys) delete store[key]
})

const announce = (fake: Awaited<ReturnType<typeof startFakeHost>>, offerKey: string) => ({
  ...fake.endpoint,
  script_handle: fake.scriptHandle,
  label: "Gaffer",
  protocol: 1,
  offer_key: offerKey,
})

it("a plugin loaded after an older version in the same process judges its own host ok", async () => {
  const fake = await startFakeHost({ hostVersion: readPackage().version, appVersion: "1.7.1.0" })
  // What an older version created when it loaded: the kit's shared list under "gaffer", with its own range.
  const older = createOfferRegistry({ expected: { ...expected, range: "^0.1.0" } })
  store[offerRegistryKey("gaffer")] = { generation: 2, registry: older }
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
    appVersion: "1.7.1.0",
    methods: {
      context: () => {
        throw {
          code: "invalid_args",
          message: "SceneView renderer is not registered: Arnold",
          data: { reason: "unsupported_renderer", registered_renderers: ["OpenGL", "Cycles"] },
        }
      },
    },
  })
  const plugin = await loadPlugin()
  try {
    await plugin.rpc("advertise", announce(fake, "errors"))
    const offerID = plugin.registry.hosts().offers[0]?.offerID as string
    assert.deepEqual(await plugin.rpc("attachHost", { offerID, sessionID: "ses_test" }), { outcome: "attached" })
    await assert.rejects(plugin.tool("gaffer_context", { script_handle: fake.scriptHandle }), (error: Error) => {
      assert.match(error.message, /\(invalid_args\): SceneView renderer is not registered: Arnold/)
      assert.match(error.message, /"reason":"unsupported_renderer","registered_renderers":\["OpenGL","Cycles"\]/)
      return true
    })
  } finally {
    await plugin.cleanup()
    await fake.close()
  }
})
