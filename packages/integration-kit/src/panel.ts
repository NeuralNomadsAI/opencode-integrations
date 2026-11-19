// An in-app chat is a plain client: attach its conversation to the host's existing offer, never announce one.
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
  abort: AbortController
  timer?: NodeJS.Timeout
  checking: boolean
  tools: Map<string, { name: string; started: number }>
  model?: ModelRef
}
export type PanelRequest = { id: string; channel: string; method: string; params?: Record<string, unknown> }

export type PanelHost = {
  app: string
  rpcID: string
  marker: string
  document: string
  keyField: string
  nameField: string
  defaultName: string
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
  offerWaitMs?: number
  checkEveryMs?: number
}

// A message of this sidecar's own, safe to show in the panel.
class PanelError extends Error {}

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

type Part = { type?: string; text?: string; name?: string; state?: { status?: string } }
type Message = { type?: string; text?: string; content?: Part[] }
type EventData = {
  sessionID?: string
  delta?: string
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
      if (part.type === "tool") return [{ role: "tool", text: `${part.name} / ${part.state?.status ?? "unknown"}` }]
      return []
    })
  })
}

export function createPanel(config: PanelHost, deps: PanelDependencies) {
  const requestHost = deps.requestHost ?? kitRequestHost
  // A Location opened for the panel loads its plugins first, which took tens of seconds in a trial.
  const offerWaitMs = deps.offerWaitMs ?? 30_000
  const checkEveryMs = deps.checkEveryMs ?? 5_000
  const channels = new Map<string, Channel>()
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
    if (!found) throw new PanelError("Start OpenCode before connecting the panel.")
    client = found
    rpc = found.rpc(rpcDefinition(config.rpcID, "native") as never) as unknown as Rpc
    return client
  }

  // This project's offer: the one with its label among the offered hosts (offer keys stay private to the service).
  async function findOffer(c: { directory: string; label: string }) {
    const until = Date.now() + offerWaitMs
    for (;;) {
      let result: { offers?: unknown } | undefined
      try {
        result = (await rpc.hosts?.({}, options(c))) as typeof result
      } catch (error) {
        // The first call in the project's folder: OpenCode opens that Location and loads its plugins meanwhile, and
        // the integration's RPC is not there yet (a CodeNomad conversation's own Location is long loaded).
        if (
          !/RPC is unavailable/i.test(String((error as { message?: unknown })?.message ?? error)) ||
          Date.now() > until
        )
          throw error
        await new Promise((resolve) => setTimeout(resolve, 1_000))
        continue
      }
      const offers = ((result?.offers ?? []) as Array<{ label: string; offerID: string }>).filter(
        (offer) => offer.label === c.label,
      )
      if (offers.length > 1)
        throw new PanelError(
          `Several ${config.duplicateOffer} offer ${c.label}: attach this conversation from OpenCode (${config.app.toLowerCase()}_hosts).`,
        )
      if (offers[0]) return offers[0]
      if (Date.now() > until)
        throw new PanelError(`This ${config.document} is not offered to OpenCode yet; choose Connect again.`)
      await new Promise((resolve) => setTimeout(resolve, 1_000))
    }
  }

  async function attached(c: Channel) {
    const read = await rpc.bindingRead?.({ sessionID: c.sessionID }, options(c))
    return read?.state === "attached" && (read.binding as { offerID?: string } | undefined)?.offerID === c.offerID
  }

  // The project's own conversation, when one is marked for it and still open.
  async function markedSession(service: Api, directory: string, key: string) {
    let cursor: string | undefined
    for (let page = 0; page < 10; page += 1) {
      const listed = await service.session.list({ directory, order: "desc", limit: 100, cursor })
      const found = listed.data.find(
        (candidate) =>
          !candidate.time?.archived &&
          (candidate.metadata as Record<string, unknown> | undefined)?.[config.marker] === key,
      )
      if (found) return found
      cursor = listed.cursor.next ?? undefined
      if (!cursor) return undefined
    }
    return undefined
  }

  // Closing releases this panel's conversation only: the offer stays for every other conversation.
  async function close(c: Channel) {
    if (channels.get(c.id) === c) channels.delete(c.id)
    if (c.timer) clearInterval(c.timer)
    c.abort.abort()
    await host(c, "cancel").catch(() => {})
    // Another client may have moved the conversation to another host: never detach that binding.
    await attached(c)
      .then((still) => (still ? rpc.detachHost?.({ sessionID: c.sessionID }, options(c)) : undefined))
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
    } else if (type === "session.execution.started") event(c, "status", { state: "busy" })
    else if (type === "session.text.started") event(c, "assistant_start", {})
    else if (type === "session.text.delta") event(c, "assistant_delta", { text: data.delta ?? "" })
    else if (type === "session.text.ended") event(c, "assistant_end", {})
    else if (type === "session.tool.input.started") {
      c.tools.set(String(data.id), { name: String(data.name ?? "tool"), started: performance.now() })
      event(c, "tool", { id: data.id, name: data.name, state: "starting" })
    } else if (type === "session.tool.success" || type === "session.tool.failed") {
      const tool = c.tools.get(String(data.id))
      c.tools.delete(String(data.id))
      event(c, "tool", {
        id: data.id,
        name: tool?.name,
        state: type === "session.tool.success" ? "completed" : "failed",
        ...(config.consoleEvents && tool ? { duration_ms: Math.round(performance.now() - tool.started) } : {}),
      })
    } else if (type === "permission.asked")
      event(c, "permission", { request_id: data.id, action: data.action, resources: data.resources ?? [] })
    else if (config.consoleEvents && type === "permission.replied")
      event(c, "permission_done", { request_id: data.requestID })
    else if (type === "session.execution.succeeded" || type === "session.idle") event(c, "status", { state: "idle" })
    else if (type === "session.execution.interrupted" || type === "session.execution.failed") {
      if (type === "session.execution.failed") event(c, "error", { message: "OpenCode execution failed." })
      event(c, "status", {
        state: "idle",
        ...(config.consoleEvents && type === "session.execution.interrupted" ? { interrupted: true } : {}),
      })
      void host(c, "cancel").catch(() => {})
    }
  }

  async function stream(c: Channel, service: Api) {
    try {
      for await (const item of service.event.subscribe({ signal: c.abort.signal })) {
        const data = item.data as unknown as EventData
        if (data?.sessionID === c.sessionID) forward(c, item.type, data)
      }
    } catch {
      // ended below
    }
    if (!c.abort.signal.aborted)
      event(c, config.consoleEvents ? "disconnected" : "error", {
        message: "OpenCode disconnected; choose Connect to reconnect.",
      })
  }

  function watch(c: Channel) {
    let warned = false
    c.timer = setInterval(() => {
      if (c.checking) return
      c.checking = true
      void api()
        .then(() => attached(c))
        .then((still) => {
          if (!still) throw new Error("detached")
          warned = false
        })
        .catch(() => {
          client = undefined
          if (!warned && channels.get(c.id) === c)
            event(c, config.consoleEvents ? "disconnected" : "error", {
              message: "Conversation detached or service unavailable; choose Connect to reconnect.",
            })
          warned = true
        })
        .finally(() => {
          c.checking = false
        })
    }, checkEveryMs)
    c.timer.unref?.()
  }

  // A failed Connect says which step failed and why, in a line (never a credential): the user knows what to check.
  const step = async <T>(doing: string, work: () => Promise<T>): Promise<T> => {
    try {
      return await work()
    } catch (error) {
      if (error instanceof PanelError) throw error
      throw new PanelError(
        `Connect failed while ${doing}: ${briefly(error)} Check that OpenCode runs, then choose Connect.`,
      )
    }
  }

  async function connect(id: string, params: Record<string, unknown>, fresh: boolean) {
    const previous = channels.get(id)
    if (previous) await close(previous)
    if (params.host !== "127.0.0.1" || !Number.isInteger(params.port) || !/^[0-9a-f]{64}$/.test(String(params.token)))
      throw new Error("Invalid host endpoint.")
    const directory = String(params.directory ?? "")
    const label = String(params.offer_label ?? "")
    const key = String(params[config.keyField] ?? "")
    const handle = String(params.script_handle ?? "")
    if (!directory || !label || !key || !handle) throw new Error("Incomplete connect request.")
    if (config.hostControls === false) {
      const next = typeof params.registration === "string" ? params.registration : undefined
      if (next !== registration) client = undefined
      registration = next
    }
    const service = await step("reaching OpenCode", api)
    // The project's conversation, unless New asked for another one.
    const known = fresh
      ? undefined
      : await step(`finding this ${config.document}'s conversation`, () => markedSession(service, directory, key))
    const session =
      known ??
      (await step(`creating this ${config.document}'s conversation`, () =>
        service.session.create({
          title: `${config.app}: ${String(params[config.nameField] || config.defaultName)}`,
          location: { directory },
          metadata: { [config.marker]: key },
        }),
      ))
    const c: Channel = {
      id,
      directory,
      sessionID: session.id,
      label,
      offerID: "",
      host: { host: "127.0.0.1", port: Number(params.port), token: String(params.token) },
      handle,
      abort: new AbortController(),
      checking: false,
      tools: new Map(),
      model: (session as { model?: ModelRef }).model,
    }
    try {
      const offer = await step(`finding this ${config.document}'s offer`, () => findOffer(c))
      const result = await step(`attaching the conversation to this ${config.document}`, async () =>
        rpc.attachHost?.({ offerID: offer.offerID, sessionID: c.sessionID }, options(c)),
      )
      if (result?.outcome === "busy")
        throw new PanelError("This conversation is attached to another application; detach it there first.")
      if (result?.outcome !== "attached") throw new PanelError(`${config.app} attachment was refused.`)
      c.offerID = offer.offerID
      channels.set(id, c)
      watch(c)
      void stream(c, service)
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
      event(c, "status", {
        state: (active as Record<string, { type?: string }>)[c.sessionID]?.type === "running" ? "busy" : "idle",
      })
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
    } else if (method === "prompt") {
      if (!(await attached(c))) throw new PanelError(`${config.app} was detached; choose Connect to attach again.`)
      await host(c, "resume")
      await service.session.prompt({ sessionID: c.sessionID, text: String(params.text ?? "") })
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

  async function dispatch(request: PanelRequest) {
    try {
      const params = request.params ?? {}
      if (request.method === "connect" || request.method === "new_session")
        await connect(request.channel, params, request.method === "new_session")
      else {
        const c = channels.get(request.channel)
        if (c) await act(c, request.method, params)
        else if (request.method !== "close") throw new PanelError("Choose Connect first.")
      }
      deps.send({ id: request.id, result: {} })
    } catch (error) {
      deps.send({
        channel: request.channel,
        event: "error",
        data: {
          message:
            error instanceof PanelError
              ? error.message
              : `The panel action failed: ${briefly(error)} Check that OpenCode runs, then choose Connect.`,
        },
      })
      deps.send({ id: request.id, error: { message: "Panel action failed." } })
    }
  }

  return {
    dispatch,
    closeAll: () => Promise.allSettled([...channels.values()].map(close)),
  }
}
