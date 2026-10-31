// No secret in RPC outputs or events (spec/rpc-contract.md, rule RPC-6): a fake
// host advertises known sentinel values, the native contract runs end to end,
// and no output except hello (plugin to host) nor any event contains one.

import { randomBytes } from "node:crypto"

import { checkEventPayload, checkRpcOutput } from "./contract.ts"
import { startFakeHost } from "./host.ts"

export type ContractTarget = {
  call(method: string, input: unknown): Promise<unknown>
  // Events emitted by the plugin, appended as they happen.
  events: Array<{ name: string; data: unknown }>
}

export type SentinelReport = { errors: string[]; transcript: Array<{ step: string; output: unknown }> }

export function findSentinels(text: string, sentinels: { strings: string[]; port: number }): string[] {
  const found = sentinels.strings.filter((value) => text.includes(value))
  if (new RegExp(`(^|\\D)${sentinels.port}(\\D|$)`).test(text)) found.push(`port ${sentinels.port}`)
  return found
}

export async function runSentinelScenario(
  target: ContractTarget,
  options: { sessionID?: string; protocol?: number; hostVersion?: string } = {},
): Promise<SentinelReport> {
  const marker = randomBytes(6).toString("hex")
  const host = await startFakeHost({
    scriptHandle: `SENTINEL-HANDLE-${marker}`,
    document: "sentinel.blend",
    protocol: options.protocol ?? 1,
    hostVersion: options.hostVersion ?? "0.1.0",
  })
  const offerKey = `sentinel-key-${marker}`
  const sentinels = { strings: [host.endpoint.token, host.scriptHandle, offerKey], port: host.endpoint.port }
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

  try {
    const advertised = await step("advertise", "advertise", {
      host: "127.0.0.1",
      port: host.endpoint.port,
      token: host.endpoint.token,
      script_handle: host.scriptHandle,
      label: "Sentinel scene",
      protocol: options.protocol ?? 1,
      offer_key: offerKey,
    })
    if (advertised.outcome !== "accepted")
      errors.push(`advertise: expected accepted, got ${String(advertised.outcome)}`)
    await step("renew", "advertise", {
      host: "127.0.0.1",
      port: host.endpoint.port,
      token: host.endpoint.token,
      script_handle: host.scriptHandle,
      label: "Sentinel scene",
      protocol: options.protocol ?? 1,
      offer_key: offerKey,
    })
    const listed = (await step("hosts", "hosts", {})) as { offers?: Array<{ offerID: string }> }
    const offerID = listed.offers?.[0]?.offerID
    if (!offerID) errors.push("hosts: the advertised offer is not listed")
    else {
      const attached = await step("attachHost", "attachHost", { offerID, sessionID })
      if (attached.outcome !== "attached") errors.push(`attachHost: expected attached, got ${String(attached.outcome)}`)
    }
    await step("status", "status", {})
    await step("detachHost", "detachHost", { sessionID })
    const withdrawn = await step("withdraw", "withdraw", { offer_key: offerKey, token: host.endpoint.token })
    if (withdrawn.outcome !== "withdrawn") errors.push(`withdraw: expected withdrawn, got ${String(withdrawn.outcome)}`)
    await step("hosts after withdraw", "hosts", {})
  } finally {
    await host.close()
  }

  for (const entry of transcript) {
    for (const found of findSentinels(JSON.stringify(entry.output), sentinels)) {
      errors.push(`${entry.step}: the output contains the sentinel ${found === host.endpoint.token ? "token" : found}`)
    }
  }
  for (const event of target.events) {
    errors.push(...checkEventPayload(event.name, event.data))
    for (const found of findSentinels(`${event.name} ${JSON.stringify(event.data)}`, sentinels)) {
      errors.push(`event ${event.name}: contains the sentinel ${found === host.endpoint.token ? "token" : found}`)
    }
  }
  if (!host.requests.some((request) => request.method === "hello" && request.authorized)) {
    errors.push("advertise: the plugin never sent hello to the host")
  }
  return { errors, transcript }
}
