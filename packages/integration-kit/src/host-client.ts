// Loopback client of host wire protocol 1 (spec/host-handshake.md, HST-24): one
// newline-delimited JSON request per TCP connection, one response line.

import { randomUUID } from "node:crypto"
import net from "node:net"

import { boundedDetail } from "./contract.ts"

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
  // The host's own explanation of a refusal, as bounded display text; never the endpoint.
  readonly detail: string | undefined
  // The details the host gave with its refusal (`error.data`, a JSON object), such as the current revision.
  readonly data: Record<string, unknown> | undefined
  constructor(code: string, message: string, detail?: string, data?: Record<string, unknown>) {
    super(message)
    this.name = "HostError"
    this.code = code
    this.detail = detail
    this.data = data
  }
}

// What a refusal's message carries of the host's text and data, so that an agent reading the error reads them.
const MESSAGE_TEXT_MAX = 300
const MESSAGE_DATA_MAX = 2_000

function refusalText(message: unknown) {
  if (typeof message !== "string") return undefined
  const clean = message
    .replace(/\p{Cc}/gu, " ")
    .replace(/\s+/g, " ")
    .trim()
  return clean ? [...clean].slice(0, MESSAGE_TEXT_MAX).join("") : undefined
}

function refusalData(data: unknown): Record<string, unknown> | undefined {
  if (typeof data !== "object" || data === null || Array.isArray(data) || !Object.keys(data).length) return undefined
  return data as Record<string, unknown>
}

function refusalMessage(code: string, text: string | undefined, data: Record<string, unknown> | undefined) {
  let message = `Host refused the request (${code})${text ? `: ${text}` : "."}`
  if (data) {
    const json = JSON.stringify(data)
    message += ` ${json.length > MESSAGE_DATA_MAX ? `${json.slice(0, MESSAGE_DATA_MAX)}…` : json}`
  }
  return message
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
      const response = envelope as {
        id?: unknown
        ok?: unknown
        result?: unknown
        error?: { code?: unknown; message?: unknown; data?: unknown }
      }
      if (response.id !== id) return finish(new HostError("invalid_response", "Host returned an invalid envelope."))
      if (response.ok === false) {
        const raw = response.error?.code
        const code = typeof raw === "string" && HOST_ERROR_CODES.includes(raw) ? raw : "internal"
        const detail = typeof response.error?.message === "string" ? boundedDetail(response.error.message) : undefined
        const data = refusalData(response.error?.data)
        const message = refusalMessage(code, refusalText(response.error?.message), data)
        return finish(new HostError(code, message, detail, data))
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
