// Child process of checkPluginLoad: imports a plugin entry, runs its setup
// with an inert context that records RPC registrations, calls `status` once,
// runs the cleanup and prints one JSON line. The parent isolates HOME and XDG.
// Usage: node load-harness.ts <entry file URL> <options JSON>

type Registration = { id: string; methods: string[]; events: string[] }

function inert(): unknown {
  const target = () => {}
  return new Proxy(target, {
    get(_, property) {
      if (property === "then") return undefined
      if (property === Symbol.toPrimitive) return () => ""
      return inert()
    },
    apply() {
      return inert()
    },
  })
}

const [entry, rawOptions] = process.argv.slice(2)
const registrations: Registration[] = []
const handlers = new Map<string, Record<string, (input: unknown, context: unknown) => Promise<unknown>>>()
const events: Array<{ name: string; data: unknown }> = []

const rpc = {
  async register(
    definition: { id: string; methods: Record<string, unknown>; events: Record<string, unknown> },
    methodHandlers: Record<string, (input: unknown, context: unknown) => Promise<unknown>>,
  ) {
    registrations.push({
      id: definition.id,
      methods: Object.keys(definition.methods ?? {}),
      events: Object.keys(definition.events ?? {}),
    })
    handlers.set(definition.id, methodHandlers)
    return {
      events: {
        emit: async (name: string, data: unknown) => {
          events.push({ name, data })
        },
      },
      dispose: async () => {},
    }
  },
}

const context = new Proxy(
  {},
  {
    get(_, property) {
      if (property === "rpc") return rpc
      if (property === "options") return JSON.parse(rawOptions ?? "{}")
      if (property === "location") return { directory: process.env.CONFORMANCE_LOCATION ?? process.cwd() }
      if (property === "then") return undefined
      return inert()
    },
  },
)

const result: Record<string, unknown> = {}
try {
  const module = (await import(entry ?? "")) as { default?: { id?: unknown; setup?: unknown } }
  const plugin = module.default
  if (!plugin || typeof plugin.setup !== "function") throw new Error("the entry has no default Plugin.define export")
  result.pluginID = plugin.id
  const cleanup = await (plugin.setup as (context: unknown) => unknown)(context)
  result.registrations = registrations
  const signal = new AbortController().signal
  for (const [id, methods] of handlers) {
    if (typeof methods.status === "function") {
      result.status = await methods.status({}, { signal, error: (type: string) => ({ type }) })
      result.statusRpc = id
    }
  }
  if (typeof cleanup === "function") await cleanup()
  result.events = events
} catch (error) {
  result.error = String((error as Error)?.message ?? error)
}
process.stdout.write(`${JSON.stringify(result)}\n`)
// Plugins may leave timers or sockets behind; the result is complete.
process.exit(0)
