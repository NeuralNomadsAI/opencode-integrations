// End to end on an isolated OpenCode service (AC-B8, AC-B15): the built plugin loads from the global
// configuration's `plugins` list, a background Blender with "Offer this session to OpenCode" ticked
// discovers the service from the fixture's XDG_STATE_HOME and advertises, `hosts` lists the offer,
// `attachHost` binds a session, and unticking the consent removes the offer within the lease.
// Skipped unless OPENCODE_TEST_BIN (OpenCode 2.2.5) and BLENDER_TEST_BIN are set; run after `npm run build`.
// Never touches the shared daemon or the user's configuration: everything lives in a temporary root.

import assert from "node:assert/strict"
import { type ChildProcess, spawn } from "node:child_process"
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import net from "node:net"
import { join } from "node:path"
import { describe, it } from "node:test"

import { blenderBin, installAddon, isolatedRoot, runBlender } from "./helpers/blender.ts"

const opencodeBin =
  process.env.OPENCODE_TEST_BIN && existsSync(process.env.OPENCODE_TEST_BIN) ? process.env.OPENCODE_TEST_BIN : undefined
const pluginDir = join(import.meta.dirname, "..", "plugin")
const skip =
  !opencodeBin || !blenderBin
    ? "OPENCODE_TEST_BIN and BLENDER_TEST_BIN are not both set"
    : !existsSync(join(pluginDir, "dist", "index.js"))
      ? "build the plugin first"
      : false

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

async function freePort() {
  const server = net.createServer()
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()))
  const { port } = server.address() as net.AddressInfo
  await new Promise<void>((resolve) => server.close(() => resolve()))
  return port
}

async function until<T>(label: string, timeoutMs: number, probe: () => Promise<T | undefined>): Promise<T> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await probe().catch(() => undefined)
    if (value !== undefined) return value
    await wait(250)
  }
  throw new Error(`timed out waiting for ${label}`)
}

describe("OpenCode 2.2.5 service with a background Blender", { skip }, () => {
  it("advertises, lists, attaches and withdraws an offer", async () => {
    const iso = isolatedRoot("blender-e2e-")
    const globalConfig = join(iso.env.XDG_CONFIG_HOME as string, "opencode")
    mkdirSync(globalConfig, { recursive: true })
    writeFileSync(
      join(globalConfig, "opencode.json"),
      JSON.stringify({ plugins: [{ package: pluginDir, options: {} }] }),
    )
    writeFileSync(join(globalConfig, "service.json"), JSON.stringify({ port: await freePort() }))
    const env = {
      ...iso.env,
      OPENCODE_DISABLE_MODELS_FETCH: "1",
      OPENCODE_DISABLE_AUTOUPDATE: "1",
      OPENCODE_DISABLE_FFF: "1",
    }
    let logs = ""
    const daemon: ChildProcess = spawn(opencodeBin as string, ["serve", "--service", "--print-logs"], {
      env,
      cwd: iso.root,
      windowsHide: true,
    })
    const collect = (chunk: Buffer) => {
      logs += chunk
    }
    daemon.stdout?.on("data", collect)
    daemon.stderr?.on("data", collect)
    const stopped = new Promise((resolve) => daemon.once("close", resolve))
    let blender: ReturnType<typeof runBlender> | undefined
    try {
      const registration = join(iso.env.XDG_STATE_HOME as string, "opencode", "service.json")
      const info = await until("the service registration", 60_000, async () => {
        const parsed = existsSync(registration)
          ? (JSON.parse(readFileSync(registration, "utf8")) as { url?: string; password?: string })
          : {}
        return parsed.url ? parsed : undefined
      })
      const authorization = `Basic ${Buffer.from(`opencode:${info.password}`).toString("base64")}`
      const rpc = async (method: string, input: unknown = {}) => {
        const url = new URL(`/api/rpc/neuralnomads.blender/${method}`, info.url)
        url.searchParams.set("location[directory]", globalConfig)
        const response = await fetch(url, {
          method: "POST",
          headers: { authorization, "content-type": "application/json" },
          body: JSON.stringify({ input }),
        })
        const body = (await response.json()) as { output?: Record<string, unknown> }
        if (response.status !== 200) throw new Error(`${method}: ${response.status} ${JSON.stringify(body)}`)
        return body.output as Record<string, unknown>
      }

      const status = await until("the plugin's status", 90_000, () => rpc("status"))
      assert.deepEqual(status.integration, { id: "blender", version: "0.1.0", family: "native", spec: 0 })
      assert.deepEqual(await rpc("hosts"), { offers: [] })

      installAddon(iso)
      blender = runBlender(iso, join(import.meta.dirname, "blender", "serve.py"), [
        "--offer",
        "--seconds",
        "90",
        "--off-after",
        "25",
      ])
      const offers = await until("an offer", 30_000, async () => {
        const listed = (await rpc("hosts")) as { offers: Array<{ offerID: string; label: string; protocol: string }> }
        return listed.offers.length > 0 ? listed.offers : undefined
      })
      assert.equal(offers.length, 1)
      assert.equal(offers[0]?.label, "Blender: Untitled")
      assert.equal(offers[0]?.protocol, "ok")
      assert.deepEqual(await rpc("attachHost", { offerID: offers[0]?.offerID, sessionID: "ses_e2e" }), {
        outcome: "attached",
      })
      const attached = await rpc("status")
      assert.deepEqual(attached.connection, { state: "attached", offers: 1, attachedSessions: 1 })
      assert.equal((attached.host as { state: string }).state, "ok")

      await until("the consent switch", 40_000, async () =>
        blender?.output().includes("CONSENT_OFF") ? true : undefined,
      )
      const off = Date.now()
      await until("the withdrawn offer", 16_000, async () => {
        const listed = (await rpc("hosts")) as { offers: unknown[] }
        return listed.offers.length === 0 ? true : undefined
      })
      assert.ok(Date.now() - off < 15_500, "gone within the lease")
      assert.deepEqual((await rpc("status")).connection, { state: "none", offers: 0, attachedSessions: 0 })
    } catch (error) {
      throw new Error(
        `${(error as Error).message}\n--- blender\n${blender?.output().slice(-2000) ?? ""}\n--- daemon\n${logs.slice(-3000)}`,
      )
    } finally {
      blender?.child.kill()
      await blender?.exited
      daemon.kill()
      await Promise.race([stopped, wait(5_000)])
      if (daemon.exitCode === null && process.platform === "win32")
        spawn("taskkill", ["/pid", String(daemon.pid), "/t", "/f"], { windowsHide: true })
      await wait(500)
      iso.cleanup()
    }
  })
})
