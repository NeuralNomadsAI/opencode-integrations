// An upgrade without restarting OpenCode: the daemon loaded an older OpenCode for Godot, then this version in the same
// process. This version's offer list, named after its host contract, never reuses the older one, which judges hellos
// by the older range.

import assert from "node:assert/strict"
import { after, it } from "node:test"

import {
  createOfferRegistry,
  offerRegistryKey,
  offerRegistryName,
} from "../../../packages/integration-kit/src/index.ts"
import { startFakeHost } from "../../../packages/testing/src/index.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { announcement, loadPlugin } from "./helpers.ts"

const marker = readPackage().opencodeIntegration
const host = marker.host as NonNullable<typeof marker.host>
const expected = { protocol: host.protocol, range: host.range, appVersions: host.appVersions }
const store = globalThis as unknown as Record<symbol, unknown>
const older = { ...expected, range: "^0.0.1" }
const keys = [
  offerRegistryKey(offerRegistryName("godot", older)),
  offerRegistryKey(offerRegistryName("godot", expected)),
]
after(() => {
  for (const key of keys) delete store[key]
})

it("a plugin loaded after an older version in the same process judges its own host ok", async () => {
  const fake = await startFakeHost({ hostVersion: readPackage().version, appVersion: "4.7.2-stable (official)" })
  const previous = createOfferRegistry({ expected: older })
  store[offerRegistryKey(offerRegistryName("godot", older))] = { generation: 2, registry: previous }
  const plugin = await loadPlugin({ shared: true })
  try {
    assert.notEqual(plugin.registry, previous, "this version keeps its own list")
    assert.deepEqual(await plugin.rpc("advertise", announcement(fake.endpoint, fake.scriptHandle, "after")), {
      outcome: "accepted",
    })
    assert.deepEqual(
      plugin.registry.hosts().offers.map((offer) => offer.protocol),
      ["ok"],
    )
    await previous.advertise(announcement(fake.endpoint, fake.scriptHandle, "after"))
    assert.equal(previous.hosts().offers[0]?.protocol, "incompatible")
  } finally {
    await plugin.cleanup()
    await fake.close()
  }
})

it("an editor of a Godot older than 4.7 is incompatible", async () => {
  const fake = await startFakeHost({ hostVersion: readPackage().version, appVersion: "4.5.2-stable (official)" })
  const plugin = await loadPlugin()
  try {
    await plugin.rpc("advertise", announcement(fake.endpoint, fake.scriptHandle))
    assert.equal(plugin.registry.hosts().offers[0]?.protocol, "incompatible")
    assert.equal(((await plugin.rpc("status")) as { host: { state: string } }).host.state, "incompatible")
  } finally {
    await plugin.cleanup()
    await fake.close()
  }
})
