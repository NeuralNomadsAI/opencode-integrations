import readline from "node:readline"
import { OpenCode } from "@opencode/client"
import { createPanel, type PanelHost, type PanelRequest } from "./panel.ts"
import { discoverService } from "./panel-service.ts"

export function runPanel(config: PanelHost) {
  delete process.env.BUN_BE_BUN
  const send = (value: unknown) => process.stdout.write(`${JSON.stringify(value)}\n`)
  const panel = createPanel(config, {
    send,
    discover: async (registration) => {
      const endpoint = await discoverService(undefined, registration)
      return endpoint && OpenCode.make({ baseUrl: endpoint.url, headers: endpoint.headers, fetch: endpoint.fetch })
    },
  })
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
  input.on("line", (line) => {
    if (!line.trim()) return
    try {
      if (Buffer.byteLength(line) > 1024 * 1024) throw new Error("Request too large")
      const request = JSON.parse(line) as PanelRequest
      if (!request.id || !request.channel || !request.method) throw new Error("Invalid request")
      void panel.dispatch(request)
    } catch {
      send({ error: { message: "Invalid panel request." } })
    }
  })
  input.on("close", () => {
    void panel.closeAll().finally(() => process.exit(0))
  })
}
