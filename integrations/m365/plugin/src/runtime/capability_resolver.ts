import { getAuthStatus } from "./auth.ts"

export function resolveCapabilities() {
  const authStatus = getAuthStatus()

  return {
    authenticated: authStatus.authenticated,
    pending_auth: authStatus.pending_auth,
    scopes: authStatus.scope_list,
  }
}
