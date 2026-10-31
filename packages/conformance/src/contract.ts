// Checks of RPC contract outputs against spec/schemas/rpc.schema.json and the
// rules it cannot express (spec/rpc-contract.md).

import { RPC_DEFINITIONS, rpcValidator, schemaErrors } from "./schemas.ts"

// Draft of the specification these checks validate.
export const SPEC_DRAFT = "0.1"
export const KNOWN_NOTICES = ["legacy_install"]
export const NATIVE_METHODS = ["status", "installHost", "hosts", "attachHost", "detachHost", "advertise", "withdraw"]

export type MarkerIdentity = {
  spec: number
  id: string
  family: "native" | "vendor-mcp" | "api"
  host?: { range: string }
}

type StatusLike = {
  integration?: { id?: unknown; family?: unknown; spec?: unknown; version?: unknown }
  host?: unknown
  connection?: unknown
  notices?: unknown[]
}

// Strict producer check of a `status` output, and its equality with the marker.
export function checkStatusOutput(value: unknown, marker: MarkerIdentity, version?: string): string[] {
  const errors = schemaErrors("status", rpcValidator("status.output", "strict"), value)
  if (errors.length > 0) return errors
  const status = value as StatusLike
  const integration = status.integration ?? {}
  if (integration.id !== marker.id || integration.family !== marker.family || integration.spec !== marker.spec) {
    errors.push("status: integration id, family and spec must equal the marker")
  }
  if (version !== undefined && integration.version !== version) {
    errors.push(`status: integration.version must be the package version ${version}`)
  }
  if (marker.family === "native" && status.host === undefined) errors.push("status: native integrations report host")
  if (marker.family !== "native" && status.host !== undefined)
    errors.push("status: only native integrations report host")
  const host = status.host as { state?: string; action?: string; expectedRange?: string } | undefined
  if (host?.state === "unknown" && host.action !== undefined)
    errors.push("status: host.state unknown never proposes an action")
  if (host?.state === "ok" && host.action !== undefined) errors.push("status: host.state ok proposes no action")
  if (host && marker.host && host.expectedRange !== undefined && host.expectedRange !== marker.host.range) {
    errors.push("status: host.expectedRange must be the marker's host.range")
  }
  for (const notice of status.notices ?? []) {
    if (!KNOWN_NOTICES.includes(notice as string)) errors.push(`status: unknown notice ${String(notice)}`)
  }
  return errors
}

// Strict producer check of any other method output or event payload.
export function checkRpcOutput(method: string, value: unknown): string[] {
  if (!RPC_DEFINITIONS.includes(`${method}.output`)) return [`${method}: not defined by the contract`]
  return schemaErrors(method, rpcValidator(`${method}.output`, "strict"), value)
}

export function checkEventPayload(name: string, value: unknown): string[] {
  if (!RPC_DEFINITIONS.includes(`${name}.event`)) return [`event ${name}: not defined by the contract`]
  return schemaErrors(`event ${name}`, rpcValidator(`${name}.event`, "strict"), value)
}

// Registered RPC definition of a plugin: its ID and the methods it serves.
export function checkRpcRegistration(
  registration: { id: string; methods: string[]; events: string[] },
  marker: MarkerIdentity & { rpc: string },
): string[] {
  const errors: string[] = []
  if (registration.id !== marker.rpc) errors.push(`rpc: registered ID ${registration.id} must be ${marker.rpc}`)
  const required = marker.family === "native" ? NATIVE_METHODS : ["status"]
  for (const method of required) {
    if (!registration.methods.includes(method)) errors.push(`rpc: method ${method} is not registered`)
  }
  for (const event of marker.family === "native" ? ["status_changed", "hosts_changed"] : ["status_changed"]) {
    if (!registration.events.includes(event)) errors.push(`rpc: event ${event} is not declared`)
  }
  return errors
}
