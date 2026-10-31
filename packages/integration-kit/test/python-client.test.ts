// Contract of the reference Python discovery client (python/opencode_service.py)
// against fake OpenCode services. Every path comes from the test's own XDG and
// HOME folders; the real service registration is never read.

import assert from "node:assert/strict"
import { execFile } from "node:child_process"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { createServer, type IncomingMessage, type Server } from "node:http"
import net from "node:net"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, describe, it } from "node:test"
import { promisify } from "node:util"

import { capabilityHash } from "../src/index.ts"

const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
const moduleDir = join(import.meta.dirname, "..", "python")
const TOKEN = "e".repeat(64)
const PASSWORD = "sentinel-daemon-password"

type RpcCall = { rpc: string; method: string; directory: string | null; input: unknown; authorization?: string }

type FakeService = {
  url: string
  pid: number
  calls: RpcCall[]
  close(): Promise<void>
}

const cleanups: Array<() => Promise<void> | void> = []
after(async () => {
  for (const cleanup of cleanups.reverse()) await cleanup()
})

async function body(request: IncomingMessage): Promise<unknown> {
  let text = ""
  for await (const chunk of request) text += chunk
  return text ? JSON.parse(text) : null
}

async function fakeService(
  pid: number,
  answer: (call: RpcCall) => { status: number; body: unknown } = () => ({
    status: 200,
    body: { output: { outcome: "accepted" } },
  }),
): Promise<FakeService> {
  const calls: RpcCall[] = []
  const server: Server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1")
    const send = (status: number, value: unknown) => {
      response.writeHead(status, { "content-type": "application/json" })
      response.end(JSON.stringify(value))
    }
    if (request.method === "GET" && url.pathname === "/api/info") return send(200, { pid, version: "2.2.5" })
    const match = /^\/api\/rpc\/([^/]+)\/([^/]+)$/.exec(url.pathname)
    if (request.method === "POST" && match) {
      const payload = (await body(request)) as { input?: unknown }
      const call: RpcCall = {
        rpc: decodeURIComponent(match[1] ?? ""),
        method: decodeURIComponent(match[2] ?? ""),
        directory: url.searchParams.get("location[directory]"),
        input: payload?.input,
        authorization: request.headers.authorization,
      }
      calls.push(call)
      const reply = answer(call)
      return send(reply.status, reply.body)
    }
    send(404, { type: "not_found" })
  })
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()))
  const port = (server.address() as { port: number }).port
  const service = {
    url: `http://127.0.0.1:${port}`,
    pid,
    calls,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  }
  cleanups.push(service.close)
  return service
}

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "python-client-"))
  cleanups.push(() => rmSync(root, { recursive: true, force: true }))
  const home = join(root, "home")
  const env = {
    PATH: process.env.PATH,
    SystemRoot: process.env.SystemRoot,
    HOME: home,
    USERPROFILE: home,
    XDG_STATE_HOME: join(root, "state"),
    XDG_CONFIG_HOME: join(root, "config"),
  }
  const registrations = join(env.XDG_STATE_HOME, "opencode")
  mkdirSync(registrations, { recursive: true })
  mkdirSync(home, { recursive: true })
  const register = (file: string, info: Record<string, unknown>) =>
    writeFileSync(join(registrations, file), JSON.stringify(info))
  return { root, env, register, configDir: join(env.XDG_CONFIG_HOME, "opencode") }
}

const driver = `
import json, sys
sys.path.insert(0, sys.argv[1])
import opencode_service as client
request = json.loads(sys.argv[2])
fn = getattr(client, request["call"])
result = fn(*request.get("args", []), **request.get("kwargs", {}))
if isinstance(result, list):
    result = [repr(item) if isinstance(item, client.Service) else item for item in result]
print(json.dumps(result))
`

// Asynchronous: the fake services answer from this process's event loop.
async function python(env: Record<string, string | undefined>, call: string, args: unknown[] = [], kwargs = {}) {
  const { stdout: output } = await promisify(execFile)(
    PYTHON,
    ["-I", "-c", driver, moduleDir, JSON.stringify({ call, args, kwargs })],
    { env: env as NodeJS.ProcessEnv, encoding: "utf8", windowsHide: true, timeout: 30_000 },
  )
  return { parsed: JSON.parse(output.trim().split("\n").at(-1) ?? "null"), output }
}

const offer = {
  host: "127.0.0.1",
  port: 50123,
  token: TOKEN,
  script_handle: "blend:1",
  label: "scene.blend",
  protocol: 1,
  offer_key: "proc-1",
}

describe("Python discovery client", () => {
  it("advertises to every healthy service at the global configuration Location", async () => {
    const { env, register, configDir } = fixture()
    const main = await fakeService(101)
    const named = await fakeService(202)
    register("service.json", { url: main.url, pid: 101, version: "2.2.5", password: PASSWORD })
    register("service-beta2.json", { url: named.url, pid: 202, version: "2.2.5", password: PASSWORD })
    register("service-stale.json", { url: named.url, pid: 999, version: "2.2.5" })
    register("service-remote.json", { url: "http://192.0.2.10:4096", pid: 5, version: "2.2.5" })
    register("service-down.json", { url: "http://127.0.0.1:9", pid: 6, version: "2.2.5" })
    register("service.json.tmp", { url: main.url, pid: 101 })

    const { parsed, output } = await python(env, "advertise_all", ["neuralnomads.demo", offer])
    assert.deepEqual(parsed, [
      { service: "service-beta2.json", outcome: "accepted" },
      { service: "service.json", outcome: "accepted" },
    ])
    for (const service of [main, named]) {
      assert.equal(service.calls.length, 1)
      const [call] = service.calls
      assert.equal(call?.rpc, "neuralnomads.demo")
      assert.equal(call?.method, "advertise")
      assert.equal(call?.directory, configDir)
      assert.deepEqual(call?.input, offer)
      assert.equal(call?.authorization, `Basic ${Buffer.from(`opencode:${PASSWORD}`).toString("base64")}`)
    }
    assert.doesNotMatch(output, new RegExp(`${TOKEN}|${PASSWORD}`))
  })

  it("targets one service by name; shared channel names select service.json", async () => {
    const { env, register } = fixture()
    const main = await fakeService(101)
    const named = await fakeService(202)
    register("service.json", { url: main.url, pid: 101, version: "2.2.5" })
    register("service-beta2.json", { url: named.url, pid: 202, version: "2.2.5" })
    assert.deepEqual(
      (await python(env, "advertise_all", ["neuralnomads.demo", offer], { service_name: "beta2" })).parsed,
      [{ service: "service-beta2.json", outcome: "accepted" }],
    )
    for (const channel of ["beta", "latest", "dev", "next"]) {
      assert.deepEqual(
        (await python(env, "advertise_all", ["neuralnomads.demo", offer], { service_name: channel })).parsed,
        [{ service: "service.json", outcome: "accepted" }],
        channel,
      )
    }
    assert.deepEqual(
      (await python(env, "advertise_all", ["neuralnomads.demo", offer], { service_name: "absent" })).parsed,
      [],
    )
    assert.equal(main.calls.length, 4)
    assert.equal(named.calls.length, 1)
    const discovered = (await python(env, "discover", [])).parsed as string[]
    assert.equal(discovered.length, 2)
    for (const repr of discovered) assert.doesNotMatch(repr, /password|sentinel/)
  })

  it("falls back to the configured directory when the default Location has no such RPC", async () => {
    const { env, register, configDir, root } = fixture()
    const custom = join(root, "custom-config")
    const service = await fakeService(101, (call) =>
      call.directory === custom
        ? { status: 200, body: { output: { outcome: "renewed" } } }
        : { status: 404, body: { _tag: "RpcError", type: "rpc.unavailable", message: "RPC unavailable" } },
    )
    register("service.json", { url: service.url, pid: 101, version: "2.2.5" })
    assert.deepEqual((await python(env, "advertise_all", ["neuralnomads.demo", offer])).parsed, [
      { service: "service.json", outcome: "unavailable" },
    ])
    service.calls.length = 0
    assert.deepEqual(
      (await python(env, "advertise_all", ["neuralnomads.demo", offer], { config_dir_setting: custom })).parsed,
      [{ service: "service.json", outcome: "renewed" }],
    )
    assert.deepEqual(
      service.calls.map((call) => call.directory),
      [configDir, custom],
    )
  })

  it("withdraws, and reports unexpected answers as errors without their content", async () => {
    const { env, register } = fixture()
    const service = await fakeService(101, (call) =>
      call.method === "withdraw"
        ? { status: 200, body: { output: { outcome: "withdrawn" } } }
        : { status: 500, body: { type: "rpc.internal", message: TOKEN } },
    )
    register("service.json", { url: service.url, pid: 101, version: "2.2.5" })
    assert.deepEqual((await python(env, "withdraw_all", ["neuralnomads.demo", "proc-1", TOKEN])).parsed, [
      { service: "service.json", outcome: "withdrawn" },
    ])
    assert.deepEqual(service.calls.at(-1)?.input, { offer_key: "proc-1", token: TOKEN })
    const { parsed, output } = await python(env, "advertise_all", ["neuralnomads.demo", offer])
    assert.deepEqual(parsed, [{ service: "service.json", outcome: "error" }])
    assert.doesNotMatch(output, new RegExp(TOKEN))
  })

  it("never sends the password or the token through a proxy, nor follows redirects", async () => {
    const { env, register } = fixture()
    const proxied: string[] = []
    const proxy = createServer((request, response) => {
      proxied.push(`${request.method} ${request.url} ${request.headers.authorization ?? ""}`)
      response.writeHead(502)
      response.end()
    })
    await new Promise<void>((resolve) => proxy.listen(0, "127.0.0.1", () => resolve()))
    cleanups.push(() => new Promise<void>((resolve) => proxy.close(() => resolve())))
    const proxyUrl = `http://127.0.0.1:${(proxy.address() as { port: number }).port}`
    const redirected = await fakeService(303, () => ({ status: 200, body: {} }))
    const service = await fakeService(101)
    register("service.json", { url: service.url, pid: 101, version: "2.2.5", password: PASSWORD })
    const proxyEnv = { ...env, HTTP_PROXY: proxyUrl, http_proxy: proxyUrl, ALL_PROXY: proxyUrl, NO_PROXY: "" }
    assert.deepEqual((await python(proxyEnv, "advertise_all", ["neuralnomads.demo", offer])).parsed, [
      { service: "service.json", outcome: "accepted" },
    ])
    assert.deepEqual(proxied, [])

    const redirect = createServer((request, response) => {
      if (request.url === "/api/info") {
        response.writeHead(200, { "content-type": "application/json" })
        return response.end(JSON.stringify({ pid: 404, version: "2.2.5" }))
      }
      response.writeHead(307, { location: `${redirected.url}${request.url}` })
      response.end()
    })
    await new Promise<void>((resolve) => redirect.listen(0, "127.0.0.1", () => resolve()))
    cleanups.push(() => new Promise<void>((resolve) => redirect.close(() => resolve())))
    const redirectUrl = `http://127.0.0.1:${(redirect.address() as { port: number }).port}`
    register("service.json", { url: redirectUrl, pid: 404, version: "2.2.5", password: PASSWORD })
    assert.deepEqual((await python(env, "advertise_all", ["neuralnomads.demo", offer])).parsed, [
      { service: "service.json", outcome: "error" },
    ])
    assert.deepEqual(redirected.calls, [])
  })

  it("skips a port held by a non-HTTP process and a registration without version", async () => {
    const { env, register } = fixture()
    const garbage = net.createServer((socket) => socket.resume().end("not http\r\n\r\n"))
    await new Promise<void>((resolve) => garbage.listen(0, "127.0.0.1", () => resolve()))
    cleanups.push(() => new Promise<void>((resolve) => garbage.close(() => resolve())))
    const garbagePort = (garbage.address() as { port: number }).port
    const service = await fakeService(101)
    const unversioned = await fakeService(202)
    register("service-garbage.json", { url: `http://127.0.0.1:${garbagePort}`, pid: 7, version: "2.2.5" })
    register("service-unversioned.json", { url: unversioned.url, pid: 202 })
    register("service.json", { url: service.url, pid: 101, version: "2.2.5" })
    assert.deepEqual((await python(env, "advertise_all", ["neuralnomads.demo", offer])).parsed, [
      { service: "service.json", outcome: "accepted" },
    ])
    assert.deepEqual(unversioned.calls, [])
  })

  it("computes the same capability hash as the kit", async () => {
    const { env } = fixture()
    const capabilities = {
      methods: ["hello", "context", "apply"],
      operations: [{ name: "object.rename", undoable: true, network: false }],
      undo: "grouped_best_effort" as const,
      background: false,
    }
    assert.equal((await python(env, "capability_hash", [capabilities])).parsed, capabilityHash(capabilities))
  })
})
