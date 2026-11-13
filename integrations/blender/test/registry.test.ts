// An upgrade without restarting OpenCode: the daemon loaded OpenCode for Blender 0.2.0, whose offer list lives
// on globalThis under the application's name, then this version in the same process. This version's list never
// reuses that one, which judges hellos by 0.2.0's range and called this add-on incompatible.

import assert from "node:assert/strict"
import { after, it } from "node:test"

import { startFakeHost } from "../../../packages/testing/src/index.ts"
import { createRegistry, readPackage, registryName } from "../plugin/src/integration.ts"
import { offerRegistryKey, sharedOfferRegistry } from "../plugin/src/kit.ts"
import { readOptions } from "../plugin/src/options.ts"
import { announcement } from "./helpers/plugin.ts"

const marker = readPackage().opencodeIntegration
const host = marker.host as NonNullable<typeof marker.host>
const expected = { protocol: host.protocol, range: host.range, appVersions: host.appVersions }
const store = globalThis as unknown as Record<symbol, unknown>
const keys = [offerRegistryKey("blender"), offerRegistryKey(registryName(expected))]
after(() => {
  for (const key of keys) delete store[key]
})

it("a plugin loaded after an older version in the same process judges its own add-on ok", async () => {
  const fake = await startFakeHost({ hostVersion: readPackage().version, appVersion: "5.3.0" })
  try {
    // What 0.2.0 created when it loaded: the kit's shared list under "blender", with its own expectation.
    const older = sharedOfferRegistry("blender", { expected: { protocol: 1, range: "^0.2.0", appVersions: ">=5.2" } })
    const registry = createRegistry(marker, readOptions(undefined, {}))
    assert.notEqual(registry, older, "this version keeps its own list")
    assert.deepEqual(await registry.advertise(announcement(fake.endpoint, fake.scriptHandle)), { outcome: "accepted" })
    assert.equal(registry.hosts().offers[0]?.protocol, "ok")
    assert.deepEqual(
      registry.liveHellos().map((hello) => hello.compatibility),
      ["ok"],
    )
    // The older list, which this version used to reuse, judges the same add-on incompatible.
    await older.advertise(announcement(fake.endpoint, fake.scriptHandle))
    assert.equal(older.hosts().offers[0]?.protocol, "incompatible")
    assert.equal(createRegistry(marker, readOptions(undefined, {})), registry, "one list per contract and process")
  } finally {
    await fake.close()
  }
})
