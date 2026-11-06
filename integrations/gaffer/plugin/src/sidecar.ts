import { randomUUID } from "node:crypto"
import { homedir } from "node:os"
import { join } from "node:path"
import readline from "node:readline"

import { OpenCode } from "@opencode/client"

import { type HostBinding, requestHost } from "./host-client.ts"
import { GafferRpc } from "./rpc.ts"
import { discoverService } from "./service.ts"

type Channel = {
  id: string
  directory: string
  scriptKey: string
  sessionID: string
  bindingKey: string
  offerID?: string
  binding: HostBinding
  revision: number
  lastBindingCheck: number
  toolNames: Map<string, string>
  model?: ModelRef
}

type ModelRef = {
  id: string
  providerID: string
  variant?: string
}

function modelKey(model: ModelRef | undefined) {
  return model ? `${model.providerID}/${model.id}${model.variant ? `#${model.variant}` : ""}` : ""
}

type Request = {
  id?: string
  channel?: string
  method?: string
  params?: Record<string, any>
}

let client: ReturnType<typeof OpenCode.make> | undefined
let gafferRpc: any
let eventAbort: AbortController | undefined
const channels = new Map<string, Channel>()
const channelQueues = new Map<string, Promise<void>>()
let pollTimer: NodeJS.Timeout | undefined
let pollRunning = false
let restoreTimer: NodeJS.Timeout | undefined

function emit(message: unknown) {
  process.stdout.write(`${JSON.stringify(message)}\n`)
}

function event(channel: string, name: string, data: Record<string, unknown> = {}) {
  emit({ event: name, channel, data })
}

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message
  if (typeof error === "object" && error !== null && "message" in error) {
    return String((error as { message: unknown }).message)
  }
  return String(error)
}

function errorData(error: unknown) {
  const object = typeof error === "object" && error !== null ? (error as Record<string, unknown>) : {}
  return {
    code: typeof object.code === "string" ? object.code : typeof object.type === "string" ? object.type : undefined,
    message: errorMessage(error),
    data: object.data,
  }
}

function rpcOptions(directory: string, signal?: AbortSignal) {
  return { location: { directory }, signal }
}

async function ensureClient() {
  if (client) return client

  // The editor is a client of the user's shared OpenCode2 service. Beta client
  // package versions may lag the independently upgraded CLI, so exact-version
  // discovery would incorrectly stop the shared service and disrupt all hosts.
  const discovered = await discoverService()
  const endpoint = discovered
  if (!endpoint) throw new Error("OpenCode is not running; start it in your OpenCode client.")

  client = OpenCode.make({ baseUrl: endpoint.url, headers: endpoint.headers, fetch: endpoint.fetch })
  gafferRpc = client.rpc(GafferRpc as never)
  startEvents()
  return client
}

function sessionForEvent(input: any) {
  return input?.data?.sessionID ?? input?.data?.session_id
}

function startEvents() {
  if (!client || eventAbort) return
  const api = client
  const controller = new AbortController()
  eventAbort = controller
  const signal = controller.signal

  void (async () => {
    let failure: unknown = new Error("OpenCode event stream closed")
    try {
      for await (const item of api.event.subscribe({ signal })) {
        const sessionID = sessionForEvent(item)
        if (!sessionID) continue
        for (const channel of channels.values()) {
          if (channel.sessionID !== sessionID) continue
          forwardOpenCodeEvent(channel, item)
        }
      }
    } catch (error) {
      failure = error
    }
    if (signal.aborted || client !== api) return

    await Promise.allSettled([...channels.values()].map((channel) => cancelHost(channel)))
    client = undefined
    gafferRpc = undefined
    eventAbort = undefined
    for (const channel of channels.values()) {
      event(channel.id, "status", { state: "reconnecting", message: errorMessage(failure) })
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
    try {
      await ensureClient()
      await restoreBindings()
    } catch (error) {
      for (const channel of channels.values()) {
        event(channel.id, "error", errorData(error))
      }
    }
  })()
}

function forwardOpenCodeEvent(channel: Channel, item: any) {
  const data = item.data ?? {}
  switch (item.type) {
    case "session.model.selected":
      channel.model = data.model
      event(channel.id, "model", { selected: modelKey(channel.model) })
      break
    case "session.execution.started":
      event(channel.id, "status", { state: "busy" })
      break
    case "session.text.started":
      event(channel.id, "assistant_start", { message_id: data.assistantMessageID, ordinal: data.ordinal })
      break
    case "session.text.delta":
      event(channel.id, "assistant_delta", {
        message_id: data.assistantMessageID,
        ordinal: data.ordinal,
        text: data.delta ?? "",
      })
      break
    case "session.text.ended":
      event(channel.id, "assistant_end", {
        message_id: data.assistantMessageID,
        ordinal: data.ordinal,
        text: data.text ?? "",
      })
      break
    case "session.tool.input.started": {
      channel.toolNames.set(String(data.id), String(data.name ?? "tool"))
      event(channel.id, "tool", {
        id: data.id,
        name: data.name,
        message_id: data.assistantMessageID,
        state: "starting",
      })
      break
    }
    case "session.tool.success": {
      const name = channel.toolNames.get(String(data.id))
      channel.toolNames.delete(String(data.id))
      event(channel.id, "tool", {
        id: data.id,
        name,
        message_id: data.assistantMessageID,
        state: "completed",
      })
      break
    }
    case "session.tool.failed": {
      const name = channel.toolNames.get(String(data.id))
      channel.toolNames.delete(String(data.id))
      event(channel.id, "tool", {
        id: data.id,
        name,
        message_id: data.assistantMessageID,
        state: "failed",
        error: data.error?.message,
      })
      break
    }
    case "permission.asked":
      event(channel.id, "permission", {
        request_id: data.id,
        action: data.action,
        resources: data.resources ?? [],
        message: data.message,
      })
      break
    case "session.status":
      event(channel.id, "status", { state: data.status?.type ?? "unknown" })
      break
    case "session.idle":
    case "session.execution.succeeded":
      event(channel.id, "status", { state: "idle" })
      break
    case "session.execution.failed":
      event(channel.id, "error", { message: data.error?.message ?? "OpenCode execution failed" })
      event(channel.id, "status", { state: "failed" })
      void cancelHost(channel).catch((error) => event(channel.id, "graph_error", errorData(error)))
      break
    case "session.execution.interrupted":
      event(channel.id, "status", { state: "interrupted" })
      void cancelHost(channel).catch((error) => event(channel.id, "graph_error", errorData(error)))
      break
  }
}

function normalizeHistory(messages: any[]) {
  return messages.flatMap((message) => {
    if (message.type === "user") return [{ role: "user", text: message.text ?? "" }]
    if (message.type !== "assistant") return []
    return (message.content ?? []).flatMap((part: any) => {
      if (part.type === "text") return [{ role: "assistant", text: part.text ?? "" }]
      if (part.type === "tool") {
        return [{ role: "tool", text: `${part.name} / ${part.state?.status ?? "unknown"}` }]
      }
      return []
    })
  })
}

async function attachChannel(channelID: string, params: Record<string, any>, forceNew = false) {
  const api = await ensureClient()
  const binding: HostBinding = {
    host: String(params.host ?? "127.0.0.1"),
    port: Number(params.port),
    token: String(params.token),
    scriptHandle: String(params.script_handle),
  }
  const directory = String(params.directory)
  const scriptKey = String(params.script_key ?? "")
  if (!directory || !scriptKey || !binding.scriptHandle || !binding.token || !Number.isInteger(binding.port)) {
    throw new Error("connect requires directory, script_key, script_handle, token, and a valid port")
  }

  let session: any
  if (!forceNew) {
    let cursor: string | undefined
    for (let page = 0; page < 10 && !session; page += 1) {
      const listed = await api.session.list({ directory, order: "desc", limit: 100, cursor })
      session = listed.data.find(
        (candidate: any) => !candidate.time?.archived && candidate.metadata?.opencode_gaffer_script === scriptKey,
      )
      cursor = listed.cursor.next ?? undefined
      if (!cursor) break
    }
  }
  session ??= await api.session.create({
    title: `Gaffer / ${params.script_name ?? "Untitled"}`,
    location: { directory },
    metadata: { opencode_gaffer_script: scriptKey },
  })

  const previous = channels.get(channelID)
  const bindingKey = randomUUID()
  const channel: Channel = {
    id: channelID,
    directory,
    scriptKey,
    sessionID: session.id,
    bindingKey,
    binding,
    revision: 0,
    lastBindingCheck: Date.now(),
    toolNames: new Map(),
    model: session.model,
  }
  await reattach(channel)
  if (previous) {
    try {
      await stopChannel(previous)
    } catch (error) {
      await gafferRpc
        .detachHost({ sessionID: channel.sessionID }, rpcOptions(channel.directory, AbortSignal.timeout(2000)))
        .catch(() => undefined)
      throw error
    }
  }
  channels.set(channelID, channel)

  const [history, models, configuredDefault] = await Promise.all([
    api.message.list({ sessionID: session.id, order: "desc", limit: 200 }),
    api.model.list({ location: { directory } }),
    api.model.default({ location: { directory } }),
  ])
  if (!channel.model && configuredDefault.data) channel.model = configuredDefault.data
  history.data.reverse()
  const active = await api.session.active()
  event(channelID, "session", {
    id: session.id,
    title: session.title,
    resumed: !forceNew && normalizeHistory(history.data).length > 0,
    revision: channel.revision,
    model: modelKey(channel.model),
    workspace: directory,
  })
  event(channelID, "models", {
    selected: modelKey(channel.model),
    models: models.data
      .filter((model: any) => model.enabled)
      .map((model: any) => ({
        id: model.id,
        provider_id: model.providerID,
        name: model.name,
      })),
  })
  event(channelID, "history", { messages: normalizeHistory(history.data) })
  event(channelID, "status", { state: active[session.id]?.type === "running" ? "busy" : "idle" })
  startPolling()
  return { session_id: session.id, revision: channel.revision }
}

async function reattach(channel: Channel) {
  await advertise(channel)
  const listed = await gafferRpc.hosts({}, rpcOptions(channel.directory))
  const offer = listed.offers.find((item: { label: string }) => item.label === offerLabel(channel))
  if (!offer) throw new Error("The Gaffer host offer is unavailable.")
  const attached = await gafferRpc.attachHost(
    { offerID: offer.offerID, sessionID: channel.sessionID },
    rpcOptions(channel.directory),
  )
  if (attached.outcome !== "attached") throw new Error("This conversation could not attach to Gaffer.")
  channel.offerID = offer.offerID
}

function offerLabel(channel: Channel) {
  return `Gaffer ${channel.id}`.slice(0, 120)
}
function offerLocation() {
  return (
    process.env.NEURALNOMADS_GAFFER_GLOBAL_CONFIG_DIR ??
    join(process.env.XDG_CONFIG_HOME ?? join(homedir(), ".config"), "opencode")
  )
}
async function advertise(channel: Channel) {
  const result = await gafferRpc.advertise(
    {
      host: channel.binding.host,
      port: channel.binding.port,
      token: channel.binding.token,
      script_handle: channel.binding.scriptHandle,
      label: offerLabel(channel),
      protocol: 1,
      offer_key: channel.bindingKey,
    },
    rpcOptions(offerLocation()),
  )
  if (result.outcome === "refused") throw new Error("The Gaffer host advertisement was refused.")
}

async function ensureAttached(channel: Channel) {
  await advertise(channel)
  const status = await gafferRpc.bindingRead({ sessionID: channel.sessionID }, rpcOptions(channel.directory))
  if (status.state !== "attached" || status.binding?.offerID !== channel.offerID)
    throw new Error("Gaffer is detached or attached elsewhere; reconnect explicitly in the panel.")
  channel.lastBindingCheck = Date.now()
}

async function cancelHost(channel: Channel) {
  await requestHost(channel.binding, "cancel", { session_id: channel.sessionID }, { timeoutMs: 2000 })
}

async function cancelHostWithRetry(channel: Channel) {
  let failure: unknown
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await cancelHost(channel)
      return
    } catch (error) {
      failure = error
      if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 250))
    }
  }
  throw failure
}

async function stopChannel(channel: Channel) {
  try {
    // Withdraw only this panel's offer: the conversation may now be attached elsewhere.
    await cancelHostWithRetry(channel)
  } finally {
    await gafferRpc.withdraw(
      { offer_key: channel.bindingKey, token: channel.binding.token },
      rpcOptions(offerLocation(), AbortSignal.timeout(2000)),
    )
  }
}

async function restoreBindings() {
  let failed = false
  for (const channel of channels.values()) {
    try {
      await advertise(channel)
      event(channel.id, "status", { state: "detached", message: "Reconnect explicitly in the panel to attach again." })
    } catch (error) {
      failed = true
      event(channel.id, "status", { state: "reconnecting", message: errorMessage(error) })
    }
  }
  if (failed && !restoreTimer) {
    restoreTimer = setTimeout(() => {
      restoreTimer = undefined
      void restoreBindings()
    }, 1000)
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(() => void pollGraphChanges(), 500)
}

async function pollGraphChanges() {
  if (pollRunning) return
  pollRunning = true
  try {
    for (const channel of channels.values()) {
      try {
        if (Date.now() - channel.lastBindingCheck >= 5000) {
          await ensureAttached(channel)
        }
        const result = await requestHost(
          channel.binding,
          "sync",
          { after_revision: channel.revision },
          { timeoutMs: 2000 },
        )
        const revision = Number(result.revision ?? channel.revision)
        if (revision === channel.revision) continue
        if (channels.get(channel.id) !== channel) continue
        channel.revision = revision
        event(channel.id, "graph_revision", {
          revision,
          changes: result.changes ?? [],
          reset: result.reset === true,
        })
      } catch (error) {
        event(channel.id, "graph_error", errorData(error))
      }
    }
  } finally {
    pollRunning = false
  }
}

async function dispatch(request: Request) {
  const id = request.id
  const channelID = String(request.channel ?? "")
  const params = request.params ?? {}
  try {
    let result: unknown
    switch (request.method) {
      case "connect":
        result = await attachChannel(channelID, params)
        break
      case "new_session": {
        const current = channels.get(channelID)
        if (!current) throw new Error("Channel is not connected")
        result = await attachChannel(
          channelID,
          {
            ...params,
            host: current.binding.host,
            port: current.binding.port,
            token: current.binding.token,
            script_handle: current.binding.scriptHandle,
            directory: current.directory,
            script_key: current.scriptKey,
          },
          true,
        )
        break
      }
      case "prompt": {
        const channel = channels.get(channelID)
        if (!channel) throw new Error("Channel is not connected")
        const api = await ensureClient()
        await ensureAttached(channel)
        await requestHost(channel.binding, "resume", { session_id: channel.sessionID }, { timeoutMs: 2000 })
        result = await api.session.prompt({ sessionID: channel.sessionID, text: String(params.text ?? "") })
        break
      }
      case "switch_model": {
        const channel = channels.get(channelID)
        if (!channel) throw new Error("Channel is not connected")
        const api = await ensureClient()
        const key = String(params.model ?? "")
        const catalog = await api.model.list({ location: { directory: channel.directory } })
        const selected = catalog.data.find((model: any) => model.enabled && `${model.providerID}/${model.id}` === key)
        if (!selected) throw new Error(`Model is not available in this workspace: ${key}`)
        channel.model = { id: selected.id, providerID: selected.providerID }
        await api.session.switchModel({ sessionID: channel.sessionID, model: channel.model })
        event(channel.id, "model", { selected: modelKey(channel.model) })
        result = { selected: modelKey(channel.model) }
        break
      }
      case "interrupt": {
        const channel = channels.get(channelID)
        if (!channel) throw new Error("Channel is not connected")
        const api = await ensureClient()
        await cancelHost(channel).catch((error) => {
          event(channel.id, "graph_error", errorData(error))
        })
        result = await api.session.interrupt({ sessionID: channel.sessionID, resume: false })
        break
      }
      case "permission_reply": {
        const channel = channels.get(channelID)
        if (!channel) throw new Error("Channel is not connected")
        const api = await ensureClient()
        const reply = String(params.reply)
        if (reply !== "once" && reply !== "always" && reply !== "reject") {
          throw new Error("permission reply must be once, always, or reject")
        }
        result = await api.permission.reply({
          sessionID: channel.sessionID,
          requestID: String(params.request_id),
          decision: reply,
        })
        break
      }
      case "close": {
        const channel = channels.get(channelID)
        if (channel) {
          channels.delete(channelID)
          if (channels.size === 0 && pollTimer) {
            clearInterval(pollTimer)
            pollTimer = undefined
          }
          await ensureClient()
          await stopChannel(channel)
        }
        result = {}
        break
      }
      default:
        throw new Error(`Unknown sidecar method: ${request.method}`)
    }
    if (id !== undefined) emit({ id, result })
  } catch (error) {
    if (id !== undefined) emit({ id, error: errorData(error) })
    event(channelID, "error", errorData(error))
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
input.on("line", (line) => {
  if (!line.trim()) return
  try {
    if (Buffer.byteLength(line, "utf8") > 1024 * 1024) throw new Error("Sidecar request exceeded 1 MiB")
    const request = JSON.parse(line) as Request
    if (!request.id || !request.channel || !request.method)
      throw new Error("Sidecar request requires id, channel, and method")
    const previous = channelQueues.get(request.channel) ?? Promise.resolve()
    const next = previous.catch(() => undefined).then(() => dispatch(request))
    channelQueues.set(request.channel, next)
    void next.finally(() => {
      if (channelQueues.get(request.channel!) === next) channelQueues.delete(request.channel!)
    })
  } catch (error) {
    emit({ error: { message: `Malformed sidecar request: ${errorMessage(error)}` } })
  }
})

input.on("close", () => {
  void (async () => {
    if (pollTimer) clearInterval(pollTimer)
    if (restoreTimer) clearTimeout(restoreTimer)
    eventAbort?.abort()
    const closingChannels = [...channels.values()]
    channels.clear()
    if (gafferRpc && client) {
      await Promise.allSettled(closingChannels.map((channel) => stopChannel(channel)))
    }
    process.exit(0)
  })()
})
