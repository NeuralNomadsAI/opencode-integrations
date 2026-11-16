// A native integration's conversations and their application: ownership (RPC-12), the attached offer (attached
// by the tool, restored after an OpenCode restart, or automatically the only free compatible offer, FAM-5), host
// calls that say what became of an application that stopped answering (crashed, closed, still running a long
// call, frozen), and the hosts tool. Knows nothing of any application: names and texts come from the integration.

import type { ToolContext } from "@opencode/plugin/promise/tool"

import type { Attachments } from "./attachments.ts"
import { type CrashWatch, type HostProcess, isAlive, readProcess } from "./closed.ts"
import { type BindingAuthority, HELLO_TIMEOUT_MS } from "./contract.ts"
import { HostError, requestHost } from "./host-client.ts"
import type { HostBinding, OfferRegistry } from "./offer-registry.ts"
import type { SessionBindings } from "./session-bindings.ts"

// The status method answers off the application's main thread: no answer within this long means it is frozen.
export const STATUS_TIMEOUT_MS = 3_000
// How often a running call looks for its application's crash report.
export const CRASH_POLL_MS = 2_000
// Ownership snapshots taken again when other conversations or offers moved the shared registry meanwhile.
const OWNERSHIP_ATTEMPTS = 5

// label: the attached offer's label when the ticket was taken (a permission prompt shows it).
export type Ticket = { authority: BindingAuthority; bound: HostBinding; label?: string }

export type HostSessionTexts = {
  // "Blender": the application as messages name it; "blender": the tool prefix.
  appName: string
  app: string
  // The application part ("OpenCode Link") and what it is ("add-on").
  part: string
  partKind: string
  // No offer at all: how to offer one.
  noneOffered: string
  // How to get a part with a missing method ("install OpenCode Link 0.4.8 (blender_install_host) and restart Blender").
  installHint: string
}

export type HostSessionOptions = {
  texts: HostSessionTexts
  registry: OfferRegistry
  bindings: SessionBindings
  attachments: Attachments
  // Attaches the working method to a conversation that uses the application.
  attachMethod: (sessionID: string) => Promise<void>
  crash: CrashWatch
  // Whether the daemon runs in WSL, where the application's process is on Windows (no process diagnosis).
  wsl: () => boolean
  requestTimeoutMs: number
  // A host refusal as the agent reads it (the integration's hints).
  hostError: (error: unknown) => Error
}

export function createHostSession(options: HostSessionOptions) {
  const { texts, registry, bindings, attachments, crash, wsl } = options
  const { appName, app } = texts

  // Whether a conversation other than `sessionID` is attached to the offer: the conversations this daemon recorded
  // (attachments); with one offer, also the registry's count of attached sessions, which are all on it.
  const heldElsewhere = (offerID: string, sessionID: string) => {
    if (attachments.heldBy(offerID, sessionID)) return true
    const offers = registry.hosts().offers
    if (offers.length !== 1 || offers[0]?.offerID !== offerID) return false
    return registry.counts().attachedSessions - (registry.binding(sessionID)?.offerID === offerID ? 1 : 0) > 0
  }

  // This conversation's ownership, fenced by the registry's revision. That revision is shared by every offer and
  // conversation: another conversation attaching another application, or an offer appearing, during the native
  // lookup moves it. Only then is the snapshot taken again (an ownership that does not hold fails at once).
  const ownership = async (sessionID: string, signal?: AbortSignal) => {
    for (let attempt = 0; attempt < OWNERSHIP_ATTEMPTS; attempt += 1) {
      const before = registry.authority()
      const authority = await bindings.snapshot(sessionID, signal)
      if (authority || signal?.aborted) return authority
      const after = registry.authority()
      const moved =
        before &&
        after &&
        before.serviceGeneration === after.serviceGeneration &&
        (before.registryGeneration !== after.registryGeneration || before.revision !== after.revision)
      if (!moved) return null
    }
    return null
  }

  // The offer this conversation acts on, as the hosts and context tools name it.
  const attachedTo = (sessionID: string) => {
    const bound = registry.binding(sessionID)
    const offer = bound && registry.hosts().offers.find((candidate) => candidate.offerID === bound.offerID)
    return offer ? { offerID: offer.offerID, label: offer.label } : null
  }

  // After a wait (a permission): this conversation still owned, still attached to the same offer. Unrelated
  // changes of other offers and conversations do not count.
  const reheld = async (context: ToolContext, held: Ticket): Promise<Ticket | null> => {
    const authority = await ownership(context.sessionID, context.signal)
    const bound = authority && registry.current(authority) ? registry.binding(context.sessionID) : null
    const label = attachedTo(context.sessionID)?.label
    return bound &&
      bound.offerID === held.bound.offerID &&
      bound.scriptHandle === held.bound.scriptHandle &&
      label === held.label
      ? { authority: authority as BindingAuthority, bound, label }
      : null
  }

  // The process of the application each conversation last reached (its hello): a later call that cannot reach it
  // says that it closed, and where its crash report is. Not from WSL, where the process is on Windows.
  const reached = new Map<string, HostProcess>()
  const helloed = new Map<string, string>() // sessionID -> the offer whose hello was read
  // Its offer gone: crashed, closed, or alive without renewing its offer. Never another application in its place.
  const goneFor = (sessionID: string) => {
    const host = reached.get(sessionID)
    if (!host || wsl() || registry.hosts().offers.some((offer) => offer.offerID === host.offerID)) return null
    return crash.closedMessage(host) ?? crash.silentMessage(host)
  }
  // A call that failed, timed out or was refused as busy: what the application is doing. Its crash report
  // (written since this conversation reached that process), the process gone, or the request its main thread runs
  // now (the status method answers off the main thread).
  const diagnose = async (sessionID: string, bound: HostBinding, code: string) => {
    const host = reached.get(sessionID)
    if (!host || wsl()) return null
    const closed = crash.closedMessage(host, isAlive)
    if (closed || code === "unreachable" || !bound.methods.includes("status")) return closed
    try {
      const status = (await requestHost(bound.endpoint, "status", {}, { timeoutMs: STATUS_TIMEOUT_MS })) as {
        busy?: boolean
        running?: string
        seconds?: number
      }
      if (!status.busy) return null
      return `${appName} is still running ${status.running ?? "a request"} (${status.seconds ?? "?"} s so far): it goes on in ${appName}, nothing is lost. ${app}_context answers once it has finished; read it before acting again.`
    } catch {
      return `${appName} does not answer: its process ${host.pid} runs, but ${texts.part} answered nothing within ${STATUS_TIMEOUT_MS / 1_000} s (frozen?).`
    }
  }

  const hello = async (bound: HostBinding, signal: AbortSignal) =>
    (await requestHost(bound.endpoint, "hello", {}, { signal, timeoutMs: HELLO_TIMEOUT_MS })) as {
      script_handle: string
      revision: number
      host_version?: string
      document?: unknown
    }

  const remember = async (context: ToolContext, held: Ticket) => {
    if (helloed.get(context.sessionID) === held.bound.offerID) return
    helloed.set(context.sessionID, held.bound.offerID)
    try {
      const found = readProcess(await hello(held.bound, context.signal), held.label)
      if (found) reached.set(context.sessionID, { ...found, offerID: held.bound.offerID })
      else reached.delete(context.sessionID)
    } catch {
      // a part without process in its hello, or a failed hello: the call itself reports
    }
  }

  const unverifiable = () => new Error("This conversation's ownership is unverifiable: try again.")

  // The conversation's attached application. Without one, the only offered one, compatible and attached to no
  // conversation, attaches itself (spec FAM-5): checked against the ownership snapshot with no wait in between.
  const ticket = async (context: ToolContext): Promise<Ticket> => {
    const authority = await ownership(context.sessionID, context.signal)
    if (!authority || context.signal.aborted || !registry.current(authority)) throw unverifiable()
    let bound = registry.binding(context.sessionID)
    // Its stored attachment first: the application it used before an OpenCode restart.
    if (!bound && attachments.has(context.sessionID)) {
      await attachments.restore(context.sessionID)
      bound = registry.binding(context.sessionID)
    }
    if (!bound) {
      // Its application crashed or closed: said, and no other one attaches itself in its place.
      const gone = goneFor(context.sessionID)
      if (gone) throw new Error(gone)
      const offers = registry.hosts().offers
      const only = offers.length === 1 ? offers[0] : undefined
      if (
        only &&
        only.protocol === "ok" &&
        !heldElsewhere(only.offerID, context.sessionID) &&
        registry.current(authority)
      ) {
        if (registry.attach(only.offerID, context.sessionID).outcome === "attached") {
          bound = registry.binding(context.sessionID)
          await attachments.remember(context.sessionID)
        }
      }
      if (!bound) {
        if (!offers.length) throw new Error(texts.noneOffered)
        const list = offers
          .map(
            (offer) =>
              `${offer.label} [${offer.offerID}]${heldElsewhere(offer.offerID, context.sessionID) ? " (attached to another conversation)" : ""}${offer.protocol === "incompatible" ? ` (incompatible ${texts.partKind})` : ""}`,
          )
          .join(", ")
        throw new Error(
          `No ${appName} is attached to this conversation. Offered: ${list}. Attach one with ${app}_hosts attach.`,
        )
      }
    }
    const current = registry.authority()
    if (!current) throw unverifiable()
    await options.attachMethod(context.sessionID)
    return { authority: current, bound, label: attachedTo(context.sessionID)?.label }
  }

  const callHost = async (
    context: ToolContext,
    method: string,
    params: Record<string, unknown>,
    timeoutMs = options.requestTimeoutMs,
    captured?: Ticket,
  ) => {
    const held = captured ? await reheld(context, captured) : await ticket(context)
    if (!held) throw new Error(`This conversation's ${appName} changed while permission was asked; retry the action.`)
    if (context.signal.aborted || !registry.current(held.authority))
      throw new Error("Application attachment is unverifiable.")
    const { bound } = held
    if (!bound.methods.includes(method)) {
      throw new Error(`The attached ${appName}'s ${texts.part} has no ${method} method: ${texts.installHint}.`)
    }
    await remember(context, held)
    // A crash ends the wait at once: on Windows a crashed application may stay open behind its crash window, its
    // connection with it, so the call would wait its whole timeout. Its crash report is watched meanwhile.
    const host = wsl() ? undefined : reached.get(context.sessionID)
    const stop = new AbortController()
    const abort = () => stop.abort()
    context.signal.addEventListener("abort", abort)
    let crashed: string | null = null
    const watch = host
      ? setInterval(() => {
          crashed = crash.crashReport(host) ? crash.closedMessage(host, isAlive) : null
          if (crashed) stop.abort()
        }, CRASH_POLL_MS)
      : undefined
    try {
      return await requestHost(
        bound.endpoint,
        method,
        { script_handle: bound.scriptHandle, ...params },
        { signal: stop.signal, timeoutMs },
      )
    } catch (error) {
      if (crashed) throw new Error(crashed)
      const said =
        error instanceof HostError && ["unreachable", "timeout", "busy"].includes(error.code)
          ? await diagnose(context.sessionID, bound, error.code)
          : null
      throw said ? new Error(said) : options.hostError(error)
    } finally {
      clearInterval(watch)
      context.signal.removeEventListener("abort", abort)
    }
  }

  // The hosts tool: detach, attach (after `allow`, the attach permission), then the offers and which
  // conversation holds each. `list` names the offers' key in the output ("blenders").
  const hosts = async (
    args: Record<string, unknown>,
    context: ToolContext,
    tool: {
      allow: (context: ToolContext, offer: { offerID: string; label: string }) => Promise<void>
      status: () => Promise<{ host?: unknown }>
      list: string
    },
  ) => {
    const out: Record<string, unknown> = {}
    // Only a conversation of this Location sees and changes its attachment (RPC-12).
    const owned = await ownership(context.sessionID, context.signal)
    if (!owned) throw unverifiable()
    if (args.detach === true) {
      await attachments.forget(context.sessionID) // first: the detach's change event must not restore it
      const authority = await ownership(context.sessionID, context.signal)
      if (!authority || !registry.current(authority)) throw unverifiable()
      out.detached = registry.detach(context.sessionID).outcome
      reached.delete(context.sessionID) // detached on purpose: its application closing later is no news here
      helloed.delete(context.sessionID)
    }
    if (typeof args.attach === "string") {
      const offer = registry.hosts().offers.find((candidate) => candidate.offerID === args.attach)
      if (!offer) throw new Error(`No offered ${appName} ${String(args.attach)}: call ${app}_hosts again.`)
      await tool.allow(context, offer)
      // After the permission: this conversation still owned, the chosen offer still the same application.
      const authority = await ownership(context.sessionID, context.signal)
      const still = registry.hosts().offers.find((candidate) => candidate.offerID === offer.offerID)
      if (!authority || !registry.current(authority) || !still || still.label !== offer.label)
        throw new Error(`This conversation or the chosen ${appName} changed while permission was asked: try again.`)
      out.attached = registry.attach(offer.offerID, context.sessionID).outcome
      if (out.attached === "attached") {
        await attachments.remember(context.sessionID)
        await options.attachMethod(context.sessionID)
        if (heldElsewhere(offer.offerID, context.sessionID))
          out.shared = `Another conversation is attached to this ${appName} too: both change the same document, and each call is checked against its revision.`
      }
    }
    const status = await tool.status()
    const mine = registry.binding(context.sessionID)?.offerID
    out.attached_to = attachedTo(context.sessionID)
    out.host = status.host
    out[tool.list] = registry.hosts().offers.map((offer) => ({
      ...offer,
      attached:
        offer.offerID === mine
          ? "this conversation"
          : heldElsewhere(offer.offerID, context.sessionID)
            ? "another conversation"
            : null,
    }))
    return out
  }

  // Renews or forgets the stored attachments and attaches back the conversations whose application offers
  // itself again: one run at a time, and once more when the offers changed during it.
  let running: Promise<void> | undefined
  let again = false
  const maintain = (): Promise<void> => {
    if (running) {
      again = true
      return running
    }
    running = (async () => {
      do {
        again = false
        try {
          await attachments.sweep()
          await attachments.restore()
        } catch {}
      } while (again)
    })().finally(() => {
      running = undefined
    })
    return running
  }

  return { ownership, reheld, attachedTo, heldElsewhere, ticket, callHost, hello, hosts, maintain }
}

export type HostSession = ReturnType<typeof createHostSession>

// A host refusal as the agent reads it: the integration's hint for its code, then the host's own text.
export function hostErrorWith(hints: Record<string, string>, appName: string, detail: (error: HostError) => string) {
  return (error: unknown): Error => {
    if (error instanceof HostError) {
      const hint = hints[error.code] ?? `${appName} refused the request (${error.code}).`
      const text = detail(error)
      return new Error(text ? `${hint} ${text}` : hint)
    }
    return error instanceof Error ? error : new Error(String(error))
  }
}
