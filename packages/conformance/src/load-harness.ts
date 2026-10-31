// Child process of checkPluginLoad: imports a plugin entry, runs its setup
// with an inert context that records RPC registrations, calls `status` once,
// runs the cleanup and prints one JSON line. The parent isolates HOME and XDG.
// Process launches, listeners, outbound connections and fetch are blocked and
// reported while the plugin loads (PKG-6).
// Usage: node load-harness.ts <entry file URL> <options JSON>

import childProcess from "node:child_process"
import http from "node:http"
import https from "node:https"
import { syncBuiltinESMExports } from "node:module"
import net from "node:net"

type Registration = { id: string; methods: string[]; events: string[] }

const sideEffects: string[] = []
let guarding = true
const blocked = (name: string) => {
  if (guarding) {
    sideEffects.push(name)
    throw new Error(`${name} is not allowed while loading`)
  }
}
const guard = <T extends object>(owner: T, names: string[], label: string) => {
  const target = owner as Record<string, unknown>
  for (const name of names) {
    const original = target[name]
    if (typeof original !== "function") continue
    target[name] = function (this: unknown, ...args: unknown[]) {
      blocked(`${label}.${name}`)
      return (original as (...a: unknown[]) => unknown).apply(this, args)
    }
  }
}
guard(childProcess, ["spawn", "spawnSync", "exec", "execSync", "execFile", "execFileSync", "fork"], "child_process")
guard(net, ["connect", "createConnection"], "net")
guard(net.Server.prototype, ["listen"], "net.Server")
guard(http, ["request", "get"], "http")
guard(https, ["request", "get"], "https")
guard(globalThis, ["fetch"], "globalThis")
// ESM named imports of builtins see the guarded functions.
syncBuiltinESMExports()

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
guarding = false
result.sideEffects = sideEffects
process.stdout.write(`${JSON.stringify(result)}\n`)
// Plugins may leave timers or sockets behind; the result is complete.
process.exit(0)
