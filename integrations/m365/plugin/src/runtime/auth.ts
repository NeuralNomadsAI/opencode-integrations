import { authOptions } from "../options.ts"
import { readState, removeState, writeState } from "../state.ts"
import { currentSignal, sleep, throwIfCancelled } from "./cancellation.ts"

export const DEFAULT_APP_NAME = "OpenCode for Microsoft 365"
export type JsonRecord = Record<string, unknown>
export type StoredAuth = {
  clientId: string
  tenantId: string
  scope: string
  tokenType: string
  accessToken: string
  refreshToken: string | null
  expiresAt: number
}
export type PendingAuth = {
  clientId: string
  tenantId: string
  scope: string
  deviceCode: string
  userCode: string
  verificationUri: string
  verificationUriComplete: string | null
  expiresAt: number
  intervalSeconds: number
  message: string | null
}
export const isRecord = (value: unknown): value is JsonRecord =>
  typeof value === "object" && value !== null && !Array.isArray(value)
export function parseJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}
export const getScopeList = (value?: string | null): string[] => [
  ...new Set((value || "").split(/[\s,]+/).filter(Boolean)),
]
export const mergeScopes = (...scopes: (string[] | undefined | null)[]) => [...new Set(scopes.flatMap((s) => s || []))]
export const buildScopeString = (scopes: string[]) => mergeScopes(scopes).join(" ")
export const hasAllScopes = (available: string[], required: string[]) =>
  required.every((s) => available.some((a) => a.toLowerCase() === s.toLowerCase()))
export const getAvailableScopeList = (auth?: Pick<StoredAuth, "scope" | "accessToken"> | null) =>
  getScopeList(auth?.scope)

export function getClientId() {
  const clientId = authOptions().clientId
  if (!clientId)
    throw new Error(
      "Microsoft 365 is not configured. Set clientId in plugin options or NEURALNOMADS_M365_CLIENT_ID to your own Entra application ID.",
    )
  return clientId
}
export const getTenantId = () => authOptions().tenantId
export const getScopeString = () => authOptions().scopes.join(" ")
export function loadAuth() {
  const auth = readState<StoredAuth>("auth")
  return auth?.clientId === authOptions().clientId && auth?.tenantId === getTenantId() ? auth : null
}
export function loadPendingAuth() {
  const pending = readState<PendingAuth>("pending")
  return pending?.clientId === authOptions().clientId && pending?.tenantId === getTenantId() ? pending : null
}
export function clearAuth() {
  throwIfCancelled()
  removeState("auth")
}
export function clearPendingAuth() {
  throwIfCancelled()
  removeState("pending")
}

async function postForm(tenant: string, leaf: string, fields: Record<string, string>) {
  const signal = currentSignal()
  const response = await fetch(`https://login.microsoftonline.com/${encodeURIComponent(tenant)}/oauth2/v2.0/${leaf}`, {
    method: "POST",
    redirect: "error",
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000),
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(fields),
  })
  const payload = parseJson(await response.text())
  throwIfCancelled()
  if (!isRecord(payload)) throw new Error("Invalid Microsoft OAuth response.")
  if (!response.ok && typeof payload.error !== "string")
    throw new Error(`Microsoft OAuth request failed (${response.status}).`)
  return payload
}

function storedToken(payload: JsonRecord, previous?: StoredAuth): StoredAuth {
  if (typeof payload.access_token !== "string" || !payload.access_token)
    throw new Error("Microsoft OAuth did not return an access token. Check Entra app settings and consent.")
  const lifetime = Number(payload.expires_in)
  if (!Number.isFinite(lifetime) || lifetime <= 0) throw new Error("Invalid Microsoft token expiry.")
  // Only granted scopes count; requesting a scope does not establish consent.
  const scope = typeof payload.scope === "string" ? payload.scope : previous?.scope || ""
  return {
    clientId: getClientId(),
    tenantId: getTenantId(),
    scope,
    tokenType: "Bearer",
    accessToken: payload.access_token,
    refreshToken: typeof payload.refresh_token === "string" ? payload.refresh_token : previous?.refreshToken || null,
    expiresAt: Date.now() + lifetime * 1000,
  }
}

export async function ensureDeviceCodeBootstrap() {
  const clientId = getClientId(),
    tenantId = getTenantId(),
    scope = getScopeString()
  const existing = loadPendingAuth()
  if (existing && existing.expiresAt > Date.now() && existing.scope === scope)
    return { autoStarted: false, pending: existing, scopeList: getScopeList(scope) }
  const payload = await postForm(tenantId, "devicecode", { client_id: clientId, scope })
  if (
    typeof payload.device_code !== "string" ||
    typeof payload.user_code !== "string" ||
    typeof payload.verification_uri !== "string"
  )
    throw new Error("Microsoft device login could not start. Check your app configuration and tenant policy.")
  const uri = new URL(payload.verification_uri)
  if (
    uri.protocol !== "https:" ||
    !["microsoft.com", "www.microsoft.com", "login.microsoftonline.com"].includes(uri.hostname)
  )
    throw new Error("Invalid Microsoft verification URL.")
  const pending: PendingAuth = {
    clientId,
    tenantId,
    scope,
    deviceCode: payload.device_code,
    userCode: payload.user_code,
    verificationUri: uri.toString(),
    verificationUriComplete: null,
    intervalSeconds: Math.max(5, Math.min(Number(payload.interval) || 5, 60)),
    expiresAt: Date.now() + Math.min(Number(payload.expires_in) || 900, 900) * 1000,
    message: null,
  }
  writeState("pending", pending)
  return { autoStarted: true, pending, scopeList: getScopeList(scope) }
}

export async function pollForDeviceToken(args: { timeoutSeconds: number }) {
  getClientId()
  const pending = loadPendingAuth()
  if (!pending || pending.expiresAt <= Date.now())
    throw new Error("Start with m365_auth_device_start to obtain a fresh login code.")
  const deadline = Math.min(pending.expiresAt, Date.now() + Math.min(args.timeoutSeconds, 120) * 1000)
  let interval = pending.intervalSeconds
  while (Date.now() < deadline) {
    const payload = await postForm(pending.tenantId, "token", {
      client_id: pending.clientId,
      grant_type: "urn:ietf:params:oauth:grant-type:device_code",
      device_code: pending.deviceCode,
    })
    if (payload.access_token) {
      if (loadPendingAuth()?.deviceCode !== pending.deviceCode)
        throw new Error("Microsoft 365 login changed while authorization was pending. Retry sign-in.")
      writeState("auth", storedToken(payload))
      clearPendingAuth()
      return getAuthStatus()
    }
    if (payload.error !== "authorization_pending" && payload.error !== "slow_down") {
      clearPendingAuth()
      throw new Error("Microsoft authorization failed or expired. Start a new login and check tenant policy.")
    }
    if (payload.error === "slow_down") interval += 5
    await sleep(Math.min(interval * 1000, Math.max(0, deadline - Date.now())))
  }
  return { ...getAuthStatus(), pending: true }
}

export async function getValidAuth(forceRefresh = false): Promise<StoredAuth> {
  throwIfCancelled()
  getClientId()
  const auth = loadAuth()
  if (!auth?.accessToken) throw new Error("Microsoft 365 requires sign-in. Call m365_auth_device_start.")
  if (!forceRefresh && auth.expiresAt > Date.now() + 120000) return auth
  if (!auth.refreshToken) throw new Error("Microsoft 365 login expired. Sign in again.")
  const payload = await postForm(auth.tenantId, "token", {
    client_id: auth.clientId,
    grant_type: "refresh_token",
    refresh_token: auth.refreshToken,
    scope: auth.scope,
  })
  if (loadAuth()?.accessToken !== auth.accessToken)
    throw new Error("Microsoft 365 login changed during refresh. Retry the operation.")
  if (payload.error) {
    if (payload.error === "invalid_grant") clearAuth()
    throw new Error("Microsoft 365 token refresh failed. Sign in again or check tenant policy.")
  }
  const next = storedToken(payload, auth)
  writeState("auth", next)
  return next
}

export function getAuthStatus() {
  const auth = loadAuth(),
    pending = loadPendingAuth()
  const authenticated = Boolean(auth?.accessToken && (auth.expiresAt > Date.now() || auth.refreshToken))
  return {
    app_name: DEFAULT_APP_NAME,
    configured: Boolean(authOptions().clientId),
    configuration: authOptions().clientId ? "configured" : "client_id_required",
    authenticated,
    signed_in: authenticated,
    scope_list: getAvailableScopeList(auth),
    configured_scopes: authOptions().scopes,
    expires_at: auth?.expiresAt ?? null,
    pending_auth: Boolean(pending && pending.expiresAt > Date.now()),
  }
}
