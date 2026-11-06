import { getValidAuth, type JsonRecord, parseJson } from "./auth.ts"
import { currentSignal } from "./cancellation.ts"

export const GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
export type GraphRequestArgs = {
  path: string
  method?: string
  query?: JsonRecord
  body?: JsonRecord
  force_refresh?: boolean
}
export type GraphRawRequestArgs = Omit<GraphRequestArgs, "body"> & { body?: string | Uint8Array; content_type?: string }
export type GraphRequestResult = { ok: boolean; status: number; method: string; path: string; result: unknown }
export type GraphDownloadResult = {
  ok: boolean
  status: number
  path: string
  contentType: string | null
  contentLength: number | null
  bytes: Uint8Array
}

export function buildGraphUrl(path: string, query?: JsonRecord) {
  const url = new URL(path.startsWith("https://") ? path : `${GRAPH_BASE_URL}${path.startsWith("/") ? "" : "/"}${path}`)
  if (
    url.origin !== "https://graph.microsoft.com" ||
    url.username ||
    url.password ||
    url.hash ||
    !/^\/(v1\.0|beta)\//.test(url.pathname)
  )
    throw new Error("Only Microsoft Graph v1.0 or beta URLs are allowed.")
  for (const [key, value] of Object.entries(query || {})) {
    if (value == null) continue
    if (Array.isArray(value)) for (const item of value) url.searchParams.append(key, String(item))
    else url.searchParams.set(key, String(value))
  }
  return url.toString()
}

function requestSignal() {
  const signal = currentSignal()
  return signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000)
}

async function boundedBytes(response: Response, maxBytes: number) {
  if (Number(response.headers.get("content-length")) > maxBytes) {
    await response.body?.cancel()
    throw new Error("Microsoft Graph response exceeds the size limit.")
  }
  const chunks: Uint8Array[] = []
  let size = 0
  const reader = response.body?.getReader()
  if (!reader) return new Uint8Array()
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > maxBytes) throw new Error("Microsoft Graph response exceeds the size limit.")
      chunks.push(value)
    }
  } finally {
    await reader.cancel()
    reader.releaseLock()
  }
  return Buffer.concat(chunks)
}

async function request(path: string, query: JsonRecord | undefined, init: RequestInit, download = false) {
  // Validate before reading or refreshing credentials.
  const url = buildGraphUrl(path, query)
  let auth = await getValidAuth(false)
  for (let attempt = 0; attempt < 2; attempt++) {
    let response = await fetch(url, {
      ...init,
      redirect: download ? "manual" : "error",
      signal: requestSignal(),
      headers: { ...init.headers, Authorization: `Bearer ${auth.accessToken}` },
    })
    if (download && [301, 302, 303, 307, 308].includes(response.status)) {
      const target = new URL(response.headers.get("location") || "", url)
      await response.body?.cancel()
      if (
        target.protocol !== "https:" ||
        target.port ||
        target.username ||
        target.password ||
        !["sharepoint.com", "1drv.com", "onedrive.com"].some((domain) => target.hostname.endsWith(`.${domain}`))
      )
        throw new Error("Unsafe Microsoft download redirect refused.")
      // Preauthenticated download URLs receive no Graph credentials.
      response = await fetch(target, { redirect: "error", signal: requestSignal() })
    }
    if (response.ok) return response
    await response.body?.cancel()
    if (response.status === 401 && attempt === 0) {
      auth = await getValidAuth(true)
      continue
    }
    throw new Error(`Microsoft Graph request failed (${response.status}). Check scopes, target and tenant policy.`)
  }
  throw new Error("Microsoft Graph request failed after refresh.")
}

export async function executeGraphRequest(args: GraphRequestArgs): Promise<GraphRequestResult> {
  buildGraphUrl(args.path, args.query)
  if (args.force_refresh) await getValidAuth(true)
  const method = (args.method || (args.body ? "POST" : "GET")).toUpperCase()
  const response = await request(args.path, args.query, {
    method,
    ...(args.body !== undefined
      ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(args.body) }
      : {}),
  })
  const result = parseJson(Buffer.from(await boundedBytes(response, 10 * 1024 * 1024)).toString("utf8"))
  return { ok: true, status: response.status, method, path: args.path, result }
}

export async function executeGraphRawRequest(args: GraphRawRequestArgs): Promise<GraphRequestResult> {
  buildGraphUrl(args.path, args.query)
  if (args.force_refresh) await getValidAuth(true)
  const method = (args.method || (args.body ? "PUT" : "GET")).toUpperCase()
  const response = await request(args.path, args.query, {
    method,
    headers: { "Content-Type": args.content_type || "application/octet-stream" },
    body: args.body instanceof Uint8Array ? Buffer.from(args.body) : args.body,
  })
  const result = parseJson(Buffer.from(await boundedBytes(response, 10 * 1024 * 1024)).toString("utf8"))
  return { ok: true, status: response.status, method, path: args.path, result }
}

export async function downloadGraphBytes(args: {
  path: string
  query?: JsonRecord
  force_refresh?: boolean
  max_bytes?: number
}): Promise<GraphDownloadResult> {
  buildGraphUrl(args.path, args.query)
  if (args.force_refresh) await getValidAuth(true)
  const response = await request(args.path, args.query, { method: "GET" }, true)
  const contentLength = response.headers.get("content-length")
  return {
    ok: true,
    status: response.status,
    path: args.path,
    contentType: response.headers.get("content-type"),
    contentLength: contentLength === null ? null : Number(contentLength),
    bytes: await boundedBytes(response, Math.min(args.max_bytes || 25 * 1024 * 1024, 100 * 1024 * 1024)),
  }
}

export async function graphResult(args: GraphRequestArgs) {
  return (await executeGraphRequest(args)).result
}
export function pingGraph(force_refresh = false) {
  return executeGraphRequest({
    path: "/me",
    query: { $select: "id,displayName,userPrincipalName,mail" },
    force_refresh,
  })
}
