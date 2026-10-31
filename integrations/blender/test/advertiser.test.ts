// The add-on's advertiser (AC-B11, HST-18 to HST-21) under the system Python against fake OpenCode
// services: every discovered service by default, one by name, unreachable services skipped, the
// OPENCODE_CONFIG_DIR fallback setting, and withdrawal on stop. Paths come from the test's own
// HOME and XDG folders; the real service registration is never read.

import assert from "node:assert/strict"
import { execFile } from "node:child_process"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { createServer, type IncomingMessage } from "node:http"
import net from "node:net"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, describe, it } from "node:test"
import { promisify } from "node:util"

import { hostFolder, PYTHON } from "./helpers/plugin.ts"

const TOKEN = "d".repeat(64)
const PASSWORD = "sentinel-daemon-password"
const cleanups: Array<() => Promise<void> | void> = []
after(async () => {
  for (const cleanup of cleanups.reverse()) await cleanup()
})

type Call = { method: string; directory: string | null; input: Record<string, unknown> }

async function service(pid: number, answer: (call: Call) => unknown = () => ({ output: { outcome: "accepted" } })) {
  const calls: Call[] = []
  const read = async (request: IncomingMessage) => {
    let text = ""
    for await (const chunk of request) text += chunk
    return text ? JSON.parse(text) : null
  }
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1")
    const send = (status: number, body: unknown) => {
      response.writeHead(status, { "content-type": "application/json" })
      response.end(JSON.stringify(body))
    }
    if (url.pathname === "/api/info") return send(200, { pid, version: "2.2.5" })
    const match = /^\/api\/rpc\/neuralnomads\.blender\/(\w+)$/.exec(url.pathname)
    if (!match) return send(404, {})
    const call = {
      method: match[1] ?? "",
      directory: url.searchParams.get("location[directory]"),
      input: (await read(request)).input,
    }
    calls.push(call)
    const reply = answer(call) as { status?: number }
    send(reply.status ?? 200, reply)
  })
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()))
  cleanups.push(() => new Promise<void>((resolve) => server.close(() => resolve())))
  return { url: `http://127.0.0.1:${(server.address() as net.AddressInfo).port}`, pid, calls }
}

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "blender-advertiser-"))
  cleanups.push(() => rmSync(root, { recursive: true, force: true }))
  const env = {
    PATH: process.env.PATH,
    SystemRoot: process.env.SystemRoot,
    HOME: join(root, "home"),
    USERPROFILE: join(root, "home"),
    XDG_STATE_HOME: join(root, "state"),
    XDG_CONFIG_HOME: join(root, "config"),
  }
  const registrations = join(env.XDG_STATE_HOME, "opencode")
  mkdirSync(registrations, { recursive: true })
  const register = (file: string, info: { url: string; pid: number }) =>
    writeFileSync(join(registrations, file), JSON.stringify({ ...info, version: "2.2.5", password: PASSWORD }))
  return { root, env, register, defaultDir: join(env.XDG_CONFIG_HOME, "opencode") }
}

const driver = `
import json, sys
sys.path.insert(0, sys.argv[1])
from opencode_link.advertiser import Advertiser, OFFER_KEY
request = json.loads(sys.argv[2])
advertiser = Advertiser(lambda: (4242, request["token"], "blend-test", "Blender: test.blend"),
                        service_name=request.get("service", ""), config_dir=request.get("config_dir", ""), env=request["env"])
advertised = advertiser.advertise_once()
withdrawn = advertiser.stop(request["token"]) if request.get("stop") else []
print(json.dumps({"advertised": advertised, "withdrawn": withdrawn, "offer_key": OFFER_KEY, "repr": repr(advertiser)}))
`

async function run(env: Record<string, string | undefined>, request: Record<string, unknown> = {}) {
  const { stdout } = await promisify(execFile)(
    PYTHON,
    ["-I", "-c", driver, hostFolder, JSON.stringify({ token: TOKEN, env, ...request })],
    { env: env as NodeJS.ProcessEnv, windowsHide: true },
  )
  assert.ok(!stdout.includes(PASSWORD) && !stdout.includes(TOKEN), "no password nor token in results")
  return JSON.parse(stdout) as {
    advertised: Array<{ service: string; outcome: string }>
    withdrawn: Array<{ service: string; outcome: string }>
    offer_key: string
  }
}

describe("advertising to OpenCode services (AC-B11)", () => {
  it("offers the session to every discovered service by default", async () => {
    const { env, register, defaultDir } = fixture()
    const shared = await service(101)
    const named = await service(102)
    register("service.json", shared)
    register("service-studio.json", named)
    const result = await run(env, { stop: true })
    assert.deepEqual(result.advertised, [
      { service: "service-studio.json", outcome: "accepted" },
      { service: "service.json", outcome: "accepted" },
    ])
    for (const target of [shared, named]) {
      const advertise = target.calls.find((call) => call.method === "advertise")
      assert.equal(advertise?.directory, defaultDir, "the global configuration Location")
      assert.deepEqual(advertise?.input, {
        host: "127.0.0.1",
        port: 4242,
        token: TOKEN,
        script_handle: "blend-test",
        label: "Blender: test.blend",
        protocol: 1,
        offer_key: result.offer_key,
      })
      assert.deepEqual(target.calls.find((call) => call.method === "withdraw")?.input, {
        offer_key: result.offer_key,
        token: TOKEN,
      })
    }
  })

  it("offers it to the named service only when the setting is filled", async () => {
    const { env, register } = fixture()
    const shared = await service(201)
    const named = await service(202)
    register("service.json", shared)
    register("service-studio.json", named)
    const result = await run(env, { service: "studio" })
    assert.deepEqual(result.advertised, [{ service: "service-studio.json", outcome: "accepted" }])
    assert.equal(shared.calls.length, 0)
  })

  it("skips unreachable services without blocking the others", async () => {
    const { env, register } = fixture()
    const healthy = await service(301)
    const closed = net.createServer()
    await new Promise<void>((resolve) => closed.listen(0, "127.0.0.1", () => resolve()))
    const port = (closed.address() as net.AddressInfo).port
    await new Promise<void>((resolve) => closed.close(() => resolve()))
    const sockets = new Set<net.Socket>()
    const garbage = net.createServer((socket) => {
      sockets.add(socket)
      socket.on("error", () => {})
      socket.end("not http\r\n\r\n")
    })
    await new Promise<void>((resolve) => garbage.listen(0, "127.0.0.1", () => resolve()))
    cleanups.push(() => {
      for (const socket of sockets) socket.destroy()
      return new Promise<void>((resolve) => garbage.close(() => resolve()))
    })
    register("service-gone.json", { url: `http://127.0.0.1:${port}`, pid: 302 })
    register("service-junk.json", { url: `http://127.0.0.1:${(garbage.address() as net.AddressInfo).port}`, pid: 303 })
    register("service.json", healthy)
    const result = await run(env)
    assert.deepEqual(result.advertised, [{ service: "service.json", outcome: "accepted" }])
  })

  it("retries with the configuration folder setting when the default Location has no RPC (OPENCODE_CONFIG_DIR)", async () => {
    const { env, register, root } = fixture()
    const custom = join(root, "custom-config")
    const target = await service(401, (call) =>
      call.directory === custom ? { output: { outcome: "accepted" } } : { status: 404, type: "rpc.unavailable" },
    )
    register("service.json", target)
    const result = await run(env, { config_dir: custom })
    assert.deepEqual(result.advertised, [{ service: "service.json", outcome: "accepted" }])
    assert.deepEqual(
      target.calls.map((call) => call.directory),
      [join(env.XDG_CONFIG_HOME, "opencode"), custom],
    )
  })
})
