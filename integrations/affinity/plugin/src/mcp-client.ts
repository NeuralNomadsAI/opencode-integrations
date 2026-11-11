// Minimal MCP client for the legacy HTTP+SSE transport that Affinity's embedded server speaks
// (GET <url> opens the event stream, whose `endpoint` event names the POST URL). OpenCode's own
// remote transport is Streamable HTTP only, so the plugin talks to Affinity itself. Loopback only.

import { type ClientRequest, request as httpRequest, type IncomingMessage } from "node:http"

export const PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"] as const
const MAX_FRAME_CHARS = 32 * 1024 * 1024

export type JsonRpcMessage = {
  jsonrpc?: string
  id?: number | string | null
  method?: string
  params?: unknown
  result?: unknown
  error?: { code?: number; message?: string }
}

export class McpError extends Error {
  readonly code: "unreachable" | "protocol" | "closed" | "timeout" | "rpc"
  constructor(message: string, code: McpError["code"]) {
    super(message)
    this.code = code
  }
}

export function isLoopback(url: URL) {
  return ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
}

export function parseEvent(block: string): { type: string; data: string } {
  const lines = block.split("\n")
  return {
    type:
      lines
        .find((line) => line.startsWith("event:"))
        ?.slice(6)
        .trim() ?? "message",
    data: lines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n"),
  }
}

type Pending = { resolve: (value: unknown) => void; reject: (error: Error) => void }

export class LegacySseClient {
  #requests = new Set<ClientRequest>()
  #pending = new Map<number, Pending>()
  #serial = 0
  #endpoint: URL | undefined
  #closed = false
  #closeListeners = new Set<() => void>()
  serverInfo: { name?: string; version?: string } | undefined
  protocolVersion: string | undefined

  readonly url: URL

  private constructor(url: URL) {
    this.url = url
  }

  get closed() {
    return this.#closed
  }

  onClose(listener: () => void) {
    this.#closeListeners.add(listener)
    return () => this.#closeListeners.delete(listener)
  }

  static async connect(
    url: URL,
    options: { timeoutMs: number; clientInfo: { name: string; version: string } },
  ): Promise<LegacySseClient> {
    if (url.protocol !== "http:" || !isLoopback(url) || url.username || url.password)
      throw new McpError("Affinity MCP URL must be a loopback http URL.", "protocol")
    const client = new LegacySseClient(url)
    const timer = setTimeout(() => client.close(), options.timeoutMs)
    try {
      await client.#open()
      clearTimeout(timer)
      await client.#initialize(options)
      return client
    } catch (error) {
      clearTimeout(timer)
      client.close()
      if (error instanceof McpError) throw error
      throw new McpError("Affinity is not reachable.", "unreachable")
    }
  }

  // node:http without a shared agent: every socket belongs to one tracked request, so close()
  // destroys them all (pooled fetch sockets could outlive the client).
  #send(url: URL, method: "GET" | "POST", headers: Record<string, string>, body?: string) {
    return new Promise<IncomingMessage>((resolve, reject) => {
      if (this.#closed) return reject(new McpError("Affinity MCP connection is closed.", "closed"))
      const request = httpRequest(
        {
          hostname: url.hostname.replace(/^\[|\]$/g, ""),
          port: url.port || 80,
          path: `${url.pathname}${url.search}`,
          method,
          headers: body === undefined ? headers : { ...headers, "Content-Length": String(Buffer.byteLength(body)) },
          agent: false,
        },
        resolve,
      )
      this.#requests.add(request)
      request.on("close", () => this.#requests.delete(request))
      request.on("error", () => reject(new McpError("Affinity is not reachable.", "unreachable")))
      request.end(body)
    })
  }

  async #open() {
    const response = await this.#send(this.url, "GET", { Accept: "text/event-stream" })
    if (response.statusCode !== 200 || !String(response.headers["content-type"] ?? "").includes("text/event-stream")) {
      response.destroy()
      throw new McpError(`Affinity MCP answered HTTP ${response.statusCode}.`, "protocol")
    }
    response.setEncoding("utf8")
    await new Promise<void>((resolve, reject) => {
      let buffer = ""
      const fail = (error: McpError) => {
        reject(error)
        response.destroy()
      }
      response.on("data", (chunk: string) => {
        buffer = (buffer + chunk).replace(/\r\n/g, "\n")
        if (buffer.length > MAX_FRAME_CHARS) return fail(new McpError("Affinity MCP frame too large.", "protocol"))
        for (let split = buffer.indexOf("\n\n"); split >= 0; split = buffer.indexOf("\n\n")) {
          const event = parseEvent(buffer.slice(0, split))
          buffer = buffer.slice(split + 2)
          if (event.type === "endpoint") {
            let candidate: URL
            try {
              candidate = new URL(event.data, this.url)
            } catch {
              return fail(new McpError("Invalid MCP endpoint.", "protocol"))
            }
            if (candidate.origin !== this.url.origin)
              return fail(new McpError("Foreign MCP endpoint refused.", "protocol"))
            this.#endpoint = candidate
            resolve()
          } else if (event.data) this.#dispatch(event.data)
        }
      })
      response.on("error", () => {})
      response.on("close", () => {
        reject(new McpError("Affinity closed the MCP stream.", "closed"))
        this.#shutdown()
      })
    })
  }

  #dispatch(data: string) {
    let message: JsonRpcMessage
    try {
      message = JSON.parse(data) as JsonRpcMessage
    } catch {
      return
    }
    if (message.method !== undefined) {
      // Server-to-client request: answer ping, refuse anything else; ignore notifications.
      if (message.id === undefined || message.id === null) return
      const reply: JsonRpcMessage =
        message.method === "ping"
          ? { jsonrpc: "2.0", id: message.id, result: {} }
          : { jsonrpc: "2.0", id: message.id, error: { code: -32601, message: "Method not found" } }
      void this.#post(reply).catch(() => {})
      return
    }
    if (typeof message.id !== "number") return
    const waiter = this.#pending.get(message.id)
    if (!waiter) return
    this.#pending.delete(message.id)
    if (message.error)
      waiter.reject(new McpError(String(message.error.message ?? "Affinity MCP error").slice(0, 500), "rpc"))
    else waiter.resolve(message.result)
  }

  async #post(message: JsonRpcMessage) {
    if (!this.#endpoint || this.#closed) throw new McpError("Affinity MCP connection is closed.", "closed")
    const response = await this.#send(
      this.#endpoint,
      "POST",
      { "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
      JSON.stringify(message),
    ).catch(() => {
      throw new McpError("Affinity MCP request failed.", "closed")
    })
    const text = await new Promise<string>((resolve, reject) => {
      let body = ""
      response.setEncoding("utf8")
      response.on("data", (chunk: string) => {
        body += chunk
        if (body.length > MAX_FRAME_CHARS) response.destroy()
      })
      response.on("end", () => resolve(body))
      response.on("error", () => {})
      response.on("close", () => reject(new McpError("Affinity MCP request failed.", "closed")))
    })
    const status = response.statusCode ?? 0
    if (status < 200 || status >= 300) throw new McpError(`Affinity MCP answered HTTP ${status}.`, "protocol")
    if (String(response.headers["content-type"] ?? "").includes("application/json") && text.trim()) this.#dispatch(text)
  }

  async #initialize(options: { clientInfo: { name: string; version: string } }) {
    let lastError: unknown
    for (const protocolVersion of PROTOCOL_VERSIONS) {
      try {
        const result = (await this.request(
          "initialize",
          { protocolVersion, capabilities: {}, clientInfo: options.clientInfo },
          { timeoutMs: 10_000 },
        )) as { protocolVersion?: string; serverInfo?: { name?: string; version?: string } }
        this.protocolVersion = result?.protocolVersion ?? protocolVersion
        this.serverInfo = result?.serverInfo
        await this.#post({ jsonrpc: "2.0", method: "notifications/initialized" })
        return
      } catch (error) {
        lastError = error
        if (!(error instanceof McpError && error.code === "rpc" && /protocol/i.test(error.message))) throw error
      }
    }
    throw lastError instanceof McpError ? lastError : new McpError("Affinity MCP initialization failed.", "protocol")
  }

  request(method: string, params: unknown, options: { timeoutMs: number; signal?: AbortSignal }): Promise<unknown> {
    if (this.#closed) return Promise.reject(new McpError("Affinity MCP connection is closed.", "closed"))
    const id = ++this.#serial
    return new Promise((resolve, reject) => {
      const finish = () => {
        clearTimeout(timer)
        options.signal?.removeEventListener("abort", onAbort)
        this.#pending.delete(id)
      }
      const cancel = (error: Error) => {
        finish()
        void this.#post({ jsonrpc: "2.0", method: "notifications/cancelled", params: { requestId: id } }).catch(
          () => {},
        )
        reject(error)
      }
      const onAbort = () =>
        cancel(options.signal?.reason instanceof Error ? options.signal.reason : new Error("Aborted"))
      const timer = setTimeout(
        () => cancel(new McpError(`Affinity did not answer ${method} in time.`, "timeout")),
        options.timeoutMs,
      )
      if (options.signal?.aborted) return onAbort()
      options.signal?.addEventListener("abort", onAbort, { once: true })
      this.#pending.set(id, {
        resolve: (value) => {
          finish()
          resolve(value)
        },
        reject: (error) => {
          finish()
          reject(error)
        },
      })
      this.#post({ jsonrpc: "2.0", id, method, params }).catch((error) => {
        finish()
        reject(error instanceof McpError ? error : new McpError("Affinity MCP request failed.", "closed"))
      })
    })
  }

  #shutdown() {
    if (this.#closed) return
    this.#closed = true
    for (const waiter of this.#pending.values())
      waiter.reject(new McpError("Affinity MCP connection is closed.", "closed"))
    this.#pending.clear()
    for (const listener of this.#closeListeners) listener()
  }

  close() {
    this.#shutdown()
    for (const request of this.#requests) request.destroy()
    this.#requests.clear()
  }
}
