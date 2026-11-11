// In-process stand-in for Affinity's embedded MCP server: legacy HTTP+SSE transport,
// protocol 2025-11-25 only, scripts refused until the preamble topic was read.

import { createServer, type IncomingMessage, type ServerResponse } from "node:http"
import type { AddressInfo } from "node:net"

export type FakeOptions = {
  protocols?: string[]
  foreignEndpoint?: boolean
  onScript?: (script: string) => { text?: string; isError?: boolean }
}

export async function startFakeAffinity(options: FakeOptions = {}) {
  const protocols = options.protocols ?? ["2025-11-25"]
  const streams = new Map<string, ServerResponse>()
  const preambleRead = new Set<string>()
  const calls: { session: string; method: string; params: unknown }[] = []
  let serial = 0

  const send = (session: string, message: unknown) => {
    streams.get(session)?.write(`event: message\ndata: ${JSON.stringify(message)}\n\n`)
  }

  const handle = (session: string, message: { id?: number; method?: string; params?: Record<string, unknown> }) => {
    if (message.method === undefined) return
    calls.push({ session, method: message.method, params: message.params })
    if (message.id === undefined) return
    const reply = (result: unknown) => send(session, { jsonrpc: "2.0", id: message.id, result })
    const fail = (code: number, text: string) =>
      send(session, { jsonrpc: "2.0", id: message.id, error: { code, message: text } })
    if (message.method === "initialize") {
      const version = String(message.params?.protocolVersion)
      if (!protocols.includes(version)) return fail(-32602, "Unsupported protocol version")
      return reply({
        protocolVersion: version,
        serverInfo: { name: "Affinity", version: "1.0.0" },
        capabilities: { tools: {} },
      })
    }
    if (message.method !== "tools/call") return fail(-32601, "Method not found")
    const name = String(message.params?.name)
    const args = (message.params?.arguments ?? {}) as Record<string, unknown>
    const text = (value: string, isError = false) => reply({ isError, content: [{ type: "text", text: value }] })
    switch (name) {
      case "read_sdk_documentation_topic":
        if (args.filename === "preamble") preambleRead.add(session)
        else if (!preambleRead.has(session))
          return text("The preamble documentation topic has not yet been read.", true)
        return text(`topic:${String(args.filename)}:${"x".repeat(args.filename === "big.js" ? 70_000 : 0)}`)
      case "list_sdk_documentation":
        return text("preamble, document.js, examples/makeGrid.js")
      case "search_sdk_hints":
        return text(`hint for ${String(args.prompt)}`)
      case "list_library_scripts":
        return text("Grid, Stars")
      case "read_library_script":
        return text(`script ${String(args.title)}`)
      case "execute_script": {
        if (!preambleRead.has(session)) return text("The preamble documentation topic has not yet been read.", true)
        const script = String(args.script)
        if (script.includes("Document.current; console.log(d ?")) return text("E9E73C76-340C-4637-8DC6-1AD5201AB6A5\n")
        if (script.includes("const LIMITS")) return text('{"app":"Affinity 3.3","documents":[]}\n')
        const result = options.onScript?.(script) ?? { text: "ok\n" }
        return text(result.text ?? "", result.isError)
      }
      case "render_spread":
      case "render_selection":
        return reply({ isError: false, content: [{ type: "image", mimeType: "image/jpeg", data: "/9j/AA==" }] })
      default:
        return text(`Unknown tool ${name}`, true)
    }
  }

  const server = createServer((request: IncomingMessage, response: ServerResponse) => {
    const url = new URL(request.url ?? "/", "http://localhost")
    if (request.method === "GET" && url.pathname === "/sse") {
      const session = `s${++serial}`
      response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" })
      const endpoint = options.foreignEndpoint ? "http://example.invalid/message" : `/message?sessionId=${session}`
      response.write(`event: endpoint\ndata: ${endpoint}\n\n`)
      streams.set(session, response)
      request.on("close", () => streams.delete(session))
      return
    }
    if (request.method === "POST" && url.pathname === "/message") {
      const session = url.searchParams.get("sessionId") ?? ""
      let body = ""
      request.on("data", (chunk) => {
        body += chunk
      })
      request.on("end", () => {
        if (!streams.has(session)) {
          response.writeHead(404, { "Content-Type": "application/json" }).end("{}")
          return
        }
        response.writeHead(202).end()
        handle(session, JSON.parse(body))
      })
      return
    }
    response.writeHead(404, { "Content-Type": "application/json" }).end("{}")
  })
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
  const port = (server.address() as AddressInfo).port
  return {
    url: new URL(`http://127.0.0.1:${port}/sse`),
    calls,
    openConnections: () => new Promise<number>((resolve) => server.getConnections((_error, count) => resolve(count))),
    dropStreams: () => {
      for (const stream of streams.values()) stream.end()
      streams.clear()
    },
    close: () =>
      new Promise<void>((resolve) => {
        for (const stream of streams.values()) stream.end()
        server.closeAllConnections()
        server.close(() => resolve())
      }),
  }
}
