import { readFile } from "node:fs/promises"
import { Agent, request } from "node:http"
import { homedir } from "node:os"
import { join } from "node:path"
import { Readable } from "node:stream"

const agent = new Agent({ keepAlive: true, proxyEnv: {} })

function serviceURL(value: string) {
  if (!/^http:\/\/(?:127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/|$)/.test(value))
    throw new Error("OpenCode service requires a literal loopback HTTP URL")
  const url = new URL(value)
  if (url.username || url.password || url.hash) throw new Error("Invalid OpenCode service URL")
  return url
}

// A dedicated HTTP agent bypasses environment/system proxies; redirects are never followed.
export function serviceFetch(baseUrl: string): typeof globalThis.fetch {
  const origin = serviceURL(baseUrl).origin
  return async (input, init) => {
    const message = new Request(input, init)
    if (serviceURL(message.url).origin !== origin) throw new Error("OpenCode service origin changed")
    const body = message.body ? Buffer.from(await message.arrayBuffer()) : undefined
    message.signal.throwIfAborted()
    // The private Bun sidecar's HTTP adapter ignores proxyEnv; force direct connections there too.
    if (process.versions.bun) process.env.no_proxy = process.env.NO_PROXY = "*"
    return new Promise((resolve, reject) => {
      const req = request(
        message.url,
        {
          agent,
          method: message.method,
          headers: Object.fromEntries(message.headers),
          signal: message.signal,
        },
        (response) => {
          const status = response.statusCode ?? 500
          if (status >= 300 && status < 400) {
            response.resume()
            reject(new Error("OpenCode service redirect refused"))
            return
          }
          const headers = new Headers()
          for (const [key, value] of Object.entries(response.headers)) {
            for (const item of Array.isArray(value) ? value : value === undefined ? [] : [value])
              headers.append(key, item)
          }
          resolve(
            new Response(
              message.method === "HEAD" || [204, 205, 304].includes(status)
                ? null
                : (Readable.toWeb(response) as ReadableStream<Uint8Array>),
              { status, headers },
            ),
          )
        },
      )
      req.on("error", reject)
      req.once("abort", () => reject(message.signal.reason ?? new Error("OpenCode request aborted")))
      req.end(body)
    })
  }
}

export async function discoverService(
  file = join(process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state"), "opencode", "service.json"),
) {
  try {
    const registration = JSON.parse(await readFile(file, "utf8"))
    if (
      typeof registration.url !== "string" ||
      !Number.isInteger(registration.pid) ||
      registration.pid <= 0 ||
      typeof registration.version !== "string" ||
      !registration.version
    )
      return undefined
    const fetch = serviceFetch(registration.url)
    const headers: Record<string, string> = {}
    if (typeof registration.password === "string")
      headers.authorization = `Basic ${Buffer.from(`opencode:${registration.password}`).toString("base64")}`
    const response = await fetch(`${registration.url.replace(/\/$/, "")}/api/info`, {
      headers,
      signal: AbortSignal.timeout(3000),
    })
    if (!response.ok) return undefined
    const info = await response.json()
    if (info?.pid !== registration.pid || info?.version !== registration.version) return undefined
    return { url: registration.url as string, headers, fetch }
  } catch {
    return undefined
  }
}
