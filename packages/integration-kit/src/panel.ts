// An in-app chat is a plain client: attach its conversation to the host's existing offer, never announce one.

import { normalize } from "node:path"
import type { OpenCode } from "@opencode/client"
import { rpcDefinition } from "./contract.ts"
import { requestHost as kitRequestHost } from "./host-client.ts"

export type Api = ReturnType<typeof OpenCode.make>
type Rpc = Partial<
  Record<
    "hosts" | "attachHost" | "detachHost" | "bindingRead",
    (input: object, options: object) => Promise<Record<string, unknown>>
  >
>
type ModelRef = { id: string; providerID: string; variant?: string }
type Channel = {
  id: string
  directory: string
  sessionID: string
  label: string
  offerID: string
  host: { host: string; port: number; token: string }
  handle: string
  rpc: Rpc
  abort: AbortController
  tools: Map<string, { name: string; display?: string | null; started: number }>
  busy: boolean
  model?: ModelRef
}
type Target = {
  id: string
  params?: Record<string, unknown>
  fresh?: boolean
  empty?: boolean
  selected?: string
  taken?: boolean
  pending: string[]
  problem?: string
  retry?: NodeJS.Timeout
}
export type PanelRequest = { id: string; channel: string; method: string; params?: Record<string, unknown> }

export type PanelHost = {
  app: string
  rpcID: string
  document: string
  duplicateOffer: string
  agent?: boolean
  consoleEvents?: boolean
  hostControls?: false
}
export type PanelDependencies = {
  // The running OpenCode service, or undefined when none is running (never started from here).
  discover: (registration?: string) => Promise<Api | undefined>
  send: (message: unknown) => void
  requestHost?: typeof kitRequestHost
  checkEveryMs?: number
  retryMs?: number
}

// A message of this sidecar's own, safe to show in the panel.
class PanelError extends Error {}
class WaitingForOffer extends Error {}

function toolDisplay(name: string, input: unknown) {
  if (name !== "execute") return name
  const code = (input as { code?: unknown } | null)?.code
  if (typeof code !== "string") return name
  const names = new Set(
    Array.from(
      code.matchAll(/\btools\.(\w+)(?:\.(\w+)|\[['"]([\w-]+)['"]\])\s*\(/g),
      ([, ns, dot, key]) => `${ns}_${dot ?? key}`,
    ),
  )
  if (names.size) return [...names].join(", ")
  // Code Mode discovery is a separate execute call, not an application tool invocation.
  return /\bsearch\s*\(/.test(code) ? null : name
}

// An error in one line for the panel: its first line, bounded, without a token, an authorization or a port.
export function briefly(error: unknown) {
  const text = String((error as { message?: unknown } | null)?.message ?? error ?? "unknown error")
    .split(/\r?\n/)[0]
    ?.replace(/\b[0-9a-f]{32,}\b/gi, "…")
    .replace(/(basic|bearer)\s+\S+/gi, "$1 …")
    .replace(/\b(port\s+|:)\d{2,5}\b/gi, "$1…")
    .slice(0, 240)
  return `${text || "unknown error"}${/[.!?]$/.test(text ?? "") ? "" : "."}`
}

const modelKey = (model: ModelRef | undefined) =>
  model ? `${model.providerID}/${model.id}${model.variant ? `#${model.variant}` : ""}` : ""

type Part = { type?: string; text?: string; name?: string; state?: { status?: string; input?: unknown } }
type Message = { type?: string; text?: string; content?: Part[] }
type Conversation = { id: string; title?: string; time?: { updated?: number } }

function conversationEntry({ id, title, time }: Conversation) {
  const minutes = time?.updated ? Math.max(0, Math.floor((Date.now() - time.updated) / 60_000)) : 0
  const age =
    minutes >= 1440 ? `${Math.floor(minutes / 1440)}d` : minutes >= 60 ? `${Math.floor(minutes / 60)}h` : `${minutes}m`
  return { id, title: `${title || "Untitled"} (${age} ago)` }
}
type EventData = {
  sessionID?: string
  delta?: string
  input?: unknown
  id?: string
  name?: string
  action?: string
  agent?: string
  requestID?: string
  resources?: string[]
  model?: ModelRef
}

function history(messages: ReadonlyArray<Message>) {
  return messages.flatMap((message) => {
    if (message.type === "user") return [{ role: "user", text: message.text ?? "" }]
    if (message.type !== "assistant") return []
    return (message.content ?? []).flatMap((part) => {
      if (part.type === "text") return [{ role: "assistant", text: part.text ?? "" }]
      if (part.type === "tool") {
        const name = toolDisplay(part.name || "tool", part.state?.input)
        if (name === null) return []
        return [{ role: "tool", text: `${name} / ${part.state?.status ?? "unknown"}` }]
      }
      return []
    })
  })
}

export function createPanel(config: PanelHost, deps: PanelDependencies) {
  const requestHost = deps.requestHost ?? kitRequestHost
  const checkEveryMs = deps.checkEveryMs ?? 5_000
  const channels = new Map<string, Channel>()
  const targets = new Map<string, Target>()
  const queues = new Map<string, Promise<void>>()
  let stopping = false
  let client: Api | undefined
  let registration: string | undefined
  let rpc: Rpc = {}
  const event = (c: { id: string }, name: string, data: object) => deps.send({ channel: c.id, event: name, data })
  const options = (c: { directory: string }) => ({
    location: { directory: c.directory },
    signal: AbortSignal.timeout(3000),
  })
  const host = (c: Channel, method: "cancel" | "resume") =>
    config.hostControls === false
      ? Promise.resolve()
      : requestHost(c.host, method, { script_handle: c.handle, session_id: c.sessionID }, { timeoutMs: 2000 })

  async function api() {
    if (client) return client
    const found = await deps.discover(registration)
    if (!found) throw new PanelError("Start OpenCode to use this conversation.")
    client = found
    rpc = found.rpc(rpcDefinition(config.rpcID, "native") as never) as unknown as Rpc
    return client
  }

  // This project's offer: the one with its label among the offered hosts (offer keys stay private to the service).
  async function findOffer(c: { directory: string; label: string }) {
    let result: Record<string, unknown> | undefined
    try {
      result = await rpc.hosts?.({}, options(c))
    } catch (error) {
      // A new Location may still be loading its plugins.
      if (/RPC is unavailable|timed out|TimeoutError/i.test(briefly(error))) throw new WaitingForOffer()
      throw error
    }
    const offers = ((result?.offers ?? []) as Array<{ label: string; offerID: string }>).filter(
      (offer) => offer.label === c.label,
    )
    if (offers.length > 1)
      throw new PanelError(`Several ${config.duplicateOffer} offer ${c.label}; this panel cannot pick one.`)
    if (!offers[0]) throw new WaitingForOffer()
    return offers[0]
  }

  async function attached(c: Channel) {
    const read = await c.rpc.bindingRead?.({ sessionID: c.sessionID }, options(c))
    return read?.state === "attached" && (read.binding as { offerID?: string } | undefined)?.offerID === c.offerID
  }

  async function folderSessions(service: Api, directory: string) {
    let cursor: string | undefined
    const sessions = []
    do {
      const listed = await service.session.list({ directory, archived: false, order: "desc", limit: 100, cursor })
      sessions.push(
        ...listed.data.filter(
          (candidate) =>
            !candidate.time?.archived &&
            (!candidate.location || folderKey(candidate.location.directory) === folderKey(directory)),
        ),
      )
      cursor = listed.cursor.next ?? undefined
    } while (cursor)
    return sessions.sort((a, b) => (b.time?.updated ?? 0) - (a.time?.updated ?? 0))
  }

  function folderKey(path: unknown) {
    const key = normalize(String(path ?? "")).replace(/[/\\]$/, "")
    return process.platform === "win32" ? key.toLowerCase() : key
  }

  async function conversationList(target: Target, directory: string) {
    const sessions = await folderSessions(await api(), directory)
    event(target, "conversations", {
      selected: target.selected,
      entries: sessions.map(conversationEntry),
    })
    return sessions
  }

  // Closing releases this panel's conversation only: the offer stays for every other conversation.
  async function close(c: Channel) {
    if (channels.get(c.id) === c) channels.delete(c.id)
    c.abort.abort()
    if ([...channels.values()].some((other) => other.sessionID === c.sessionID && other.offerID === c.offerID)) return
    // Another client may have moved the conversation to another host: never detach that binding.
    await attached(c)
      .then((still) => (still ? c.rpc.detachHost?.({ sessionID: c.sessionID }, options(c)) : undefined))
      .catch(() => {})
  }

  function forward(c: Channel, type: string, data: EventData) {
    if (type === "session.model.selected") {
      c.model = data.model
      event(c, "model", { selected: modelKey(c.model) })
    } else if (config.agent && type === "session.agent.selected") event(c, "agent", { agent: data.agent ?? null })
    else if (config.consoleEvents && type === "session.step.started") {
      c.model = data.model
      event(c, "profile", { agent: data.agent, model: modelKey(c.model) })
    } else if (type === "session.execution.started") status(c, true)
    else if (type === "session.text.started") event(c, "assistant_start", {})
    else if (type === "session.text.delta") event(c, "assistant_delta", { text: data.delta ?? "" })
    else if (type === "session.text.ended") event(c, "assistant_end", {})
    else if (type === "session.tool.input.started") {
      c.tools.set(String(data.id), { name: String(data.name ?? "tool"), started: performance.now() })
      if (data.name !== "execute") event(c, "tool", { id: data.id, name: data.name, state: "starting" })
    } else if (type === "session.tool.called") {
      const tool = c.tools.get(String(data.id))
      if (tool?.name === "execute" && tool.display === undefined) {
        tool.display = toolDisplay(tool.name, data.input)
        if (tool.display !== null) event(c, "tool", { id: data.id, name: tool.display, state: "starting" })
      }
    } else if (type === "session.tool.success" || type === "session.tool.failed") {
      const tool = c.tools.get(String(data.id))
      c.tools.delete(String(data.id))
      if (tool?.display === null) return
      event(c, "tool", {
        id: data.id,
        name: tool?.display ?? tool?.name,
        state: type === "session.tool.success" ? "completed" : "failed",
        ...(config.consoleEvents && tool ? { duration_ms: Math.round(performance.now() - tool.started) } : {}),
      })
    } else if (type === "permission.asked")
      event(c, "permission", { request_id: data.id, action: data.action, resources: data.resources ?? [] })
    else if (config.consoleEvents && type === "permission.replied")
      event(c, "permission_done", { request_id: data.requestID })
    else if (type === "session.execution.succeeded" || type === "session.idle") status(c, false)
    else if (type === "session.execution.interrupted" || type === "session.execution.failed") {
      if (type === "session.execution.failed") event(c, "error", { message: "OpenCode execution failed." })
      status(c, false, config.consoleEvents && type === "session.execution.interrupted")
      void host(c, "cancel").catch(() => {})
    }
  }

  function status(c: Channel, busy: boolean, interrupted?: boolean) {
    c.busy = busy
    event(c, "status", { state: busy ? "busy" : "idle", ...(interrupted ? { interrupted: true } : {}) })
  }

  async function stream(c: Channel, service: Api) {
    try {
      for await (const item of service.event.subscribe({ signal: c.abort.signal })) {
        const data = item.data as unknown as EventData
        if (data?.sessionID !== c.sessionID) continue
        if (item.type === "session.renamed") {
          void serialize(c.id, async () => {
            const target = targets.get(c.id)
            if (target && channels.get(c.id) === c) await conversationList(target, c.directory)
          }).catch(() => {}) // Opening the selector refreshes the list again.
        } else forward(c, item.type, data)
      }
    } catch {
      // ended below
    }
    if (!c.abort.signal.aborted) {
      void serialize(c.id, async () => {
        if (channels.get(c.id) !== c) return
        const target = targets.get(c.id)
        if (target) await reconnect(target, c)
      })
    }
  }

  // A failed Connect says which step failed and why, in a line (never a credential): the user knows what to check.
  const step = async <T>(doing: string, work: () => Promise<T>): Promise<T> => {
    try {
      return await work()
    } catch (error) {
      if (error instanceof PanelError || error instanceof WaitingForOffer) throw error
      throw new PanelError(`Could not connect while ${doing}: ${briefly(error)}`)
    }
  }

  async function connect(target: Target, params: Record<string, unknown>) {
    const id = target.id
    const previous = channels.get(id)
    if (previous) await close(previous)
    if (!params.token || !params.offer_label) throw new WaitingForOffer()
    if (params.host !== "127.0.0.1" || !Number.isInteger(params.port) || !/^[0-9a-f]{64}$/.test(String(params.token)))
      throw new Error("Invalid host endpoint.")
    const directory = String(params.directory ?? "")
    const label = String(params.offer_label ?? "")
    const handle = String(params.script_handle ?? "")
    if (!directory || !label || !handle) throw new Error("Incomplete connect request.")
    const next = typeof params.registration === "string" ? params.registration : undefined
    if (next !== registration) client = undefined
    registration = next
    const service = await step("reaching OpenCode", api)
    const currentRpc = service.rpc(rpcDefinition(config.rpcID, "native") as never) as unknown as Rpc
    const offer = await step(`finding this ${config.document}'s offer`, () => findOffer({ directory, label }))
    // The project's conversation, unless New asked for another one.
    const sessions = await step("listing conversations in this folder", () => folderSessions(service, directory))
    const known = target.fresh ? undefined : (sessions.find((s) => s.id === target.selected) ?? sessions[0])
    if (!known && !target.pending.length) {
      if (!target.empty) {
        target.empty = true
        event(target, "session", { id: null, draft: Boolean(target.fresh), workspace: directory })
        event(target, "history", { messages: [] })
        event(target, "conversations", {
          entries: sessions.map(conversationEntry),
        })
        event(target, "status", { state: "idle" })
      }
      return
    }
    const session =
      known ??
      (await step(`creating this ${config.document}'s conversation`, () =>
        service.session.create({
          location: { directory },
        }),
      ))
    target.fresh = false
    target.empty = false
    target.selected = session.id
    const c: Channel = {
      id,
      directory,
      sessionID: session.id,
      label,
      offerID: offer.offerID,
      host: { host: "127.0.0.1", port: Number(params.port), token: String(params.token) },
      handle,
      rpc: currentRpc,
      abort: new AbortController(),
      tools: new Map(),
      busy: false,
      model: (session as { model?: ModelRef }).model,
    }
    try {
      const result = await step(`attaching the conversation to this ${config.document}`, async () =>
        c.rpc.attachHost?.({ offerID: offer.offerID, sessionID: c.sessionID, takeover: true }, options(c)),
      )
      if (result?.outcome === "busy")
        throw new PanelError("This conversation is attached to another application; detach it there first.")
      if (result?.outcome !== "attached") throw new PanelError(`${config.app} attachment was refused.`)
      channels.set(id, c)
      const [messages, models, configured, active] = await step("loading the conversation", () =>
        Promise.all([
          service.message.list({ sessionID: c.sessionID, order: "desc", limit: 200 }),
          service.model.list({ location: { directory } }),
          service.model.default({ location: { directory } }),
          service.session.active(),
        ]),
      )
      if (!c.model && configured.data) c.model = configured.data as ModelRef
      event(c, "session", {
        id: c.sessionID,
        resumed: Boolean(known),
        model: modelKey(c.model),
        ...(config.agent ? { agent: (session as { agent?: string }).agent ?? null } : {}),
        workspace: directory,
      })
      event(c, "models", {
        selected: modelKey(c.model),
        models: models.data
          .filter((model) => model.enabled)
          .map((model) => ({ id: model.id, provider_id: model.providerID, name: model.name })),
      })
      event(c, "history", { messages: history([...messages.data].reverse() as never) })
      event(target, "conversations", {
        selected: session.id,
        entries: (known ? sessions : [session, ...sessions]).map(conversationEntry),
      })
      if (result.replaced) event(c, "notice", { message: "Conversation moved from another host to this one." })
      status(c, (active as Record<string, { type?: string }>)[c.sessionID]?.type === "running")
      void stream(c, service)
    } catch (error) {
      await close(c)
      throw error
    }
  }

  async function act(c: Channel, method: string, params: Record<string, unknown>) {
    if (method === "close") return await close(c)
    const service = await api()
    if (method === "interrupt") {
      await host(c, "cancel").catch(() => {})
      await service.session.interrupt({ sessionID: c.sessionID, resume: false })
    } else if (method === "switch_model") {
      const key = String(params.model ?? "")
      const catalog = await service.model.list({ location: { directory: c.directory } })
      const selected = catalog.data.find((model) => model.enabled && `${model.providerID}/${model.id}` === key)
      if (!selected) throw new PanelError("This model is not available in this workspace.")
      c.model = { id: selected.id, providerID: selected.providerID }
      await service.session.switchModel({ sessionID: c.sessionID, model: c.model })
      event(c, "model", { selected: modelKey(c.model) })
    } else if (method === "permission_reply") {
      const decision = params.reply
      if (decision !== "once" && decision !== "reject") throw new Error("Invalid permission answer.")
      await service.permission.reply({ sessionID: c.sessionID, requestID: String(params.request_id), decision })
    } else throw new Error("Unknown panel action.")
  }

  function serialize(id: string, work: () => Promise<void>) {
    if (stopping) return Promise.resolve()
    const next = (queues.get(id) ?? Promise.resolve()).then(work)
    queues.set(id, next)
    void next
      .finally(() => {
        if (queues.get(id) === next) queues.delete(id)
      })
      .catch(() => {})
    return next
  }

  function later(target: Target, ms: number) {
    clearTimeout(target.retry)
    if (stopping) return
    target.retry = setTimeout(() => {
      void serialize(target.id, async () => {
        if (targets.get(target.id) !== target) return
        const c = channels.get(target.id)
        if (c && !(await attached(c).catch(() => false))) await reconnect(target, c)
        else {
          if (c?.busy) {
            const active = await (await api()).session.active().catch(() => undefined)
            if (active && (active as Record<string, { type?: string }>)[c.sessionID]?.type !== "running" && c.busy)
              status(c, false)
          }
          await reconcile(target)
        }
      })
    }, ms)
    target.retry.unref?.()
  }

  async function reconnect(target: Target, c: Channel) {
    const read = await c.rpc.bindingRead?.({ sessionID: c.sessionID }, options(c)).catch(() => undefined)
    if (read?.state === "attached" && (read.binding as { offerID?: string }).offerID !== c.offerID) {
      // Do not steal it back from the host whose user just opened it.
      target.taken = true
      await close(c)
      event(target, "status", { state: "connecting" })
      event(target, "notice", { message: "Conversation moved to another host. Open it again here to take it back." })
      return
    }
    event(target, "status", { state: "connecting" })
    await close(c)
    client = undefined
    later(target, deps.retryMs ?? 1_000)
  }

  async function reconcile(target: Target) {
    if (!target.params || target.taken || stopping) return
    let c = channels.get(target.id)
    if (!c) {
      try {
        await connect(target, target.params)
        target.problem = undefined
      } catch (error) {
        client = undefined
        if (!(error instanceof WaitingForOffer)) {
          const message = error instanceof PanelError ? error.message : `Could not connect: ${briefly(error)}`
          if (message !== target.problem) event(target, "error", { message })
          target.problem = message
        }
      }
      c = channels.get(target.id)
    }
    if (!c) {
      if (!target.empty || target.pending.length) later(target, deps.retryMs ?? 1_000)
      return
    }
    later(target, checkEveryMs)
    while (target.pending.length) {
      if (!(await attached(c).catch(() => false))) return await reconnect(target, c)
      try {
        await host(c, "resume")
      } catch {
        return later(target, deps.retryMs ?? 1_000)
      }
      const text = target.pending.shift() as string
      event(target, "user_message", { text })
      try {
        await (await api()).session.prompt({ sessionID: c.sessionID, text })
      } catch (error) {
        // Delivery is uncertain once submitted: never replay automatically.
        event(target, "error", { message: `Message delivery could not be confirmed: ${briefly(error)}` })
      }
    }
  }

  async function handle(request: PanelRequest) {
    try {
      const params = request.params ?? {}
      const id = request.channel
      const target = targets.get(id) ?? { id, pending: [] }
      const c = channels.get(id)
      if (request.method === "close") {
        clearTimeout(target.retry)
        targets.delete(id)
        if (c) await close(c)
      } else {
        targets.set(id, target)
        if (request.method === "list" && target.params) {
          await conversationList(target, String(target.params.directory))
        } else if (request.method === "open" && target.params) {
          const sessions = await folderSessions(await api(), String(target.params.directory))
          const selected = sessions.find((s) => s.id === params.session_id)
          if (!selected) throw new PanelError("That conversation is not in this folder.")
          target.selected = selected.id
          target.taken = false
          target.fresh = false
          target.pending = []
          if (c?.sessionID !== selected.id) {
            event(target, "status", { state: "connecting" })
            if (c) await close(c)
            await reconcile(target)
          }
        } else if (request.method === "connect" || request.method === "new_session") {
          const fresh = request.method === "new_session"
          const moved = target.params && folderKey(target.params.directory) !== folderKey(params.directory)
          if (moved) target.selected = undefined
          if (!target.params) {
            if (typeof params.session_id === "string") target.selected = params.session_id
            target.fresh = params.draft === true
          }
          if (fresh || moved) {
            if (target.pending.length)
              event(target, "notice", { message: "Queued messages were not sent to the new conversation." })
            target.pending = []
          }
          const previous = target.params
          target.params = params
          if (
            c &&
            previous &&
            !fresh &&
            !moved &&
            ["host", "port", "token", "registration", "script_handle"].every((key) => params[key] === previous[key])
          ) {
            // A rename of the same live offer does not move the attachment or interrupt its stream.
            deps.send({ id: request.id, result: {} })
            return
          }
          if (fresh || moved) {
            target.selected = undefined
            target.fresh = fresh
            target.empty = false
          } else if (target.empty) {
            target.empty = false
          }
          target.taken = false
          target.problem = undefined
          event(target, "status", { state: "connecting" })
          if (c) await close(c)
          await reconcile(target)
        } else if (request.method === "prompt") {
          target.pending.push(String(params.text ?? ""))
          if (target.params) await reconcile(target)
        } else if (request.method === "interrupt") {
          target.pending = []
          if (c) await act(c, request.method, params)
        } else if (c) await act(c, request.method, params)
      }
      deps.send({ id: request.id, result: {} })
    } catch (error) {
      deps.send({
        channel: request.channel,
        event: "error",
        data: {
          message: error instanceof PanelError ? error.message : `The panel action failed: ${briefly(error)}`,
        },
      })
      deps.send({ id: request.id, error: { message: "Panel action failed." } })
    }
  }

  return {
    dispatch: (request: PanelRequest) => serialize(request.channel, () => handle(request)),
    closeAll: async () => {
      stopping = true
      for (const target of targets.values()) clearTimeout(target.retry)
      await Promise.allSettled(queues.values())
      await Promise.allSettled([...channels.values()].map(close))
    },
  }
}
