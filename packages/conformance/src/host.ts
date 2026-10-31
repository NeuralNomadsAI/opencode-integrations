// Host wire protocol 1 (spec/host-handshake.md): a reference fake host for
// tests, a raw client, and the listener check (loopback only, token required).

import { createHash, randomBytes, timingSafeEqual } from "node:crypto"
import net from "node:net"
import { networkInterfaces } from "node:os"

import { hostValidator, schemaErrors } from "./schemas.ts"

export type Endpoint = { host: string; port: number; token: string }

export type Capabilities = {
  methods: string[]
  operations: Array<{ name: string; undoable: boolean; network?: boolean }>
  undo: "transactional" | "grouped_best_effort" | "none"
  background: boolean
}

// Canonical JSON (keys sorted, no whitespace) and the capability hash.
export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>
    const keys = Object.keys(record)
      .filter((key) => record[key] !== undefined)
      .sort()
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`
  }
  return JSON.stringify(value)
}

export function capabilityHash(capabilities: Capabilities): string {
  return createHash("sha256").update(canonicalJson(capabilities)).digest("hex")
}

export type FakeHostOptions = {
  token?: string
  // Listen address; anything but 127.0.0.1 is a conformance failure, used by negative tests.
  listen?: string
  requireToken?: boolean
  protocol?: number
  hostVersion?: string
  appVersion?: string
  scriptHandle?: string
  revision?: number
  document?: string
  capabilities?: Capabilities
  // Extra methods, by name; `hello` is built in.
  methods?: Record<string, (params: Record<string, unknown>) => unknown>
  // Rewrites the hello result before it is sent (negative tests).
  helloOverride?: (hello: Record<string, unknown>) => unknown
}

export type FakeHost = {
  endpoint: Endpoint
  scriptHandle: string
  hello: Record<string, unknown>
  requests: Array<{ method: string; authorized: boolean }>
  close(): Promise<void>
}

const DEFAULT_CAPABILITIES: Capabilities = {
  methods: ["hello", "context", "apply"],
  operations: [{ name: "object.rename", undoable: true }],
  undo: "grouped_best_effort",
  background: false,
}

function tokenMatches(expected: string, received: unknown) {
  if (typeof received !== "string") return false
  const a = Buffer.from(expected)
  const b = Buffer.from(received)
  return a.length === b.length && timingSafeEqual(a, b)
}

export async function startFakeHost(options: FakeHostOptions = {}): Promise<FakeHost> {
  const token = options.token ?? randomBytes(32).toString("hex")
  const scriptHandle = options.scriptHandle ?? `fake:${randomBytes(6).toString("hex")}`
  const capabilities = options.capabilities ?? DEFAULT_CAPABILITIES
  const hello: Record<string, unknown> = {
    protocol: options.protocol ?? 1,
    host_version: options.hostVersion ?? "0.1.0",
    app_version: options.appVersion ?? "5.2.0",
    capabilities,
    capability_hash: capabilityHash(capabilities),
    script_handle: scriptHandle,
    revision: options.revision ?? 0,
  }
  if (options.document !== undefined) hello.document = options.document
  const requests: FakeHost["requests"] = []
  const sockets = new Set<net.Socket>()

  const server = net.createServer((socket) => {
    sockets.add(socket)
    socket.on("close", () => sockets.delete(socket))
    let buffer = ""
    socket.setEncoding("utf8")
    socket.on("data", (chunk: string) => {
      buffer += chunk
      const newline = buffer.indexOf("\n")
      if (newline < 0) return
      let request: { id?: unknown; token?: unknown; method?: unknown; params?: unknown }
      try {
        request = JSON.parse(buffer.slice(0, newline))
      } catch {
        socket.end(`${JSON.stringify({ id: "", ok: false, error: { code: "invalid_request" } })}\n`)
        return
      }
      const id = typeof request.id === "string" ? request.id : ""
      const method = typeof request.method === "string" ? request.method : ""
      const authorized = options.requireToken === false || tokenMatches(token, request.token)
      requests.push({ method, authorized })
      const reply = (body: unknown) => socket.end(`${JSON.stringify(body)}\n`)
      if (!authorized) return reply({ id, ok: false, error: { code: "unauthorized" } })
      const params = (typeof request.params === "object" && request.params) || {}
      try {
        if (method === "hello") return reply({ id, ok: true, result: options.helloOverride?.(hello) ?? hello })
        const handler = options.methods?.[method]
        if (!handler) return reply({ id, ok: false, error: { code: "method_not_found" } })
        return reply({ id, ok: true, result: handler(params as Record<string, unknown>) })
      } catch (error) {
        const code = (error as { code?: string }).code ?? "internal"
        return reply({ id, ok: false, error: { code } })
      }
    })
    socket.on("error", () => {})
  })
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject)
    server.listen(0, options.listen ?? "127.0.0.1", () => resolve())
  })
  const address = server.address() as net.AddressInfo
  return {
    endpoint: { host: "127.0.0.1", port: address.port, token },
    scriptHandle,
    hello,
    requests,
    async close() {
      for (const socket of sockets) socket.destroy()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    },
  }
}

// Sends one raw request line and returns the parsed response line, or null
// when the host closes or does not answer within `timeoutMs`.
export async function rawHostRequest(
  address: { host: string; port: number },
  request: unknown,
  timeoutMs = 2_000,
): Promise<unknown> {
  return await new Promise((resolve) => {
    const socket = net.createConnection({ host: address.host, port: address.port })
    let buffer = ""
    let settled = false
    const finish = (value: unknown) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      socket.destroy()
      resolve(value)
    }
    const timer = setTimeout(() => finish(null), timeoutMs)
    socket.setEncoding("utf8")
    socket.on("connect", () => socket.write(`${JSON.stringify(request)}\n`))
    socket.on("data", (chunk: string) => {
      buffer += chunk
      const newline = buffer.indexOf("\n")
      if (newline < 0) return
      try {
        finish(JSON.parse(buffer.slice(0, newline)))
      } catch {
        finish(null)
      }
    })
    socket.on("error", () => finish(null))
    socket.on("end", () => finish(null))
  })
}

async function connects(host: string, port: number, timeoutMs: number): Promise<boolean> {
  return await new Promise((resolve) => {
    const socket = net.createConnection({ host, port })
    const timer = setTimeout(() => {
      socket.destroy()
      resolve(false)
    }, timeoutMs)
    socket.on("connect", () => {
      clearTimeout(timer)
      socket.destroy()
      resolve(true)
    })
    socket.on("error", () => {
      clearTimeout(timer)
      resolve(false)
    })
  })
}

function succeeded(response: unknown) {
  return typeof response === "object" && response !== null && (response as { ok?: unknown }).ok === true
}

export type ListenerReport = { errors: string[]; notes: string[]; hello: unknown }

// The host listens on 127.0.0.1 only and answers nothing without the exact token.
export async function checkHostListener(endpoint: Endpoint, timeoutMs = 2_000): Promise<ListenerReport> {
  const errors: string[] = []
  const notes: string[] = []
  if (endpoint.host !== "127.0.0.1") errors.push(`host: advertised address ${endpoint.host} is not 127.0.0.1`)

  const external = Object.values(networkInterfaces())
    .flat()
    .filter((entry) => entry !== undefined && !entry.internal && entry.family === "IPv4")
    .map((entry) => (entry as { address: string }).address)
  if (external.length === 0) notes.push("host: no non-loopback interface to probe")
  for (const address of external) {
    if (await connects(address, endpoint.port, 500)) {
      errors.push("host: the port is reachable on a non-loopback interface")
      break
    }
  }

  const id = "conformance-probe"
  const target = { host: "127.0.0.1", port: endpoint.port }
  const wrongToken = endpoint.token.startsWith("0") ? `1${endpoint.token.slice(1)}` : `0${endpoint.token.slice(1)}`
  const attempts: Array<[string, unknown]> = [
    ["without a token", { id, method: "hello", params: {} }],
    ["with an empty token", { id, token: "", method: "hello", params: {} }],
    ["with a wrong token", { id, token: wrongToken, method: "hello", params: {} }],
  ]
  for (const [label, request] of attempts) {
    if (succeeded(await rawHostRequest(target, request, timeoutMs))) errors.push(`host: hello succeeds ${label}`)
  }

  const response = await rawHostRequest(target, { id, token: endpoint.token, method: "hello", params: {} }, timeoutMs)
  let hello: unknown = null
  if (!succeeded(response)) errors.push("host: hello with the token fails")
  else {
    errors.push(...schemaErrors("host response", hostValidator("response"), response))
    hello = (response as { result: unknown }).result
    errors.push(...checkHello(hello))
  }
  return { errors, notes, hello }
}

export function checkHello(hello: unknown): string[] {
  const errors = schemaErrors("hello", hostValidator("hello"), hello)
  if (errors.length > 0) return errors
  const value = hello as { capabilities: Capabilities; capability_hash: string }
  if (capabilityHash(value.capabilities) !== value.capability_hash) {
    errors.push("hello: capability_hash is not the sha256 of the canonical capabilities")
  }
  return errors
}
