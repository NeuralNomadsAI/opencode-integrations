// Handlers of the RPC status contract for `ctx.rpc.register`
// (spec/rpc-contract.md). Every output is bounded and carries no token, port,
// script handle, offer key, path, URL or secret.

import {
  boundedDetail,
  CONTRACT_VERSION,
  type ConnectionStatus,
  type HostStatus,
  type InstallHostOutput,
  KNOWN_NOTICES,
  type Notice,
  rpcDefinition,
  type StatusOutput,
} from "./contract.ts"
import { deriveHostState, type HostInspection, hostUnsupported, isWslDaemon } from "./host-state.ts"
import { type IntegrationMarker, SPEC_VERSION } from "./marker.ts"
import type { OfferRegistry } from "./offer-registry.ts"

export type IntegrationRpcOptions = {
  marker: IntegrationMarker
  // Package version of the integration.
  version: string
  // native: the shared offer registry.
  registry?: OfferRegistry
  // native: read-only inspection of the application's add-on folder.
  inspectHost?: () => Promise<HostInspection>
  // native: installs the embedded host part; called only by installHost.
  installHost?: () => Promise<InstallHostOutput>
  // native: the daemon runs in WSL (defaults to isWslDaemon()).
  wsl?: () => boolean
  // vendor-mcp and api: reachability of the server or the signed-in account.
  connection?: () => Promise<ConnectionStatus>
  notices?: () => Promise<Notice[]>
}

export type RpcEventSink = { events: { emit: (name: string, data: Record<string, never>) => Promise<void> } }

const WSL_DETAIL = "The OpenCode daemon runs in WSL; install the application part from the Windows side."

export function createIntegrationRpc(options: IntegrationRpcOptions) {
  const { marker } = options
  if (marker.spec !== SPEC_VERSION) throw new Error("Unsupported marker spec.")
  const native = marker.family === "native"
  if (native && (!options.registry || !marker.host))
    throw new Error("A native integration needs a host and a registry.")
  const registry = options.registry
  const wsl = options.wsl ?? (() => isWslDaemon())

  const nativeUnsupported = () => hostUnsupported({ wsl: wsl(), validatedOffer: (registry?.counts().offers ?? 0) > 0 })

  const hostStatus = async (): Promise<HostStatus> => {
    const unsupported = nativeUnsupported()
    const inspection: HostInspection =
      unsupported || !options.inspectHost ? { kind: "not-inspected" } : await options.inspectHost()
    return deriveHostState({
      unsupported,
      live: registry?.liveHellos() ?? [],
      inspection,
      range: marker.host?.range ?? "*",
    })
  }

  const status = async (): Promise<StatusOutput> => {
    const output: StatusOutput = {
      contract: CONTRACT_VERSION,
      integration: { id: marker.id, version: options.version, family: marker.family, spec: SPEC_VERSION },
    }
    if (native && registry) {
      output.host = await hostStatus()
      const { offers, attachedSessions } = registry.counts()
      output.connection = {
        state: attachedSessions > 0 ? "attached" : offers > 0 ? "available" : "none",
        offers,
        attachedSessions,
      }
    } else if (options.connection) {
      output.connection = await options.connection()
    }
    const notices = [...new Set(await (options.notices?.() ?? Promise.resolve([])))].filter((notice) =>
      (KNOWN_NOTICES as readonly string[]).includes(notice),
    )
    if (notices.length > 0) output.notices = notices
    return output
  }

  const installHost = async (): Promise<InstallHostOutput> => {
    if (wsl()) return { outcome: "refused", detail: WSL_DETAIL }
    if (!options.installHost) return { outcome: "refused" }
    let result: InstallHostOutput
    try {
      result = await options.installHost()
    } catch {
      return { outcome: "failed" }
    }
    const detail = result.detail === undefined ? undefined : boundedDetail(result.detail)
    return detail === undefined ? { outcome: result.outcome } : { outcome: result.outcome, detail }
  }

  const handlers: Record<string, (input: unknown) => Promise<unknown>> = { status: () => status() }
  if (native && registry) {
    handlers.installHost = () => installHost()
    handlers.hosts = async () => registry.hosts()
    handlers.attachHost = async (input) => {
      const { offerID, sessionID } = input as { offerID: string; sessionID: string }
      return registry.attach(offerID, sessionID)
    }
    handlers.detachHost = async (input) => registry.detach((input as { sessionID: string }).sessionID)
    handlers.advertise = (input) => registry.advertise(input)
    handlers.withdraw = async (input) => registry.withdraw(input)
  }

  // Relays registry changes as payload-free events and expires leases; returns the cleanup.
  const connect = (registration: RpcEventSink, sweepIntervalMs = 1_000) => {
    if (!registry) return () => {}
    const unsubscribe = registry.onChange((change) => {
      void registration.events.emit(change === "hosts" ? "hosts_changed" : "status_changed", {}).catch(() => {})
    })
    const timer = setInterval(() => registry.sweep(), sweepIntervalMs)
    timer.unref?.()
    return () => {
      unsubscribe()
      clearInterval(timer)
    }
  }

  return { definition: rpcDefinition(marker.rpc, marker.family), handlers, status, connect }
}
