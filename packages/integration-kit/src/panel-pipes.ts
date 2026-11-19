import readline from "node:readline"
import { OpenCode } from "@opencode/client"
import { createPanel, type PanelHost, type PanelRequest } from "./panel.ts"
import { discoverService } from "./panel-service.ts"

export function runPanel(config: PanelHost) {
  const send = (value: unknown) => process.stdout.write(`${JSON.stringify(value)}\n`)
  const panel = createPanel(config, {
    send,
    discover: async (registration) => {
      const endpoint = await discoverService(undefined, registration)
      return endpoint && OpenCode.make({ baseUrl: endpoint.url, headers: endpoint.headers, fetch: endpoint.fetch })
    },
  })
  const queues = new Map<string, Promise<void>>()
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
  input.on("line", (line) => {
    if (!line.trim()) return
    try {
      if (Buffer.byteLength(line) > 1024 * 1024) throw new Error("Request too large")
      const request = JSON.parse(line) as PanelRequest
      if (!request.id || !request.channel || !request.method) throw new Error("Invalid request")
      const next = (queues.get(request.channel) ?? Promise.resolve()).then(() => panel.dispatch(request))
      queues.set(request.channel, next)
      void next.finally(() => {
        if (queues.get(request.channel) === next) queues.delete(request.channel)
      })
    } catch {
      send({ error: { message: "Invalid panel request." } })
    }
  })
  input.on("close", () => {
    // Blender can close its pipe while Connect is pending. Detach if that Connect reaches the service.
    const closed = config.consoleEvents ? Promise.allSettled(queues.values()).then(panel.closeAll) : panel.closeAll()
    void closed.finally(() => process.exit(0))
  })
}
