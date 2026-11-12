// A `status` output as CodeNomad reads it (spec/README.md, section 2), and the RPC registration it calls.

export type MarkerIdentity = {
  spec: number
  id: string
  family: "native" | "vendor-mcp" | "api"
  host?: { range: string }
}

const SEMVER = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$/
const HOST_STATES = ["absent", "incompatible", "outdated", "ok", "unknown", "unsupported"]
const CONNECTION_STATES = ["none", "available", "attached", "connected", "signed_out", "error"]
const NOTICE = /^[a-z][a-z0-9_]{0,31}$/

// A string that may disclose a path: a separator, a drive letter or a home `~`.
export function looksLikePath(value: string): boolean {
  return /[\\/]/.test(value) || /(?:^|[^\p{L}\p{N}])\p{L}:/u.test(value) || /(?:^|\s)~/.test(value)
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)
const isCount = (value: unknown, max: number) =>
  typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= max

// The fields CodeNomad reads, and the package's own identity: its marker and version.
export function checkStatusOutput(value: unknown, marker: MarkerIdentity, version?: string): string[] {
  if (!isObject(value)) return ["status: not an object"]
  const errors: string[] = []
  if (value.contract !== 1) errors.push("status: contract is not 1")
  const integration = isObject(value.integration) ? value.integration : {}
  if (integration.id !== marker.id || integration.family !== marker.family || integration.spec !== marker.spec)
    errors.push("status: integration id, family and spec differ from the marker")
  if (typeof integration.version !== "string" || !SEMVER.test(integration.version))
    errors.push("status: integration.version is not a semver version")
  else if (version !== undefined && integration.version !== version)
    errors.push(`status: integration.version is not the package version ${version}`)

  const host = value.host
  if (marker.family === "native" && host === undefined) errors.push("status: a native integration reports no host")
  if (host !== undefined) {
    if (!isObject(host) || !HOST_STATES.includes(host.state as string)) errors.push("status: host.state is unknown")
    else {
      if (host.partVersion !== undefined && !SEMVER.test(String(host.partVersion)))
        errors.push("status: host.partVersion is not a semver version")
      if (host.expectedRange !== undefined && marker.host && host.expectedRange !== marker.host.range)
        errors.push("status: host.expectedRange is not the marker's host.range")
      if (host.appVersion !== undefined && (typeof host.appVersion !== "string" || looksLikePath(host.appVersion)))
        errors.push("status: host.appVersion is not display text")
      if (host.action !== undefined && host.action !== "install" && host.action !== "update")
        errors.push("status: host.action is unknown")
    }
  }

  const connection = value.connection
  if (connection !== undefined) {
    if (!isObject(connection) || !CONNECTION_STATES.includes(connection.state as string))
      errors.push("status: connection.state is unknown")
    else {
      if (connection.offers !== undefined && !isCount(connection.offers, 16))
        errors.push("status: connection.offers is not 0..16")
      if (connection.attachedSessions !== undefined && !isCount(connection.attachedSessions, 10_000))
        errors.push("status: connection.attachedSessions is not a count")
    }
  }

  const notices = value.notices
  if (
    notices !== undefined &&
    (!Array.isArray(notices) ||
      notices.length > 8 ||
      new Set(notices).size !== notices.length ||
      !notices.every((notice) => typeof notice === "string" && NOTICE.test(notice)))
  )
    errors.push("status: notices are not a short list of names")
  return errors
}

// CodeNomad calls `status` under the marker's RPC ID; the application part calls `advertise` and `withdraw`.
export function checkRpcRegistration(
  registration: { id: string; methods: string[]; events: string[] },
  marker: MarkerIdentity & { rpc: string },
): string[] {
  const errors: string[] = []
  if (registration.id !== marker.rpc) errors.push(`rpc: registered ID ${registration.id} is not ${marker.rpc}`)
  const native = marker.family === "native"
  for (const method of native ? ["status", "detachHost", "advertise", "withdraw"] : ["status"]) {
    if (!registration.methods.includes(method)) errors.push(`rpc: method ${method} is not registered`)
  }
  for (const event of native ? ["status_changed", "hosts_changed"] : ["status_changed"]) {
    if (!registration.events.includes(event)) errors.push(`rpc: event ${event} is not declared`)
  }
  return errors
}
