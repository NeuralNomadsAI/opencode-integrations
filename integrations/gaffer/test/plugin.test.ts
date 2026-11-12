import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { it } from "node:test"
import { startFakeHost } from "../../../packages/testing/src/index.ts"
import { installHost } from "../plugin/src/host-install.ts"
import { readPackage, resultContent } from "../plugin/src/integration.ts"
import { packageRoot, stateRoot } from "../plugin/src/paths.ts"
import { readStoredZip } from "../plugin/src/zip.ts"
import { loadPlugin } from "./helpers.ts"

it("Gaffer attaches only owned sessions; revision, authority, expiry, detach and move fencing", async () => {
  let now = 10_000
  const plugin = await loadPlugin({ now: () => now })
  const host = await startFakeHost({ hostVersion: "0.2.0" })
  try {
    const announced = {
      host: "127.0.0.1",
      port: host.endpoint.port,
      token: host.endpoint.token,
      script_handle: host.scriptHandle,
      label: "Gaffer test",
      protocol: 1,
      offer_key: "test-offer",
    }
    assert.deepEqual(await plugin.rpc("advertise", { ...announced, host: "localhost" }), { outcome: "refused" })
    assert.deepEqual(await plugin.rpc("advertise", { ...announced, token: "0".repeat(64) }), { outcome: "refused" })
    assert.deepEqual(await plugin.rpc("advertise", announced), { outcome: "accepted" })
    const offer = plugin.registry.hosts().offers[0]!
    assert.deepEqual(await plugin.rpc("attachHost", { offerID: offer.offerID, sessionID: "ses_foreign" }), {
      outcome: "refused",
    })
    assert.deepEqual(await plugin.rpc("bindingRead", { sessionID: "ses_foreign" }), { state: "unknown" })
    const before = (await plugin.rpc("bindingRead", { sessionID: "ses_test" })) as {
      state: string
      authority: { revision: number }
    }
    assert.equal(before.state, "none")
    assert.deepEqual(await plugin.rpc("attachHost", { offerID: offer.offerID, sessionID: "ses_test" }), {
      outcome: "attached",
    })
    const bound = (await plugin.rpc("bindingRead", { sessionID: "ses_test" })) as typeof before
    assert.equal(bound.state, "attached")
    assert.ok(bound.authority.revision > before.authority.revision)
    assert.deepEqual(await plugin.rpc("bindingRead", { sessionID: "ses_other" }), {
      state: "none",
      authority: bound.authority,
    })
    plugin.sessions.set("ses_test", { id: "ses_test", location: { directory: join(tmpdir(), "foreign") } })
    assert.deepEqual(await plugin.rpc("bindingRead", { sessionID: "ses_test" }), { state: "unknown" })
    await assert.rejects(plugin.rpc("detachHost", { sessionID: "ses_test" }), /ownership/)
    plugin.sessions.set("ses_test", { id: "ses_test", location: { ...plugin.location } })
    assert.deepEqual(await plugin.rpc("detachHost", { sessionID: "ses_test" }), { outcome: "detached" })
    assert.deepEqual(await plugin.rpc("advertise", announced), { outcome: "renewed" })
    assert.equal(((await plugin.rpc("bindingRead", { sessionID: "ses_test" })) as { state: string }).state, "none")
    await plugin.rpc("attachHost", { offerID: offer.offerID, sessionID: "ses_test" })
    now += 15_001
    assert.deepEqual(await plugin.rpc("hosts"), { offers: [] })
    assert.equal(((await plugin.rpc("bindingRead", { sessionID: "ses_test" })) as { state: string }).state, "none")
    assert.ok(plugin.events.every(({ data }) => JSON.stringify(data) === "{}"))
  } finally {
    await plugin.cleanup()
    await host.close()
  }
})

it("Gaffer permission denial and attachment changes prevent attaching", async () => {
  const denied = await loadPlugin({
    answer: async () => {
      throw new Error("Denied")
    },
  })
  try {
    await assert.rejects(denied.tool("gaffer_hosts"), /Denied/)
    assert.deepEqual(denied.permissions, ["gaffer_attach"])
    assert.ok(denied.skills.has("gaffer-graph"))
  } finally {
    await denied.cleanup()
  }
  const moving = await loadPlugin({
    answer: async () => {
      moving.sessions.delete("ses_test")
    },
  })
  try {
    await assert.rejects(moving.tool("gaffer_hosts"), /changed/)
  } finally {
    await moving.cleanup()
  }
})

it("WSL refuses host installation before writes; closed host remains unknown", async () => {
  const plugin = await loadPlugin({ wsl: () => true })
  try {
    const status = (await plugin.rpc("status")) as { host: { state: string } }
    assert.equal(status.host.state, "unsupported")
    assert.equal(((await plugin.rpc("installHost")) as { outcome: string }).outcome, "refused")
  } finally {
    await plugin.cleanup()
  }
  const supported = await loadPlugin()
  try {
    assert.equal(((await supported.rpc("status")) as { host: { state: string } }).host.state, "unknown")
  } finally {
    await supported.cleanup()
  }
})

it("host payload and installer: versioned offline archive, stable sidecar and unmanaged-file protection", async () => {
  const scratch = mkdtempSync(join(tmpdir(), "gaffer-install-"))
  const oldLocal = process.env.LOCALAPPDATA
  const oldState = process.env.XDG_STATE_HOME
  process.env.LOCALAPPDATA = join(scratch, "local")
  process.env.XDG_STATE_HOME = join(scratch, "state")
  try {
    const marker = readPackage().opencodeIntegration
    const payload = readFileSync(join(packageRoot, marker.host!.payload))
    assert.equal(createHash("sha256").update(payload).digest("hex"), marker.host!.sha256)
    const entries = readStoredZip(payload)
    assert.equal(
      JSON.parse(entries.find((e) => e.name === "manifest.json")!.data.toString()).version,
      readPackage().version,
    )
    const sidecar = entries.find((e) => e.name === "sidecar.js")!.data.toString()
    assert.ok(!sidecar.includes("sidecar.ts"))
    const startup = join(scratch, "startup", "gui")
    assert.deepEqual(await installHost(marker, { hostStartupDir: startup }), { outcome: "installed" })
    assert.deepEqual(await installHost(marker, { hostStartupDir: startup }), { outcome: "unchanged" })
    const stable = join(stateRoot(), "hosts", marker.host!.sha256)
    assert.ok(existsSync(join(stable, "sidecar.js")))
    const loader = join(startup, "neuralnomadsOpenCode.py")
    assert.ok(!readFileSync(loader, "utf8").includes(packageRoot.replaceAll("\\", "/")))
    writeFileSync(loader, "# user file\n")
    assert.equal((await installHost(marker, { hostStartupDir: startup })).outcome, "refused")
    assert.equal(readFileSync(loader, "utf8"), "# user file\n")
    assert.equal(readdirSync(join(stateRoot(), "hosts")).length, 1)
  } finally {
    if (oldLocal === undefined) delete process.env.LOCALAPPDATA
    else process.env.LOCALAPPDATA = oldLocal
    if (oldState === undefined) delete process.env.XDG_STATE_HOME
    else process.env.XDG_STATE_HOME = oldState
    rmSync(scratch, { recursive: true, force: true })
  }
})

it("render feedback uses attachments and removes temporary image paths from text", () => {
  const job = { outputs: [{ name: "beauty", preview_path: join(tmpdir(), "preview.png") }, { name: "pending" }] }
  for (const result of [{ job }, { jobs: [job, { outputs: [] }] }, { job, jobs: [job] }]) {
    const content = resultContent(result)
    assert.ok(Array.isArray(content))
    assert.equal(content.length, "job" in result && "jobs" in result ? 3 : 2)
    assert.equal(content[1]!.type, "file")
    assert.ok(!(content[0] as { text: string }).text.includes("preview_path"))
    assert.ok(!(content[0] as { text: string }).text.includes("preview.png"))
    assert.ok(job.outputs[0]?.preview_path)
  }
})
