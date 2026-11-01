// Per-Location native ownership snapshots, followed by synchronous registry fencing.
// Native session movement/deletion can still race after the last snapshot (spec/rpc-contract.md).
import { isAbsolute } from "node:path"

import {
  BINDING_RPC_TIMEOUT_MS,
  type BindingAuthority,
  type BindingReadOutput,
  parseBindingReadInput,
  parseBindingReadOutput,
} from "./contract.ts"
import type { HostBinding, OfferRegistry } from "./offer-registry.ts"
import { isRecord } from "./validators.ts"

export type NativeSessionReader = {
  location: { directory: string; workspaceID?: string }
  get?: (input: { sessionID: string }) => Promise<unknown>
}

export function createSessionBindings(registry: OfferRegistry, native?: NativeSessionReader) {
  const owns = async (sessionID: string, signal?: AbortSignal): Promise<boolean> => {
    if (!parseBindingReadInput({ sessionID }) || !native?.get || signal?.aborted) return false
    const directory = native.location?.directory
    // The Promise contract returns Location.PublicRef, which omits workspaceID.
    // An explicitly workspace-scoped caller cannot establish that extra identity here.
    if (
      typeof directory !== "string" ||
      !isAbsolute(directory) ||
      /\p{Cc}/u.test(directory) ||
      native.location.workspaceID !== undefined
    )
      return false
    let timer: ReturnType<typeof setTimeout> | undefined
    let abort: (() => void) | undefined
    try {
      const unavailable = new Promise<null>((resolve) => {
        timer = setTimeout(() => resolve(null), BINDING_RPC_TIMEOUT_MS)
        abort = () => resolve(null)
        signal?.addEventListener("abort", abort, { once: true })
      })
      const session = await Promise.race([native.get({ sessionID }), unavailable])
      return (
        !signal?.aborted &&
        isRecord(session) &&
        session.id === sessionID &&
        isRecord(session.location) &&
        session.location.directory === directory &&
        session.location.workspaceID === undefined &&
        native.location.directory === directory &&
        native.location.workspaceID === undefined
      )
    } catch {
      return false
    } finally {
      clearTimeout(timer)
      if (abort) signal?.removeEventListener("abort", abort)
    }
  }

  const snapshot = async (sessionID: string, signal?: AbortSignal): Promise<BindingAuthority | null> => {
    const authority = registry.authority()
    return authority && (await owns(sessionID, signal)) && !signal?.aborted && registry.current(authority)
      ? authority
      : null
  }
  const verify = async (sessionID: string, authority: BindingAuthority, signal?: AbortSignal) =>
    (await owns(sessionID, signal)) && !signal?.aborted && registry.current(authority)

  const read = async (input: unknown, signal?: AbortSignal): Promise<BindingReadOutput> => {
    const parsed = parseBindingReadInput(input)
    const authority = parsed && (await snapshot(parsed.sessionID, signal))
    if (!parsed || !authority || signal?.aborted || !registry.current(authority)) return { state: "unknown" }
    return parseBindingReadOutput(registry.read(parsed.sessionID)) ?? { state: "unknown" }
  }

  const attach = async (offerID: string, sessionID: string, signal?: AbortSignal) => {
    const authority = await snapshot(sessionID, signal)
    return authority && !signal?.aborted && registry.current(authority)
      ? registry.attach(offerID, sessionID)
      : { outcome: "refused" as const }
  }
  const detach = async (sessionID: string, signal?: AbortSignal) => {
    const authority = await snapshot(sessionID, signal)
    if (!authority || signal?.aborted || !registry.current(authority))
      throw new Error("Session ownership is unverifiable.")
    return registry.detach(sessionID)
  }
  const binding = async (
    sessionID: string,
    signal?: AbortSignal,
  ): Promise<{ authority: BindingAuthority; bound: HostBinding }> => {
    const authority = await snapshot(sessionID, signal)
    const bound = authority && !signal?.aborted && registry.current(authority) ? registry.binding(sessionID) : null
    if (!authority || !bound) throw new Error("No verified application is attached to this conversation.")
    return { authority, bound }
  }
  return { snapshot, verify, read, attach, detach, binding }
}

export type SessionBindings = ReturnType<typeof createSessionBindings>
