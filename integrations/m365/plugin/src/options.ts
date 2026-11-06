import { AsyncLocalStorage } from "node:async_hooks"

export type AuthOptions = { clientId: string; tenantId: string; scopes: string[] }
const current = new AsyncLocalStorage<AuthOptions>()
export const withAuthOptions = <T>(options: AuthOptions, run: () => T): T => current.run(options, run)
export const authOptions = () => current.getStore() ?? readOptions()

export function readOptions(options: Record<string, unknown> = {}, env = process.env): AuthOptions {
  const clientId = options.clientId ?? env.NEURALNOMADS_M365_CLIENT_ID ?? ""
  const tenantId = options.tenantId ?? env.NEURALNOMADS_M365_TENANT_ID ?? "organizations"
  const scopes = options.scopes ??
    env.NEURALNOMADS_M365_SCOPES?.split(/[\s,]+/) ?? ["offline_access", "openid", "profile", "User.Read"]
  if (
    typeof clientId !== "string" ||
    (clientId !== "" && !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(clientId))
  )
    throw new Error("Invalid Microsoft 365 clientId.")
  if (typeof tenantId !== "string" || !/^[a-z0-9][a-z0-9.-]{0,252}$/i.test(tenantId))
    throw new Error("Invalid Microsoft 365 tenantId.")
  if (
    !Array.isArray(scopes) ||
    !scopes.length ||
    scopes.length > 64 ||
    scopes.some((s) => typeof s !== "string" || !/^[a-z0-9_.:/-]{1,200}$/i.test(s))
  )
    throw new Error("Invalid Microsoft 365 scopes.")
  return { clientId, tenantId, scopes: [...new Set(scopes)] }
}
