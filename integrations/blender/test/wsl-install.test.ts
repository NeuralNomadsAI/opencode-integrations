import assert from "node:assert/strict"
import { mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { it } from "node:test"

import { startFakeHost } from "../../../packages/conformance/src/index.ts"
import { isWslDaemon } from "../plugin/src/kit.ts"
import { announcement, clock, fixturePaths, loadPlugin } from "./helpers/plugin.ts"

function snapshot(root: string) {
  return readdirSync(root, { recursive: true, encoding: "utf8" })
    .sort()
    .map((entry) => {
      const file = join(root, entry)
      return [entry, statSync(file).isDirectory() ? null : readFileSync(file)]
    })
}

it("ordinary Linux still installs into the same kind of existing target", async (t) => {
  const paths = fixturePaths()
  t.after(() => paths.cleanup())
  const dir = join(paths.root, "extensions")
  mkdirSync(dir)
  const loaded = await loadPlugin({
    paths,
    options: { hostInspectionDir: dir },
    wsl: () => isWslDaemon({ platform: "linux", env: {}, procVersion: () => "Linux 6.8.0-generic" }),
  })
  t.after(() => loaded.cleanup())
  assert.equal(((await loaded.rpc("installHost", {})) as { outcome: string }).outcome, "installed")
  assert.match(readFileSync(join(dir, "opencode_link", "blender_manifest.toml"), "utf8"), /id = "opencode_link"/)
})

for (const detection of ["environment", "kernel"] as const) {
  for (const withOffer of [false, true]) {
    it(`WSL ${detection}, offer ${withOffer}: RPC and tool refuse without writes; attachment stays usable`, async (t) => {
      const paths = fixturePaths()
      t.after(() => paths.cleanup())
      const dir = join(paths.root, "extensions")
      mkdirSync(join(dir, "other-addon", "empty"), { recursive: true })
      writeFileSync(join(dir, "other-addon", "sentinel.txt"), "Keep this add-on.\n")
      const loaded = await loadPlugin({
        paths,
        now: clock().now,
        options: { hostInspectionDir: dir },
        wsl: () =>
          isWslDaemon({
            platform: "linux",
            env: detection === "environment" ? { WSL_DISTRO_NAME: "Ubuntu" } : {},
            procVersion: () => (detection === "kernel" ? "Linux microsoft-standard-WSL2" : null),
          }),
      })
      t.after(() => loaded.cleanup())
      if (withOffer) {
        const host = await startFakeHost({ methods: { context: () => ({ revision: 7 }) } })
        t.after(() => host.close())
        assert.deepEqual(await loaded.rpc("advertise", announcement(host.endpoint, host.scriptHandle)), {
          outcome: "accepted",
        })
        const status = (await loaded.rpc("status")) as { host: { state: string }; connection: { state: string } }
        assert.equal(status.host.state, "ok")
        assert.equal(status.connection.state, "available")
        const offer = loaded.registry.hosts().offers[0]
        assert.ok(offer)
        assert.deepEqual(await loaded.rpc("attachHost", { offerID: offer.offerID, sessionID: "ses_test" }), {
          outcome: "attached",
        })
      }
      const before = snapshot(paths.root)
      const rpcResult = (await loaded.rpc("installHost", {})) as { outcome: string; detail: string }
      assert.equal(rpcResult.outcome, "refused")
      assert.match(rpcResult.detail, /WSL/)
      assert.deepEqual(snapshot(paths.root), before)
      const toolResult = JSON.parse(String((await loaded.tool("blender_install_host")).content))
      assert.equal(toolResult.outcome, "refused")
      assert.match(toolResult.detail, /WSL/)
      assert.deepEqual(snapshot(paths.root), before)
      assert.deepEqual(
        loaded.permissions.map((request) => request.action),
        ["blender_install_host"],
      )
      if (withOffer) {
        const status = (await loaded.rpc("status")) as { host: { state: string }; connection: { state: string } }
        assert.equal(status.host.state, "ok")
        assert.equal(status.connection.state, "attached")
        assert.equal(JSON.parse(String((await loaded.tool("blender_context")).content)).revision, 7)
      }
    })
  }
}
