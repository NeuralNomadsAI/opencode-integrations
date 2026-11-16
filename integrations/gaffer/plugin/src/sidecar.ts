// The in-Gaffer panel's private JSONL client, built into the installed part as sidecar.cjs and run by Node.
import readline from "node:readline"
import { OpenCode } from "@opencode/client"
import { discoverService } from "./service.ts"
import { createPanel, type PanelRequest } from "./sidecar-core.ts"

const send = (value: unknown) => process.stdout.write(`${JSON.stringify(value)}\n`)
const panel = createPanel({
  send,
  discover: async () => {
    const endpoint = await discoverService()
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
  void panel.closeAll().finally(() => process.exit(0))
})
