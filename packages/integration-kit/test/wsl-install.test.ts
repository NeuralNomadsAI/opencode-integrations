import assert from "node:assert/strict"
import { it } from "node:test"

import { startFakeHost } from "../../conformance/src/index.ts"
import { createIntegrationRpc, createOfferRegistry, type InstallHostOutput, isWslDaemon } from "../src/index.ts"

for (const detection of ["environment", "kernel", "ordinary Linux"] as const) {
  for (const withOffer of [false, true]) {
    it(`installHost: ${detection}, validated offer ${withOffer}`, async (t) => {
      const wsl = () =>
        isWslDaemon({
          platform: "linux",
          env: detection === "environment" ? { WSL_DISTRO_NAME: "Ubuntu" } : {},
          procVersion: () => (detection === "kernel" ? "Linux Microsoft WSL2" : "Linux 6.8.0-generic"),
        })
      const registry = createOfferRegistry({
        expected: { protocol: 1, range: "^0.1.0", appVersions: ">=5.2" },
        now: () => 1_000,
      })
      if (withOffer) {
        const host = await startFakeHost()
        t.after(() => host.close())
        assert.deepEqual(
          await registry.advertise({
            ...host.endpoint,
            script_handle: host.scriptHandle,
            label: "Scene",
            protocol: 1,
            offer_key: "process-1",
          }),
          { outcome: "accepted" },
        )
      }
      let installerCalls = 0
      let inspectionCalls = 0
      const rpc = createIntegrationRpc({
        marker: {
          spec: 0,
          id: "demo",
          displayName: "OpenCode for Demo",
          family: "native",
          plugin: "neuralnomads.demo",
          rpc: "neuralnomads.demo",
          host: {
            app: "demo",
            protocol: 1,
            range: "^0.1.0",
            appVersions: ">=5.2",
            payload: "host/demo/part.zip",
            sha256: "0".repeat(64),
          },
        },
        version: "0.1.0",
        registry,
        wsl,
        inspectHost: async () => {
          inspectionCalls++
          return { kind: "not-found" }
        },
        installHost: async () => {
          installerCalls++
          return { outcome: "installed" }
        },
      })
      const result = (await rpc.handlers.installHost?.({})) as InstallHostOutput
      assert.equal(inspectionCalls, 0)
      assert.equal(installerCalls, detection === "ordinary Linux" ? 1 : 0)
      if (detection === "ordinary Linux") assert.deepEqual(result, { outcome: "installed" })
      else {
        assert.equal(result.outcome, "refused")
        assert.match(result.detail ?? "", /WSL/)
      }
    })
  }
}
