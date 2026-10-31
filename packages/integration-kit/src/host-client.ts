// Loopback client of host wire protocol 1 (spec/host-handshake.md, HST-24): one
// newline-delimited JSON request per TCP connection, one response line.

import { randomUUID } from "node:crypto"
import net from "node:net"

export const HOST_ADDRESS = "127.0.0.1"
export const MAX_REQUEST_BYTES = 1024 * 1024
export const MAX_RESPONSE_BYTES = 8 * 1024 * 1024
export const DEFAULT_HOST_TIMEOUT_MS = 30_000

export type HostEndpoint = { host: string; port: number; token: string }

// Error codes of spec/schemas/host.schema.json; any other code is reported as `internal`.
export const HOST_ERROR_CODES = [
  "unauthorized",
  "invalid_request",
  "method_not_found",
  "method_disabled",
  "invalid_args",
  "stale_handle",
  "stale_revision",
  "expectation_failed",
  "mode_conflict",
  "busy",
  "quarantined",
  "permission_denied",
  "internal",
]

export class HostError extends Error {
  readonly code: string
  constructor(code: string, message: string) {
    super(message)
    this.name = "HostError"
    this.code = code
  }
}

// Messages never carry the endpoint: port and token stay out of errors and logs.
export async function requestHost(
  endpoint: HostEndpoint,
  method: string,
  params: Record<string, unknown> = {},
  options: { signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<unknown> {
  if (endpoint.host !== HOST_ADDRESS) throw new HostError("invalid_request", "Hosts listen on 127.0.0.1 only.")
  const id = randomUUID()
  const line = `${JSON.stringify({ id, token: endpoint.token, method, params })}\n`
  if (Buffer.byteLength(line) > MAX_REQUEST_BYTES) throw new HostError("invalid_request", "Host request is too large.")
  const timeoutMs = options.timeoutMs ?? DEFAULT_HOST_TIMEOUT_MS

  return await new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: HOST_ADDRESS, port: endpoint.port })
    let buffer = Buffer.alloc(0)
    let settled = false
    const finish = (error: Error | null, result?: unknown) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      options.signal?.removeEventListener("abort", abort)
      socket.destroy()
      if (error) reject(error)
      else resolve(result)
    }
    const abort = () => finish(new HostError("cancelled", "Host request cancelled."))
    const timer = setTimeout(() => finish(new HostError("timeout", "Host request timed out.")), timeoutMs)
    options.signal?.addEventListener("abort", abort, { once: true })
    if (options.signal?.aborted) return abort()

    socket.on("connect", () => socket.write(line))
    socket.on("data", (chunk: Buffer) => {
      buffer = Buffer.concat([buffer, chunk])
      const newline = buffer.indexOf(0x0a)
      if (newline < 0) {
        if (buffer.byteLength > MAX_RESPONSE_BYTES)
          finish(new HostError("invalid_response", "Host response too large."))
        return
      }
      let envelope: unknown
      try {
        envelope = JSON.parse(buffer.subarray(0, newline).toString("utf8"))
      } catch {
        return finish(new HostError("invalid_response", "Host returned malformed JSON."))
      }
      if (typeof envelope !== "object" || envelope === null || Array.isArray(envelope)) {
        return finish(new HostError("invalid_response", "Host returned an invalid envelope."))
      }
      const response = envelope as { id?: unknown; ok?: unknown; result?: unknown; error?: { code?: unknown } }
      if (response.id !== id) return finish(new HostError("invalid_response", "Host returned an invalid envelope."))
      if (response.ok === false) {
        const raw = response.error?.code
        const code = typeof raw === "string" && HOST_ERROR_CODES.includes(raw) ? raw : "internal"
        return finish(new HostError(code, `Host refused the request (${code}).`))
      }
      if (response.ok !== true || !("result" in response)) {
        return finish(new HostError("invalid_response", "Host returned an invalid envelope."))
      }
      finish(null, response.result)
    })
    socket.on("error", () => finish(new HostError("unreachable", "Host is unreachable.")))
    socket.on("end", () => finish(new HostError("unreachable", "Host closed the connection.")))
  })
}
