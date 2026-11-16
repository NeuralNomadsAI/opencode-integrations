// Private JSONL panel client. Built into the installed host; credentials never leave this process.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname } from "node:path"
import readline from "node:readline"
import { OpenCode } from "@opencode/client"
import { Service } from "@opencode/client/service"
import { rpcDefinition } from "../../../../packages/integration-kit/src/contract.ts"
import { requestHost } from "../../../../packages/integration-kit/src/host-client.ts"

type Channel = {
  id: string
  directory: string
  sessionID: string
  label: string
  binding: { host: string; port: number; token: string }
  handle: string
  abort: AbortController
  timer?: NodeJS.Timeout
  pending: boolean
}
type Request = { id: string; channel: string; method: string; params: Record<string, unknown> }
type Rpc = Record<string, (input: object, options: object) => Promise<Record<string, unknown>>>
let client: ReturnType<typeof OpenCode.make> | undefined
let rpc: Rpc
const channels = new Map<string, Channel>()
const queues = new Map<string, Promise<void>>()
const send = (value: unknown) => process.stdout.write(`${JSON.stringify(value)}\n`)
const event = (c: Channel, name: string, data: object) => send({ channel: c.id, event: name, data })
const options = (c: { directory: string }) => ({
  location: { directory: c.directory },
  signal: AbortSignal.timeout(3000),
})
// A message of this sidecar's own, safe to show in the panel (SDK and host errors never are).
class PanelError extends Error {}

// How long Connect waits for this Houdini's own offer (it offers itself every 5 s from its start).
const OFFER_WAIT_MS = 8_000

async function api() {
  if (client) return client
  const endpoint = await Service.discover()
  if (!endpoint) throw new Error("Start OpenCode before connecting the panel.")
  client = OpenCode.make({ baseUrl: endpoint.url, headers: Service.headers(endpoint) })
  rpc = client.rpc(rpcDefinition("neuralnomads.houdini", "native") as never) as unknown as Rpc
  return client
}

// The panel's conversation per Houdini file, kept next to the installed payloads: Connect takes it back.
type Sessions = Record<string, { session: string; directory: string }>
function readSessions(file: string): Sessions {
  try {
    const value = JSON.parse(readFileSync(file, "utf8")) as unknown
    return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Sessions) : {}
  } catch {
    return {}
  }
}
function rememberSession(file: string, key: string, session: string, directory: string) {
  if (!file || !key || key.startsWith("untitled:")) return
  try {
    const sessions = readSessions(file)
    sessions[key] = { session, directory }
    const keys = Object.keys(sessions)
    for (const old of keys.slice(0, Math.max(0, keys.length - 200))) delete sessions[old]
    mkdirSync(dirname(file), { recursive: true })
    writeFileSync(file, `${JSON.stringify(sessions)}\n`)
  } catch {
    // a conversation that is not taken back next time
  }
}

// This Houdini's offer: the one with its label among the offered hosts (Houdini offers itself; the panel never).
async function findOffer(c: { directory: string; label: string }) {
  const until = Date.now() + OFFER_WAIT_MS
  for (;;) {
    const result = await rpc.hosts?.({}, options(c))
    const offers = ((result?.offers ?? []) as Array<{ label: string; offerID: string }>).filter(
      (offer) => offer.label === c.label,
    )
    if (offers.length > 1)
      throw new PanelError(`Several Houdinis offer ${c.label}: attach this conversation from OpenCode (houdini_hosts).`)
    if (offers[0]) return offers[0]
    if (Date.now() > until)
      throw new PanelError(
        "This Houdini does not offer itself to OpenCode (OPENCODE_HOUDINI_OFFER=0, or OpenCode Link not started).",
      )
    await new Promise((resolve) => setTimeout(resolve, 1_000))
  }
}

async function attached(c: Channel) {
  const read = await rpc.bindingRead?.({ sessionID: c.sessionID }, options(c))
  return read?.state === "attached" && (read.binding as { label?: string } | undefined)?.label === c.label
}

// Closing the panel releases its own conversation only: the offer stays, and other conversations attached to
// this Houdini (CodeNomad, a terminal) keep it.
async function close(c: Channel) {
  channels.delete(c.id)
  if (c.timer) clearInterval(c.timer)
  c.abort.abort()
  await requestHost(
    c.binding,
    "cancel",
    { script_handle: c.handle, session_id: c.sessionID },
    { timeoutMs: 2000 },
  ).catch(() => {})
  // A different client may have moved the session to another host. Never detach that binding.
  await attached(c)
    .then((still) => (still ? rpc.detachHost?.({ sessionID: c.sessionID }, options(c)) : undefined))
    .catch(() => {})
}

async function stream(c: Channel) {
  const service = await api()
  try {
    for await (const item of service.event.subscribe({ signal: c.abort.signal })) {
      const data = item.data as unknown as Record<string, unknown>
      if (data.sessionID !== c.sessionID) continue
      if (item.type === "session.text.delta") event(c, "assistant_delta", { text: data.delta ?? "" })
      else if (item.type === "session.text.ended") event(c, "assistant_end", {})
      else if (item.type === "session.execution.started") event(c, "status", { state: "busy" })
      else if (item.type === "session.execution.succeeded" || item.type === "session.execution.interrupted")
        event(c, "status", { state: "idle" })
      else if (item.type === "session.execution.failed") event(c, "error", { message: "OpenCode execution failed." })
      else if (item.type === "permission.asked")
        event(c, "permission", { request_id: data.id, action: data.action, resources: data.resources })
    }
  } catch {
    if (!c.abort.signal.aborted) event(c, "error", { message: "OpenCode disconnected; choose Connect to reconnect." })
  }
}

async function connect(id: string, params: Record<string, unknown>, fresh: boolean) {
  const service = await api()
  const previous = channels.get(id)
  if (previous) await close(previous)
  if (params.host !== "127.0.0.1" || !Number.isInteger(params.port) || !/^[0-9a-f]{64}$/.test(String(params.token)))
    throw new Error("Invalid host endpoint.")
  const directory = String(params.directory ?? "")
  if (!directory) throw new Error("Select a project directory.")
  const label = String(params.offer_label ?? "")
  if (!label) throw new PanelError("This Houdini's offer is unknown.")
  const key = String(params.script_key ?? "")
  const store = String(params.sessions_file ?? "")
  // The file's conversation, when it still exists; otherwise (or with New) a new one.
  let sessionID: string | undefined
  let resumed = false
  const known = !fresh && store ? readSessions(store)[key] : undefined
  if (known?.session) {
    try {
      const found = (await service.session.get({ sessionID: known.session } as never)) as { id?: string } | undefined
      if (found?.id === known.session) {
        sessionID = known.session
        resumed = true
      }
    } catch {
      // gone: a new one
    }
  }
  if (!sessionID) {
    const label = String(params.script_name || "Houdini")
    sessionID = (await service.session.create({ title: `Houdini: ${label}`, location: { directory } })).id
  }
  const c: Channel = {
    id,
    directory: resumed && known ? known.directory : directory,
    sessionID,
    label,
    binding: { host: "127.0.0.1", port: Number(params.port), token: String(params.token) },
    handle: String(params.script_handle),
    abort: new AbortController(),
    pending: false,
  }
  try {
    const offer = await findOffer(c)
    const result = await rpc.attachHost?.({ offerID: offer.offerID, sessionID: c.sessionID }, options(c))
    if (result?.outcome !== "attached") throw new PanelError("Houdini attachment was refused.")
    rememberSession(store, key, c.sessionID, c.directory)
    channels.set(id, c)
    let warned = false
    c.timer = setInterval(() => {
      if (c.pending) return
      c.pending = true
      void api()
        .then(() => attached(c))
        .then((still) => {
          if (!still) throw new Error("detached")
        })
        .catch(() => {
          client = undefined
          if (!warned)
            event(c, "error", { message: "Conversation detached or service unavailable; choose Connect to reconnect." })
          warned = true
        })
        .finally(() => {
          c.pending = false
        })
    }, 5000)
    void stream(c)
    event(c, "session", { id: c.sessionID, resumed })
    event(c, "history", { messages: [] })
    event(c, "status", { state: "idle" })
  } catch (error) {
    await close(c)
    throw error
  }
}
async function dispatch(request: Request) {
  try {
    const params = request.params ?? {}
    if (request.method === "connect" || request.method === "new_session")
      await connect(request.channel, params, request.method === "new_session")
    else {
      const c = channels.get(request.channel)
      if (!c) throw new Error("Choose Connect first.")
      const service = await api()
      if (request.method === "close") await close(c)
      else if (request.method === "interrupt") {
        await requestHost(
          c.binding,
          "cancel",
          { script_handle: c.handle, session_id: c.sessionID },
          { timeoutMs: 2000 },
        )
        await service.session.interrupt({ sessionID: c.sessionID, resume: false })
      } else if (request.method === "prompt") {
        if (!(await attached(c))) throw new PanelError("Houdini was detached; choose Connect to attach again.")
        await requestHost(
          c.binding,
          "resume",
          { script_handle: c.handle, session_id: c.sessionID },
          { timeoutMs: 2000 },
        )
        await service.session.prompt({ sessionID: c.sessionID, text: String(params.text ?? "") })
      } else if (request.method === "permission_reply") {
        const decision = params.reply
        if (decision !== "once" && decision !== "reject") throw new Error("Invalid permission answer.")
        await service.permission.reply({ sessionID: c.sessionID, requestID: String(params.request_id), decision })
      } else throw new Error("Unknown panel action.")
    }
    send({ id: request.id, result: {} })
  } catch (error) {
    // Never forward SDK/host errors: their details can include credentials or endpoints.
    send({
      channel: request.channel,
      event: "error",
      data: {
        message:
          error instanceof PanelError
            ? error.message
            : "Connection or action failed. Check OpenCode and Houdini, then choose Connect.",
      },
    })
    send({ id: request.id, error: { message: "Panel action failed." } })
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
input.on("line", (line) => {
  try {
    if (Buffer.byteLength(line) > 1024 * 1024) throw new Error("Request too large")
    const request = JSON.parse(line) as Request
    if (!request.id || !request.channel || !request.method) throw new Error("Invalid request")
    const next = (queues.get(request.channel) ?? Promise.resolve()).then(() => dispatch(request))
    queues.set(request.channel, next)
    void next.finally(() => {
      if (queues.get(request.channel) === next) queues.delete(request.channel)
    })
  } catch {
    send({ error: { message: "Invalid panel request." } })
  }
})
input.on("close", () => {
  void Promise.allSettled([...channels.values()].map(close)).finally(() => process.exit(0))
})
