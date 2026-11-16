import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"
import { startFakeHost } from "../../../packages/testing/src/index.ts"
import { inspectHost } from "../plugin/src/host-install.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { announcement, content, isolatedPaths, loadPlugin, prefsFolder } from "./helpers.ts"

const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
const version = readPackage().version

it("exact-session ownership, detach, expiry and no attachment replay", async () => {
  let now = 0
  const plugin = await loadPlugin({ now: () => now })
  const host = await startFakeHost({ hostVersion: version, appVersion: "22.0.429" })
  try {
    assert.deepEqual(await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle, "host-test")), {
      outcome: "accepted",
    })
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

it("installs in the newest Houdini 21+ preferences folder, idempotently, says to restart Houdini, refuses WSL", async () => {
  const root = mkdtempSync(join(tmpdir(), "houdini-install-"))
  const paths = isolatedPaths(join(root, "home"))
  for (const version of ["20.5", "22.0", "21.0"]) mkdirSync(prefsFolder(paths, version), { recursive: true })
  const preferences = prefsFolder(paths, "22.0")
  const plugin = await loadPlugin({ paths, state: join(root, "state") })
  try {
    assert.deepEqual(inspectHost(preferences), { kind: "not-found" })
    assert.deepEqual(await plugin.rpc("installHost"), {
      outcome: "installed",
      detail: "Restart Houdini: OpenCode Link offers it to OpenCode when it starts.",
    })
    const file = join(preferences, "packages", "neuralnomads_houdini.json")
    const native = JSON.parse(readFileSync(file, "utf8"))
    assert.ok(native.env[0].NEURALNOMADS_HOUDINI_ROOT.startsWith(join(root, "state").replaceAll("\\", "/")))
    assert.equal(native.neuralnomadsHostVersion, version)
    const imported = spawnSync(
      python,
      ["-I", join(import.meta.dirname, "panel_probe.py"), "test_installed_import", file],
      {
        windowsHide: true,
        encoding: "utf8",
      },
    )
    assert.equal(imported.status, 0, imported.stderr)
    // The startup script that offers Houdini to OpenCode is installed with it.
    const payload = native.env[0].NEURALNOMADS_HOUDINI_ROOT as string
    assert.match(readFileSync(join(payload, "scripts", "python", "pythonrc.py"), "utf8"), /start_with_houdini/)
    assert.match(
      readFileSync(join(payload, "scripts", "opencode_houdini", "opencode_service.py"), "utf8"),
      /def advertise_all/,
    )
    assert.deepEqual(inspectHost(preferences), { kind: "found", version })
    assert.deepEqual(await plugin.rpc("installHost"), { outcome: "unchanged" })
    const wsl = await loadPlugin({ paths, state: join(root, "never"), wsl: () => true })
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

it("an earlier OpenCode package in Houdini's preferences is a notice and said by houdini_hosts", async () => {
  const root = mkdtempSync(join(tmpdir(), "houdini-legacy-"))
  const paths = isolatedPaths(join(root, "home"))
  const packages = join(prefsFolder(paths, "22.0"), "packages")
  mkdirSync(packages, { recursive: true })
  const plugin = await loadPlugin({ paths })
  try {
    assert.equal(content(await plugin.tool("houdini_hosts")).legacy_install, undefined)
    writeFileSync(
      join(packages, "opencode_houdini.json"),
      JSON.stringify({ path: "/opt/earlier/opencode/bundles/houdini" }),
    )
    const status = (await plugin.rpc("status")) as { notices?: string[] }
    assert.deepEqual(status.notices, ["legacy_install"])
    const listed = content(await plugin.tool("houdini_hosts")).legacy_install
    assert.equal(listed.found[0].points_to, "/opt/earlier/opencode/bundles/houdini")
    assert.match(listed.fix, /remove it, or set "enable": false/)
    // Disabled by the user: no notice.
    writeFileSync(join(packages, "opencode_houdini.json"), JSON.stringify({ enable: false, path: "x" }))
    assert.deepEqual(((await plugin.rpc("status")) as { notices?: string[] }).notices ?? [], [])
  } finally {
    await plugin.cleanup()
    rmSync(root, { recursive: true, force: true })
  }
})

it("panels share the host Houdini started; closing every panel keeps its offer for other conversations", () => {
  const result = spawnSync(python, ["-I", join(import.meta.dirname, "panel_probe.py"), "test_shared_host_lifetime"], {
    windowsHide: true,
    encoding: "utf8",
  })
  assert.equal(result.status, 0, result.stderr)
})

it("attachment and write permissions fence revoked hosts, without dispatch or replay", async () => {
  let revoke = () => {}
  const plugin = await loadPlugin({ answer: async () => revoke() })
  const host = await startFakeHost({
    hostVersion: version,
    appVersion: "22.0.429",
    methods: { apply: () => ({ status: "applied" }) },
  })
  const offer = announcement(host.endpoint, host.scriptHandle, "write-test")
  try {
    await plugin.rpc("advertise", offer)
    let offerID = plugin.registry.hosts().offers[0]?.offerID as string
    revoke = () => {
      plugin.registry.withdraw({ offer_key: offer.offer_key, token: offer.token })
    }
    await assert.rejects(plugin.tool("houdini_hosts", { attach: offerID }), /changed while permission was asked/)
    assert.deepEqual(plugin.permissions, ["houdini_attach"])
    revoke = () => {}
    await plugin.rpc("advertise", offer)
    offerID = plugin.registry.hosts().offers[0]?.offerID as string
    await plugin.rpc("attachHost", { offerID, sessionID: "ses_test" })
    const writes = () => host.requests.filter((request) => request.method === "apply").length
    // A dry run asks nothing; a change without confirm is refused before Houdini.
    assert.equal(
      content(await plugin.tool("houdini_apply", { if_revision: 1, command_id: "a", operations: [{}] })).status,
      "applied",
    )
    assert.equal(plugin.permissions.length, 1)
    await assert.rejects(
      plugin.tool("houdini_apply", { if_revision: 1, command_id: "b", operations: [{}], dry_run: false }),
      /confirm: true/,
    )
    revoke = () => {
      plugin.registry.withdraw({ offer_key: offer.offer_key, token: offer.token })
    }
    const before = writes()
    await assert.rejects(
      plugin.tool("houdini_apply", {
        if_revision: 1,
        command_id: "c",
        operations: [{}],
        dry_run: false,
        confirm: true,
      }),
    )
    assert.equal(writes(), before)
    assert.equal(plugin.permissions.at(-1), "houdini_write")
  } finally {
    await plugin.cleanup()
    await host.close()
  }
})

it("package publishing metadata and marker keep the SDK optional and the host embedded", () => {
  const pkg = readPackage()
  assert.equal(pkg.opencodeIntegration.id, "houdini")
  assert.equal(pkg.opencodeIntegration.plugin, "neuralnomads.houdini")
  assert.equal(pkg.opencodeIntegration.host?.range, `^${version}`)
  const manifest = JSON.parse(readFileSync(join(import.meta.dirname, "..", "plugin", "package.json"), "utf8"))
  assert.equal(manifest.publishConfig.access, "public")
  assert.equal(manifest.peerDependencies["@opencode/plugin"], "^2.2.0 || ^0.0.0-dev")
  assert.equal(manifest.peerDependenciesMeta["@opencode/plugin"].optional, true)
  assert.equal(manifest.dependencies, undefined)
  for (const hook of ["preinstall", "install", "postinstall", "prepare"])
    assert.equal(manifest.scripts[hook], undefined)
  assert.ok(manifest.files.includes("methods") && manifest.files.includes("programs"))
  const host = JSON.parse(readFileSync(join(import.meta.dirname, "..", "host", "manifest.json"), "utf8"))
  assert.equal(host.version, version)
})
