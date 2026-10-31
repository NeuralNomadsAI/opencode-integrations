// Static `opencodeIntegration` marker of package.json; spec/schemas/marker.schema.json
// is its normative schema.

// Specification integer: 0 for the whole 0.x draft, 1 at the 1.0 freeze.
export const SPEC_VERSION = 0

export const PACKAGE_SCOPE = "@neuralnomads/"
export const PACKAGE_PREFIX = `${PACKAGE_SCOPE}opencode-`
export const PLUGIN_ID_PREFIX = "neuralnomads."

export type IntegrationFamily = "native" | "vendor-mcp" | "api"

export type HostMarker = {
  app: string
  protocol: number
  range: string
  appVersions: string
  payload: string
  sha256: string
}

export type VendorMarker = {
  server: string
  minAppVersion?: string
}

export type IntegrationMarker = {
  spec: typeof SPEC_VERSION
  id: string
  displayName: string
  family: IntegrationFamily
  plugin: string
  rpc: string
  host?: HostMarker
  vendor?: VendorMarker
}

// `@neuralnomads/opencode-<app>` gives `<app>`; any other name gives null.
export function integrationIdFromPackageName(name: string): string | null {
  if (!name.startsWith(PACKAGE_PREFIX)) return null
  const id = name.slice(PACKAGE_PREFIX.length)
  return /^[a-z][a-z0-9-]{0,31}$/.test(id) ? id : null
}

export function pluginIdFor(id: string) {
  return `${PLUGIN_ID_PREFIX}${id}`
}
