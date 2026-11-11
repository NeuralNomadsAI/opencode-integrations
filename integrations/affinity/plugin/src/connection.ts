// One shared, lazily opened connection to Affinity's embedded MCP server. Calls are serialized;
// a dropped connection is reopened on the next call (never by replaying a call in flight).

import { LegacySseClient, McpError } from "./mcp-client.ts"

export type ConnectionState = "connected" | "none" | "error"
export type McpContent = { type: string; text?: string; data?: string; mimeType?: string }
export type McpToolResult = { content?: McpContent[]; isError?: boolean }

export type ConnectionOptions = {
  urls: URL[]
  version: string
  connectTimeoutMs?: number
  callTimeoutMs?: number
  connect?: typeof LegacySseClient.connect
}

export function createAffinityConnection(options: ConnectionOptions) {
  const connect = options.connect ?? LegacySseClient.connect.bind(LegacySseClient)
  const connectTimeoutMs = options.connectTimeoutMs ?? 3_000
  const callTimeoutMs = options.callTimeoutMs ?? 120_000
  let client: LegacySseClient | undefined
  let opening: Promise<LegacySseClient> | undefined
  let state: ConnectionState = "none"
  let queue: Promise<unknown> = Promise.resolve()
  const listeners = new Set<(state: ConnectionState) => void>()

  const setState = (next: ConnectionState) => {
    if (next === state) return
    state = next
    for (const listener of listeners) listener(next)
  }

  const open = async (): Promise<LegacySseClient> => {
    let failure: McpError | undefined
    for (const url of options.urls) {
      let candidate: LegacySseClient | undefined
      try {
        candidate = await connect(url, {
          timeoutMs: connectTimeoutMs,
          clientInfo: { name: "neuralnomads.affinity", version: options.version },
        })
        // Affinity refuses scripts until its SDK preamble was read in this MCP session.
        const preamble = (await candidate.request(
          "tools/call",
          { name: "read_sdk_documentation_topic", arguments: { filename: "preamble" } },
          { timeoutMs: 15_000 },
        )) as McpToolResult
        if (preamble?.isError) throw new McpError("Affinity refused its SDK preamble.", "protocol")
        candidate.onClose(() => {
          if (client === candidate) {
            client = undefined
            setState("none")
          }
        })
        client = candidate
        setState("connected")
        return candidate
      } catch (error) {
        candidate?.close()
        const mapped = error instanceof McpError ? error : new McpError("Affinity is not reachable.", "unreachable")
        if (!failure || failure.code === "unreachable") failure = mapped
      }
    }
    setState(failure?.code === "unreachable" || !failure ? "none" : "error")
    throw failure ?? new McpError("Affinity is not reachable.", "unreachable")
  }

  const ensure = async () => {
    if (client && !client.closed) return client
    opening ??= open().finally(() => {
      opening = undefined
    })
    return opening
  }

  const call = (name: string, args: Record<string, unknown>, signal?: AbortSignal, timeoutMs = callTimeoutMs) => {
    const run = async () => {
      signal?.throwIfAborted()
      const active = await ensure()
      return (await active.request("tools/call", { name, arguments: args }, { timeoutMs, signal })) as McpToolResult
    }
    const next = queue.then(run, run)
    queue = next.catch(() => {})
    return next
  }

  return {
    state: () => state,
    call,
    onChange: (listener: (state: ConnectionState) => void) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    close: () => {
      client?.close()
      client = undefined
      listeners.clear()
    },
  }
}

export type AffinityConnection = ReturnType<typeof createAffinityConnection>
