// No secret in RPC outputs or events (spec/rpc-contract.md, RPC-6): a fake host
// advertises known sentinel values, the native contract runs end to end, and
// no output nor any event contains one. Only hello, between the plugin and
// its host, carries them. The fake host also puts its script handle in its
// document label and its port in its application version, which a plugin
// must not copy into `hosts`, `status` or exact-session `bindingRead` results.

import { randomBytes } from "node:crypto"

import { checkEventPayload, checkRpcOutput } from "./contract.ts"
import { startFakeHost } from "./host.ts"

export type ContractTarget = {
  call(method: string, input: unknown): Promise<unknown>
  // Events emitted by the plugin, appended as they happen.
  events: Array<{ name: string; data: unknown }>
}

export type SentinelReport = { errors: string[]; transcript: Array<{ step: string; output: unknown }> }

export type Sentinels = { strings: Array<{ name: string; value: string }>; port: number }

// Names of the sentinels found in a value. The port is searched as a number
// and as a digit run in strings, except in `offerID`, which is random.
export function findSentinels(value: unknown, sentinels: Sentinels): string[] {
  const found = new Set<string>()
  const port = new RegExp(`(^|\\D)${sentinels.port}(\\D|$)`)
  const visit = (current: unknown, key: string | null) => {
    if (typeof current === "number" && current === sentinels.port) found.add("port")
    if (typeof current === "string") {
      for (const sentinel of sentinels.strings) if (current.includes(sentinel.value)) found.add(sentinel.name)
      if (key !== "offerID" && port.test(current)) found.add("port")
    }
    if (Array.isArray(current)) for (const item of current) visit(item, key)
    else if (typeof current === "object" && current !== null) {
      for (const [name, item] of Object.entries(current)) {
        visit(name, null)
        visit(item, name)
      }
    }
  }
  visit(value, null)
  return [...found]
}

export async function runSentinelScenario(
  target: ContractTarget,
  options: { sessionID?: string; protocol?: number; hostVersion?: string } = {},
): Promise<SentinelReport> {
  const marker = randomBytes(6).toString("hex")
  const scriptHandle = `SENTINEL-HANDLE-${marker}`
  const host = await startFakeHost({
    scriptHandle,
    protocol: options.protocol ?? 1,
    hostVersion: options.hostVersion ?? "0.1.0",
    helloOverride: (hello, endpoint) => ({
      ...hello,
      document: scriptHandle,
      app_version: `5.2.0 build ${endpoint.port}`,
    }),
  })
  const offerKey = `sentinel-key-${marker}`
  const sentinels: Sentinels = {
    strings: [
      { name: "token", value: host.endpoint.token },
      { name: "script_handle", value: scriptHandle },
      { name: "offer_key", value: offerKey },
    ],
    port: host.endpoint.port,
  }
  const sessionID = options.sessionID ?? `ses_sentinel${marker}`
  const transcript: SentinelReport["transcript"] = []
  const errors: string[] = []
  const step = async (name: string, method: string, input: unknown) => {
    let output: unknown
    try {
      output = await target.call(method, input)
    } catch (error) {
      output = { thrown: String((error as Error)?.message ?? error) }
    }
    transcript.push({ step: name, output })
    if (method !== "status") errors.push(...checkRpcOutput(method, output))
    return output as Record<string, unknown>
  }
  const announcement = {
    host: "127.0.0.1",
    port: host.endpoint.port,
    token: host.endpoint.token,
    script_handle: scriptHandle,
    label: "Sentinel scene",
    protocol: options.protocol ?? 1,
    offer_key: offerKey,
  }

  try {
    const advertised = await step("advertise", "advertise", announcement)
    if (advertised.outcome !== "accepted")
      errors.push(`advertise: expected accepted, got ${String(advertised.outcome)}`)
    await step("renew", "advertise", announcement)
    const listed = (await step("hosts", "hosts", {})) as { offers?: Array<{ offerID: string }> }
    const offerID = listed.offers?.[0]?.offerID
    if (!offerID) errors.push("hosts: the advertised offer is not listed")
    else {
      const before = await step("bindingRead before attach", "bindingRead", { sessionID })
      if (before.state !== "none") errors.push("bindingRead: expected none before attach")
      const attached = await step("attachHost", "attachHost", { offerID, sessionID })
      if (attached.outcome !== "attached") errors.push(`attachHost: expected attached, got ${String(attached.outcome)}`)
      const read = await step("bindingRead attached", "bindingRead", { sessionID })
      const binding = read.binding as { offerID?: unknown; label?: unknown } | undefined
      if (read.state !== "attached" || binding?.offerID !== offerID || binding?.label !== announcement.label) {
        errors.push("bindingRead: expected the exact attached offer and safe label")
      }
    }
    await step("status", "status", {})
    await step("detachHost", "detachHost", { sessionID })
    const after = await step("bindingRead after detach", "bindingRead", { sessionID })
    if (after.state !== "none") errors.push("bindingRead: expected none after detach")
    const withdrawn = await step("withdraw", "withdraw", { offer_key: offerKey, token: host.endpoint.token })
    if (withdrawn.outcome !== "withdrawn") errors.push(`withdraw: expected withdrawn, got ${String(withdrawn.outcome)}`)
    await step("hosts after withdraw", "hosts", {})
  } finally {
    await host.close()
  }

  for (const entry of transcript) {
    for (const found of findSentinels(entry.output, sentinels))
      errors.push(`${entry.step}: the output contains the ${found}`)
  }
  for (const event of target.events) {
    errors.push(...checkEventPayload(event.name, event.data))
    for (const found of findSentinels({ name: event.name, data: event.data }, sentinels)) {
      errors.push(`event ${event.name}: contains the ${found}`)
    }
  }
  if (!host.requests.some((request) => request.method === "hello" && request.authorized)) {
    errors.push("advertise: the plugin never sent hello to the host")
  }
  return { errors, transcript }
}
