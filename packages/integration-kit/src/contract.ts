// RPC status contract (spec/rpc-contract.md). The JSON Schemas below are the
// wire schemas registered with OpenCode; packages/conformance checks that they
// equal spec/schemas/rpc.schema.json.

import type { IntegrationFamily } from "./marker.ts"

// Contract integer carried by `status`; the draft name of the specification.
export const CONTRACT_VERSION = 1
export const SPEC_DRAFT = "0.1"

// Offer lease and renewal (spec/host-handshake.md, "Advertisement").
export const OFFER_LEASE_MS = 15_000
export const OFFER_RENEW_MS = 5_000
// A hello older than this no longer derives ok, outdated or incompatible.
export const LIVE_HELLO_MS = 15_000
export const HELLO_TIMEOUT_MS = 2_000
export const MAX_OFFERS = 16

export const LABEL_MAX = 120
export const DETAIL_MAX = 300
export const APP_VERSION_MAX = 64

// Closed list of notices this contract version defines; readers ignore others.
export const KNOWN_NOTICES = ["legacy_install"] as const
export type Notice = (typeof KNOWN_NOTICES)[number]

export type HostState = "absent" | "incompatible" | "outdated" | "ok" | "unknown" | "unsupported"
export type ConnectionState = "none" | "available" | "attached" | "connected" | "signed_out" | "error"

export type HostStatus = {
  state: HostState
  partVersion?: string
  expectedRange?: string
  appVersion?: string
  action?: "install" | "update"
}

export type ConnectionStatus = { state: ConnectionState; offers?: number; attachedSessions?: number }

export type StatusOutput = {
  contract: typeof CONTRACT_VERSION
  integration: { id: string; version: string; family: IntegrationFamily; spec: 0 }
  host?: HostStatus
  connection?: ConnectionStatus
  notices?: Notice[]
}

export type InstallHostOutput = {
  outcome: "installed" | "updated" | "unchanged" | "refused" | "failed"
  detail?: string
}

export type HostOffer = {
  offerID: string
  label: string
  document?: string
  appVersion?: string
  protocol: "ok" | "incompatible"
}

export type AdvertiseInput = {
  host: "127.0.0.1"
  port: number
  token: string
  script_handle: string
  label: string
  protocol: number
  offer_key: string
}

export type AttachOutcome = "attached" | "busy" | "gone" | "refused"
export type AdvertiseOutcome = "accepted" | "renewed" | "refused"
export type WithdrawOutcome = "withdrawn" | "none"

type Schema = Record<string, unknown>

const SEMVER_PATTERN = "^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?(\\+[0-9A-Za-z.-]+)?$"
// Display text: no path separator, no control character.
const DISPLAY_PATTERN = "^[^/\\\\\\p{Cc}]+$"
const HANDLE_PATTERN = "^[^\\p{Cc}]+$"
const CONTROL_PATTERN = "[\\p{Cc}]"
const OFFER_ID_PATTERN = "^[A-Za-z0-9_-]{8,64}$"
const SESSION_ID_PATTERN = "^[A-Za-z0-9_-]{1,128}$"

const semver = (): Schema => ({ type: "string", maxLength: 64, pattern: SEMVER_PATTERN })
const display = (maxLength: number): Schema => ({ type: "string", minLength: 1, maxLength, pattern: DISPLAY_PATTERN })
const empty = (): Schema => ({ type: "object", additionalProperties: false, properties: {} })
const outcome = (values: string[]): Schema => ({
  type: "object",
  required: ["outcome"],
  properties: { outcome: { enum: values } },
})

export const RPC_SCHEMAS = {
  "status.input": empty(),
  "status.output": {
    type: "object",
    required: ["contract", "integration"],
    properties: {
      contract: { const: 1 },
      integration: {
        type: "object",
        required: ["id", "version", "family", "spec"],
        properties: {
          id: { type: "string", pattern: "^[a-z][a-z0-9-]{0,31}$" },
          version: semver(),
          family: { enum: ["native", "vendor-mcp", "api"] },
          spec: { const: 0 },
        },
      },
      host: {
        type: "object",
        required: ["state"],
        properties: {
          state: { enum: ["absent", "incompatible", "outdated", "ok", "unknown", "unsupported"] },
          partVersion: semver(),
          expectedRange: { type: "string", minLength: 1, maxLength: 64, pattern: "^[0-9xX*^~<>=|. -]+$" },
          appVersion: display(APP_VERSION_MAX),
          action: { enum: ["install", "update"] },
        },
      },
      connection: {
        type: "object",
        required: ["state"],
        properties: {
          state: { enum: ["none", "available", "attached", "connected", "signed_out", "error"] },
          offers: { type: "integer", minimum: 0, maximum: MAX_OFFERS },
          attachedSessions: { type: "integer", minimum: 0, maximum: 10000 },
        },
      },
      notices: {
        type: "array",
        maxItems: 8,
        uniqueItems: true,
        items: { type: "string", pattern: "^[a-z][a-z0-9_]{0,31}$" },
      },
    },
  },
  "installHost.input": { type: "object", additionalProperties: false, properties: { target: { const: "default" } } },
  "installHost.output": {
    type: "object",
    required: ["outcome"],
    properties: {
      outcome: { enum: ["installed", "updated", "unchanged", "refused", "failed"] },
      detail: display(DETAIL_MAX),
    },
  },
  "hosts.input": empty(),
  "hosts.output": {
    type: "object",
    required: ["offers"],
    properties: {
      offers: {
        type: "array",
        maxItems: MAX_OFFERS,
        items: {
          type: "object",
          required: ["offerID", "label", "protocol"],
          properties: {
            offerID: { type: "string", pattern: OFFER_ID_PATTERN },
            label: display(LABEL_MAX),
            document: display(LABEL_MAX),
            appVersion: display(APP_VERSION_MAX),
            protocol: { enum: ["ok", "incompatible"] },
          },
        },
      },
    },
  },
  "attachHost.input": {
    type: "object",
    additionalProperties: false,
    required: ["offerID", "sessionID"],
    properties: {
      offerID: { type: "string", pattern: OFFER_ID_PATTERN },
      sessionID: { type: "string", pattern: SESSION_ID_PATTERN },
    },
  },
  "attachHost.output": outcome(["attached", "busy", "gone", "refused"]),
  "detachHost.input": {
    type: "object",
    additionalProperties: false,
    required: ["sessionID"],
    properties: { sessionID: { type: "string", pattern: SESSION_ID_PATTERN } },
  },
  "detachHost.output": outcome(["detached", "none"]),
  "advertise.output": outcome(["accepted", "renewed", "refused"]),
  "withdraw.output": outcome(["withdrawn", "none"]),
  "status_changed.event": empty(),
  "hosts_changed.event": empty(),
} as const satisfies Record<string, Schema>

// advertise and withdraw carry the host token: OpenCode accepts any object and
// the plugin validates it (parseAdvertiseInput, parseWithdrawInput) so that a
// refusal never reveals its cause.
const OPAQUE_INPUT: Schema = { type: "object" }

export const NATIVE_METHODS = ["status", "installHost", "hosts", "attachHost", "detachHost", "advertise", "withdraw"]

// Portable RPC definition for `ctx.rpc.register`; `status` only for vendor-mcp and api.
export function rpcDefinition(rpcID: string, family: IntegrationFamily) {
  const schemas: Record<string, Schema> = RPC_SCHEMAS
  const schema = (name: string): Schema => structuredClone(schemas[name] ?? OPAQUE_INPUT)
  const io = (method: string, input?: Schema) => ({
    input: input ? structuredClone(input) : schema(`${method}.input`),
    output: schema(`${method}.output`),
  })
  const event = (name: string) => ({ schema: { ...schema(`${name}.event`), type: "object" as const } })
  const methods: Record<string, { input: Schema; output: Schema }> = { status: io("status") }
  const events: Record<string, { schema: Schema & { type: "object" } }> = { status_changed: event("status_changed") }
  if (family === "native") {
    methods.installHost = io("installHost")
    methods.hosts = io("hosts")
    methods.attachHost = io("attachHost")
    methods.detachHost = io("detachHost")
    methods.advertise = io("advertise", OPAQUE_INPUT)
    methods.withdraw = io("withdraw", OPAQUE_INPUT)
    events.hosts_changed = event("hosts_changed")
  }
  return { id: rpcID, methods, events }
}

const TOKEN = /^[0-9a-f]{64}$/
const OFFER_KEY = /^[A-Za-z0-9._:-]{1,64}$/
const DISPLAY = new RegExp(DISPLAY_PATTERN, "u")
export const HANDLE = new RegExp(HANDLE_PATTERN, "u")

function hasOnly(value: object, keys: string[]) {
  return Object.keys(value).every((key) => keys.includes(key))
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function isInteger(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= min && value <= max
}

function isText(value: unknown, pattern: RegExp, max: number): value is string {
  return typeof value === "string" && value.length >= 1 && [...value].length <= max && pattern.test(value)
}

const ADVERTISE_KEYS = ["host", "port", "token", "script_handle", "label", "protocol", "offer_key"]

// Validates an advertise input against spec/schemas/rpc.schema.json#/$defs/advertise.input.
export function parseAdvertiseInput(value: unknown): AdvertiseInput | null {
  if (!isObject(value) || !hasOnly(value, ADVERTISE_KEYS)) return null
  if (value.host !== "127.0.0.1") return null
  if (!isInteger(value.port, 1, 65535) || !isInteger(value.protocol, 1, 1000)) return null
  if (typeof value.token !== "string" || !TOKEN.test(value.token)) return null
  if (typeof value.offer_key !== "string" || !OFFER_KEY.test(value.offer_key)) return null
  if (!isText(value.script_handle, HANDLE, 200) || !isText(value.label, DISPLAY, LABEL_MAX)) return null
  return value as AdvertiseInput
}

// Validates a withdraw input against spec/schemas/rpc.schema.json#/$defs/withdraw.input.
export function parseWithdrawInput(value: unknown): { offer_key: string; token: string } | null {
  if (!isObject(value) || !hasOnly(value, ["offer_key", "token"])) return null
  if (typeof value.token !== "string" || !TOKEN.test(value.token)) return null
  if (typeof value.offer_key !== "string" || !OFFER_KEY.test(value.offer_key)) return null
  return { offer_key: value.offer_key, token: value.token }
}

// Reduces any text to a display label: last path segment, no control
// character, at most `max` code points; null when nothing is left.
export function displayLabel(value: unknown, max = LABEL_MAX): string | null {
  if (typeof value !== "string") return null
  const segment = value.split(/[/\\]/).filter(Boolean).at(-1) ?? ""
  const clean = segment.replace(new RegExp(CONTROL_PATTERN, "gu"), "").trim()
  const bounded = [...clean].slice(0, max).join("").trim()
  return bounded.length > 0 ? bounded : null
}

// Bounded `detail` of installHost: no path separator, at most 300 code points.
export function boundedDetail(value: string): string | undefined {
  const clean = value.replace(/[/\\]/g, " ").replace(new RegExp(CONTROL_PATTERN, "gu"), " ").replace(/\s+/g, " ").trim()
  const bounded = [...clean].slice(0, DETAIL_MAX).join("").trim()
  return bounded.length > 0 ? bounded : undefined
}

export function isDisplayText(value: unknown, max: number): value is string {
  return isText(value, DISPLAY, max)
}
