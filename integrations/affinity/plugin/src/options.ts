import { isLoopback } from "./mcp-client.ts"

// Affinity's embedded MCP server listens on port 6767 of the IPv6 loopback by default.
export const DEFAULT_URLS = ["http://[::1]:6767/sse", "http://127.0.0.1:6767/sse"]

export type AffinityOptions = { urls: URL[]; callTimeoutMs: number }

export function readOptions(options: Record<string, unknown> = {}, env = process.env): AffinityOptions {
  const raw = options.url ?? env.NEURALNOMADS_AFFINITY_URL
  if (raw !== undefined && (typeof raw !== "string" || raw.length === 0 || raw.length > 200))
    throw new Error("Affinity url must be a nonempty string.")
  let urls = DEFAULT_URLS.map((url) => new URL(url))
  if (typeof raw === "string") {
    let url: URL
    try {
      url = new URL(raw)
    } catch {
      throw new Error("Affinity url is not a valid URL.")
    }
    if (url.protocol !== "http:" || !isLoopback(url) || url.username || url.password)
      throw new Error("Affinity url must be a loopback http URL.")
    urls = [url]
  }
  const timeout = options.callTimeoutMs ?? 120_000
  if (typeof timeout !== "number" || !Number.isInteger(timeout) || timeout < 1_000 || timeout > 3_600_000)
    throw new Error("Affinity callTimeoutMs must be an integer between 1000 and 3600000.")
  return { urls, callTimeoutMs: timeout }
}
