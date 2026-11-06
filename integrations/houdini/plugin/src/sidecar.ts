// Private JSONL panel client. Built into the installed host; credentials never leave this process.
import { randomUUID } from "node:crypto"
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
  key: string
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
const options = (c: Channel) => ({ location: { directory: c.directory }, signal: AbortSignal.timeout(3000) })

async function api() {
  if (client) return client
  const endpoint = await Service.discover()
  if (!endpoint) throw new Error("Start OpenCode before connecting the panel.")
  client = OpenCode.make({ baseUrl: endpoint.url, headers: Service.headers(endpoint) })
  rpc = client.rpc(rpcDefinition("neuralnomads.houdini", "native") as never) as unknown as Rpc
  return client
}

async function advertise(c: Channel) {
  const result = await rpc.advertise?.(
    {
      host: c.binding.host,
      port: c.binding.port,
      token: c.binding.token,
      script_handle: c.handle,
      label: c.label,
      protocol: 1,
      offer_key: c.key,
    },
    options(c),
  )
  if (result?.outcome !== "accepted" && result?.outcome !== "renewed") throw new Error("Houdini offer was refused.")
}

async function verify(c: Channel) {
  const read = await rpc.bindingRead?.({ sessionID: c.sessionID }, options(c))
  if (read?.state !== "attached") throw new Error("Houdini was detached; choose Connect to attach again.")
  const binding = read.binding as { label?: string } | undefined
  if (binding?.label !== c.label) throw new Error("This conversation is attached to another host.")
}

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
  await verify(c)
    .then(() => rpc.detachHost?.({ sessionID: c.sessionID }, options(c)))
    .catch(() => {})
  await rpc.withdraw?.({ offer_key: c.key, token: c.binding.token }, options(c)).catch(() => {})
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

async function connect(id: string, params: Record<string, unknown>) {
  const service = await api()
  const previous = channels.get(id)
  if (previous) await close(previous)
  if (params.host !== "127.0.0.1" || !Number.isInteger(params.port) || !/^[0-9a-f]{64}$/.test(String(params.token)))
    throw new Error("Invalid host endpoint.")
  const directory = String(params.directory ?? "")
  if (!directory) throw new Error("Select a project directory.")
  const session = await service.session.create({ title: "Houdini", location: { directory } })
  const c: Channel = {
    id,
    directory,
    sessionID: session.id,
    label: `Houdini ${randomUUID().slice(0, 8)}`,
    key: randomUUID(),
    binding: { host: "127.0.0.1", port: Number(params.port), token: String(params.token) },
    handle: String(params.script_handle),
    abort: new AbortController(),
    pending: false,
  }
  try {
    await advertise(c)
    const result = await rpc.hosts?.({}, options(c))
    const offer = ((result?.offers ?? []) as Array<{ label: string; offerID: string }>).find(
      (offer) => offer.label === c.label,
    )
    if (!offer) throw new Error("No validated Houdini offer.")
    const attached = await rpc.attachHost?.({ offerID: offer.offerID, sessionID: c.sessionID }, options(c))
    if (attached?.outcome !== "attached") throw new Error("Houdini attachment was refused.")
    channels.set(id, c)
    let warned = false
    c.timer = setInterval(() => {
      if (c.pending) return
      c.pending = true
      void api()
        .then(() => advertise(c))
        .then(() => verify(c))
        .catch(() => {
          // Offering the application is independent from its conversation binding. Keep renewing
          // for other clients after a detach; discovery may recover, attachment is always explicit.
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
    event(c, "session", { id: session.id, resumed: false })
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
    if (request.method === "connect" || request.method === "new_session") await connect(request.channel, params)
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
        await verify(c)
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
  } catch {
    // Never forward SDK/host errors: their details can include credentials or endpoints.
    send({
      channel: request.channel,
      event: "error",
      data: { message: "Connection or action failed. Check OpenCode and Houdini, then choose Connect." },
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
