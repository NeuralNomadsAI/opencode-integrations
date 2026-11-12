import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"
import { startFakeHost } from "../../../packages/testing/src/index.ts"
import { inspectHost } from "../plugin/src/host-install.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { loadPlugin } from "./helpers.ts"

it("exact-session ownership, detach, expiry and no attachment replay", async () => {
  let now = 0
  const plugin = await loadPlugin({ now: () => now })
  const host = await startFakeHost({
    hostVersion: "0.2.0",
    helloOverride: (hello) => ({ ...hello, app_version: "22.0.429" }),
  })
  try {
    assert.deepEqual(
      await plugin.rpc("advertise", {
        ...host.endpoint,
        script_handle: host.hello.script_handle,
        label: "Houdini",
        protocol: 1,
        offer_key: "host-test",
      }),
      { outcome: "accepted" },
    )
    const offerID = plugin.registry.hosts().offers[0]?.offerID as string
    assert.deepEqual(await plugin.rpc("attachHost", { offerID, sessionID: "ses_foreign" }), { outcome: "refused" })
    assert.deepEqual(await plugin.rpc("bindingRead", { sessionID: "ses_foreign" }), { state: "unknown" })
    assert.deepEqual(await plugin.rpc("attachHost", { offerID, sessionID: "ses_test" }), { outcome: "attached" })
    assert.equal(((await plugin.rpc("bindingRead", { sessionID: "ses_test" })) as { state: string }).state, "attached")
    assert.deepEqual(await plugin.rpc("detachHost", { sessionID: "ses_test" }), { outcome: "detached" })
    assert.equal(((await plugin.rpc("bindingRead", { sessionID: "ses_test" })) as { state: string }).state, "none")
    now = 16000
    plugin.registry.sweep()
    assert.deepEqual(await plugin.rpc("hosts"), { offers: [] })
  } finally {
    await plugin.cleanup()
    await host.close()
  }
})

it("installer needs explicit preferences, installs stable payload, is idempotent and refuses WSL", async () => {
  const root = mkdtempSync(join(tmpdir(), "houdini-install-"))
  const preferences = join(root, "prefs")
  mkdirSync(preferences)
  const plugin = await loadPlugin({ options: { hostInspectionDir: preferences }, state: join(root, "state") })
  try {
    assert.deepEqual(inspectHost(preferences), { kind: "not-found" })
    assert.deepEqual(await plugin.rpc("installHost"), {
      outcome: "installed",
      detail: "Open Houdini, then open the OpenCode Link panel and choose Connect.",
    })
    const native = JSON.parse(readFileSync(join(preferences, "packages", "neuralnomads_houdini.json"), "utf8"))
    assert.ok(native.env[0].NEURALNOMADS_HOUDINI_ROOT.startsWith(join(root, "state").replaceAll("\\", "/")))
    assert.equal(native.neuralnomadsHostVersion, "0.2.1")
    const imported = spawnSync(
      process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3"),
      [
        "-I",
        join(import.meta.dirname, "panel_probe.py"),
        "test_installed_import",
        join(preferences, "packages", "neuralnomads_houdini.json"),
      ],
      { windowsHide: true, encoding: "utf8" },
    )
    assert.equal(imported.status, 0, imported.stderr)
    assert.deepEqual(inspectHost(preferences), { kind: "found", version: "0.2.1" })
    assert.deepEqual(await plugin.rpc("installHost"), { outcome: "unchanged" })
    const wsl = await loadPlugin({
      options: { hostInspectionDir: preferences },
      state: join(root, "never"),
      wsl: () => true,
    })
    try {
      assert.equal(((await wsl.rpc("installHost")) as { outcome: string }).outcome, "refused")
    } finally {
      await wsl.cleanup()
    }
  } finally {
    await plugin.cleanup()
    rmSync(root, { recursive: true, force: true })
  }
})

it("real onDestroyInterface callbacks revoke panel ownership idempotently through 2 → 1 → 0", () => {
  const result = spawnSync(
    process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3"),
    ["-I", join(import.meta.dirname, "panel_probe.py"), "test_shared_host_lifetime"],
    { windowsHide: true, encoding: "utf8" },
  )
  assert.equal(result.status, 0, result.stderr)
})

it("attachment and write permissions fence revoked hosts, without dispatch or replay", async () => {
  let revoke = () => {}
  const plugin = await loadPlugin({ answer: async () => revoke() })
  const host = await startFakeHost({
    hostVersion: "0.2.0",
    helloOverride: (hello) => ({ ...hello, app_version: "22.0.429" }),
  })
  const offer = {
    ...host.endpoint,
    script_handle: host.hello.script_handle,
    label: "Houdini",
    protocol: 1,
    offer_key: "write-test",
  }
  try {
    await plugin.rpc("advertise", offer)
    let offerID = plugin.registry.hosts().offers[0]?.offerID as string
    revoke = () => {
      plugin.registry.withdraw({ offer_key: offer.offer_key, token: offer.token })
    }
    assert.deepEqual(JSON.parse(String((await plugin.tool("attach", { offerID }))?.content)), { outcome: "refused" })
    assert.deepEqual(plugin.permissions, ["houdini_attach"])
    await plugin.rpc("advertise", offer)
    offerID = plugin.registry.hosts().offers[0]?.offerID as string
    await plugin.rpc("attachHost", { offerID, sessionID: "ses_test" })
    const writesBefore = host.requests.filter((request) => request.method === "apply").length
    await assert.rejects(
      plugin.tool("apply", {
        script_handle: host.hello.script_handle,
        if_revision: 1,
        command_id: "a",
        operations: [{}],
        dry_run: false,
        confirm: true,
      }),
    )
    assert.equal(host.requests.filter((request) => request.method === "apply").length, writesBefore)
    assert.equal(plugin.permissions.at(-1), "houdini_write")
  } finally {
    await plugin.cleanup()
    await host.close()
  }
})

it("package publishing metadata and preview marker keep the SDK optional and the host embedded", () => {
  const pkg = readPackage()
  assert.equal(pkg.opencodeIntegration.id, "houdini")
  assert.equal(pkg.opencodeIntegration.plugin, "neuralnomads.houdini")
  const manifest = JSON.parse(readFileSync(join(import.meta.dirname, "..", "plugin", "package.json"), "utf8"))
  assert.equal(manifest.publishConfig.access, "public")
  assert.equal(manifest.peerDependencies["@opencode/plugin"], "^2.2.0 || ^0.0.0-dev")
  assert.equal(manifest.peerDependenciesMeta["@opencode/plugin"].optional, true)
  assert.equal(manifest.dependencies, undefined)
  for (const hook of ["preinstall", "install", "postinstall", "prepare"])
    assert.equal(manifest.scripts[hook], undefined)
})
