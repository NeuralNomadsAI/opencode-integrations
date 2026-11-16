// An upgrade without restarting OpenCode: the daemon loaded an older OpenCode for Gaffer, whose offer list lives
// on globalThis under the application's name alone, then this version in the same process. This version's list,
// named after its host contract, never reuses that one, which judges hellos by the older range and calls this
// version's host incompatible.

import assert from "node:assert/strict"
import { randomBytes } from "node:crypto"
import { type AddressInfo, createServer } from "node:net"
import { after, it } from "node:test"

import {
  createOfferRegistry,
  offerRegistryKey,
  offerRegistryName,
} from "../../../packages/integration-kit/src/index.ts"
import { capabilityHash, startFakeHost } from "../../../packages/testing/src/index.ts"
import { createRegistry, readPackage } from "../plugin/src/integration.ts"
import { loadPlugin } from "./helpers.ts"

const marker = readPackage().opencodeIntegration
const host = marker.host as NonNullable<typeof marker.host>
const expected = { protocol: host.protocol, range: host.range, appVersions: host.appVersions }
const store = globalThis as unknown as Record<symbol, unknown>
const keys = [offerRegistryKey("gaffer"), offerRegistryKey(offerRegistryName("gaffer", expected))]
after(() => {
  for (const key of keys) delete store[key]
})

const announce = (
  fake: Awaited<ReturnType<typeof startFakeHost>>,
  offerKey: string,
  scriptHandle = fake.scriptHandle,
) => ({
  ...fake.endpoint,
  script_handle: scriptHandle,
  label: "Gaffer: shot.gfr",
  protocol: 1,
  offer_key: offerKey,
})

it("a plugin loaded after an older version in the same process judges its own host ok", async () => {
  const fake = await startFakeHost({ hostVersion: readPackage().version, appVersion: "1.6.15.0" })
  // What an older version created when it loaded: the kit's shared list under "gaffer", with its own range.
  const older = createOfferRegistry({ expected: { ...expected, range: "^0.2.0" } })
  store[offerRegistryKey("gaffer")] = { generation: 2, registry: older }
  const plugin = await loadPlugin({ shared: true })
  try {
    assert.notEqual(plugin.registry, older, "this version keeps its own list")
    assert.equal(
      (store[offerRegistryKey(offerRegistryName("gaffer", expected))] as { registry?: unknown })?.registry,
      plugin.registry,
      "named after this version's contract",
    )
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

// One Gaffer, one listener, several scripts: hello answers the script it is asked for (as Host.quickHello).
async function gafferWithScripts(scripts: string[]) {
  const token = randomBytes(32).toString("hex")
  const capabilities = {
    methods: ["hello", "context", "apply"],
    operations: [],
    undo: "transactional",
    background: false,
  }
  const asked: unknown[] = []
  const server = createServer((socket) => {
    socket.setEncoding("utf8")
    let buffer = ""
    socket.on("data", (chunk: string) => {
      buffer += chunk
      if (!buffer.includes("\n")) return
      const request = JSON.parse(buffer.slice(0, buffer.indexOf("\n")))
      const handle = request.params?.script_handle
      asked.push(handle)
      const reply = (body: unknown) => socket.end(`${JSON.stringify(body)}\n`)
      if (request.token !== token) return reply({ id: request.id, ok: false, error: { code: "unauthorized" } })
      if (!scripts.includes(handle))
        return reply({ id: request.id, ok: false, error: { code: "unknown_script", message: "Unknown script_handle" } })
      reply({
        id: request.id,
        ok: true,
        result: {
          protocol: 1,
          host_version: readPackage().version,
          app_version: "1.6.15.0",
          capabilities,
          capability_hash: capabilityHash(capabilities as never),
          script_handle: handle,
          revision: 0,
          document: handle,
        },
      })
    })
    socket.on("error", () => {})
  })
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
  const endpoint = { host: "127.0.0.1", port: (server.address() as AddressInfo).port, token }
  return { endpoint, asked, close: () => new Promise<void>((resolve) => server.close(() => resolve())) }
}

it("the list asks hello for each announced script: one Gaffer offers each of its scripts", async () => {
  delete store[offerRegistryKey(offerRegistryName("gaffer", expected))]
  const gaffer = await gafferWithScripts(["gaffer:one", "gaffer:two"])
  const registry = createRegistry(marker)
  const offer = (script_handle: string, offer_key: string, label: string) => ({
    ...gaffer.endpoint,
    script_handle,
    label,
    protocol: 1,
    offer_key,
  })
  try {
    assert.deepEqual(await registry.advertise(offer("gaffer:one", "gaffer-1", "Gaffer: one.gfr")), {
      outcome: "accepted",
    })
    assert.deepEqual(await registry.advertise(offer("gaffer:two", "gaffer-2", "Gaffer: two.gfr")), {
      outcome: "accepted",
    })
    // A script this Gaffer does not have (closed since): its hello fails, the offer is refused.
    assert.deepEqual(await registry.advertise(offer("gaffer:closed", "gaffer-3", "Gaffer: gone.gfr")), {
      outcome: "refused",
    })
    assert.deepEqual(gaffer.asked, ["gaffer:one", "gaffer:two", "gaffer:closed"])
    assert.deepEqual(
      registry
        .hosts()
        .offers.map((item) => [item.label, item.protocol])
        .sort(),
      [
        ["Gaffer: one.gfr", "ok"],
        ["Gaffer: two.gfr", "ok"],
      ],
    )
  } finally {
    registry.retire()
    delete store[offerRegistryKey(offerRegistryName("gaffer", expected))]
    await gaffer.close()
  }
})

it("a tool's error carries the host's own message and data", async () => {
  const fake = await startFakeHost({
    hostVersion: readPackage().version,
    appVersion: "1.6.15.0",
    methods: {
      context: () => {
        throw {
          code: "stale_revision",
          message: "The Gaffer script changed after the intent was prepared: /Sphere",
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
    await assert.rejects(plugin.tool("gaffer_context"), (error: Error) => {
      assert.match(
        error.message,
        /^The script changed since your last read: call gaffer_context and use its revision\. The Gaffer script changed after the intent was prepared: \/Sphere /,
      )
      assert.match(error.message, /"reason":"revision_conflict","expected_revision":3,"current_revision":5/)
      return true
    })
  } finally {
    await plugin.cleanup()
    await fake.close()
  }
})
