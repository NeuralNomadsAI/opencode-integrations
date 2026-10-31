// OpenCode for Blender: RPC status contract, tools and skills. Loading has no side effect: no
// socket, process, download or installation (PKG-6); the host is reached only once a session
// is attached to an offer validated by hello.

import { readFileSync } from "node:fs"
import { join } from "node:path"

import type { Plugin } from "@opencode/plugin"
import type { ToolContext } from "@opencode/plugin/promise/tool"

import { AUDIT_KINDS, type AuditKind, pathInfo, resolveBlendFile, runAudit } from "./audit.ts"
import { blenderDocs } from "./docs.ts"
import { extensionsDir, inspectHost, installHost } from "./host-install.ts"
import { attachImages } from "./images.ts"
import {
  buildPluginTools,
  createIntegrationRpc,
  HELLO_TIMEOUT_MS,
  HostError,
  type IntegrationMarker,
  loadMethodRegistry,
  type MethodManifest,
  type OfferRegistry,
  registerPluginTools,
  requestHost,
  sharedOfferRegistry,
  type ToolContent,
  validateAndNormalizeArgs,
} from "./kit.ts"
import { LEGACY_PORT, LegacyDetector } from "./legacy.ts"
import { type BlenderOptions, readOptions } from "./options.ts"
import { defaultPathEnv, methodsDir, type PathEnv, packageRoot } from "./paths.ts"
import { loadSkills } from "./skills.ts"

export const APP = "blender"
const NAMESPACE_DESCRIPTION = "Blender, through the OpenCode Link add-on: read and change the attached scene."
const MAX_OPERATIONS = 50
const MAX_READS = 20

export const PERMISSIONS = {
  attach: "blender_attach",
  network: "blender_network",
  install: "blender_install_host",
  audit: "blender_audit",
  executeCode: "blender_execute_code",
} as const

const HINTS: Record<string, string> = {
  stale_handle: "Another document is open in Blender: call blender_context again.",
  stale_revision: "The document changed since your last read: call blender_context and use its revision.",
  expectation_failed: "An expect clause does not hold: nothing was changed.",
  mode_conflict: "Blender is not in Object mode: ask the user to leave Edit or Sculpt mode.",
  busy: "Blender is busy (a modal operator, render or dialog): try again shortly.",
  quarantined:
    "OpenCode Link stopped accepting changes after a failed restoration: the user must check the scene and press Reset OpenCode Link.",
  method_disabled: "Code execution is disabled.",
  unreachable: "The attached Blender is unreachable: it may have closed or stopped offering the session.",
  timeout: "Blender did not answer in time.",
}

export type BlenderDeps = {
  env?: NodeJS.ProcessEnv
  paths?: PathEnv
  // A private registry and clock for tests; the daemon shares one registry across Locations.
  registry?: OfferRegistry
  legacyPort?: number
  wsl?: () => boolean
}

type PackageJson = { version: string; opencodeIntegration: IntegrationMarker }
type HostManifest = MethodManifest & { additionalArgs?: boolean; undoable?: boolean; network?: boolean }

export function readPackage(): PackageJson {
  return JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf8")) as PackageJson
}

function hostError(error: unknown): Error {
  if (error instanceof HostError) {
    const hint = HINTS[error.code] ?? `Blender refused the request (${error.code}).`
    return new Error(error.detail ? `${hint} ${error.detail}` : hint)
  }
  return error instanceof Error ? error : new Error(String(error))
}

function text(value: unknown) {
  return JSON.stringify(value, null, 2)
}

export function helloParams(options: BlenderOptions) {
  return options.allowCodeExecution ? { code_execution: true } : {}
}

export function createRegistry(marker: IntegrationMarker, options: BlenderOptions): OfferRegistry {
  const host = marker.host as NonNullable<IntegrationMarker["host"]>
  return sharedOfferRegistry(APP, {
    expected: { protocol: host.protocol, range: host.range, appVersions: host.appVersions },
    hello: (endpoint) => requestHost(endpoint, "hello", helloParams(options), { timeoutMs: HELLO_TIMEOUT_MS }),
  })
}

// Validates one read or operation against its manifest; extra arguments only where declared.
function checkArgs(manifest: HostManifest, args: unknown, where: string) {
  if (args !== undefined && (typeof args !== "object" || args === null || Array.isArray(args))) {
    throw new Error(`${where}: args must be an object`)
  }
  const raw = (args ?? {}) as Record<string, unknown>
  const known = Object.keys(manifest.args ?? {})
  const extra = manifest.additionalArgs
    ? Object.fromEntries(Object.entries(raw).filter(([key]) => !known.includes(key)))
    : {}
  const declared = Object.fromEntries(
    Object.entries(raw).filter(([key]) => known.includes(key) || !manifest.additionalArgs),
  )
  return { ...extra, ...validateAndNormalizeArgs(manifest, declared) }
}

export async function setupBlender(ctx: Plugin.Context, deps: BlenderDeps = {}) {
  const env = deps.env ?? process.env
  const paths = deps.paths ?? defaultPathEnv()
  const options = readOptions(ctx.options as Record<string, unknown> | undefined, env)
  const pkg = readPackage()
  const marker = pkg.opencodeIntegration
  const registry = deps.registry ?? createRegistry(marker, options)
  const extensions = () => extensionsDir(options.hostInspectionDir, paths)

  const manifests = loadMethodRegistry(methodsDir)
  const hostManifest = (name: unknown, kind: "host-read" | "host-operation") => {
    const manifest = typeof name === "string" ? manifests.get(name) : null
    return manifest?.kind === kind ? (manifest as HostManifest) : null
  }

  let emitStatus: () => void = () => {}
  const legacy = new LegacyDetector(paths, deps.legacyPort ?? LEGACY_PORT, () => emitStatus())

  const rpc = createIntegrationRpc({
    marker,
    version: pkg.version,
    registry,
    inspectHost: async () => inspectHost(extensions()),
    installHost: async () => {
      void legacy.refresh()
      return installHost(marker, extensions())
    },
    wsl: deps.wsl,
    notices: async () => (legacy.found() ? ["legacy_install"] : []),
  })
  const handlers: Record<string, (input: unknown) => Promise<unknown>> = {
    ...rpc.handlers,
    hosts: async (input: unknown) => {
      void legacy.refresh()
      return rpc.handlers.hosts?.(input)
    },
  }

  const assert = async (
    context: ToolContext,
    action: string,
    resources: string[],
    metadata?: Record<string, unknown>,
  ) => {
    await ctx.permission.assert({
      sessionID: context.sessionID,
      agent: context.agent,
      action,
      resources,
      ...(metadata ? { metadata } : {}),
      source: { type: "tool", messageID: context.messageID, id: context.id },
    } as Parameters<typeof ctx.permission.assert>[0])
  }

  const binding = (context: ToolContext) => {
    const bound = registry.binding(context.sessionID)
    if (!bound) {
      throw new Error(
        "No Blender session is attached to this conversation. In Blender, turn on 'Offer this session to OpenCode' (OpenCode Link); then call blender_hosts and blender_attach, or attach it from CodeNomad.",
      )
    }
    return bound
  }

  const callHost = async (context: ToolContext, method: string, params: Record<string, unknown>) => {
    const bound = binding(context)
    try {
      return await requestHost(
        bound.endpoint,
        method,
        { script_handle: bound.scriptHandle, ...params },
        {
          signal: context.signal,
          timeoutMs: options.requestTimeoutMs,
        },
      )
    } catch (error) {
      throw hostError(error)
    }
  }

  // The dangerous method (SEC-3): present only while the plugin option is on and a live host lists it
  // (its add-on preference is on too); refused otherwise without asking; asked on every call.
  const codeCapable = () =>
    options.allowCodeExecution && registry.liveHellos().some((hello) => hello.methods?.includes("execute_code"))
  const executeCode = async (args: Record<string, unknown>, context: ToolContext) => {
    if (!codeCapable()) throw new HostError("method_disabled", "Code execution is disabled.")
    const code = String(args.code ?? "")
    binding(context)
    await assert(context, PERMISSIONS.executeCode, ["*"], { code: code.slice(0, 2000) })
    return await callHost(context, "execute_code", { script_handle: args.script_handle, code, code_execution: true })
  }

  const executors: Record<
    string,
    (args: Record<string, unknown>, context: ToolContext) => Promise<string | ToolContent[]>
  > = {
    async blender_context(args, context) {
      const reads = (args.reads as unknown[] | undefined) ?? []
      if (reads.length > MAX_READS) throw new Error(`At most ${MAX_READS} reads per call.`)
      const checked = reads.map((read, index) => {
        const entry = read as { name?: unknown; args?: unknown }
        const manifest = hostManifest(entry?.name, "host-read")
        if (!manifest) throw new Error(`reads[${index}]: unknown read ${String(entry?.name)}; see blender_catalog.`)
        return { name: manifest.name, args: checkArgs(manifest, entry.args, manifest.name) }
      })
      void legacy.refresh()
      return attachImages(await callHost(context, "context", { reads: checked }))
    },

    async blender_apply(args, context) {
      const operations = args.operations as unknown[]
      if (operations.length === 0 || operations.length > MAX_OPERATIONS) {
        throw new Error(`An apply holds 1 to ${MAX_OPERATIONS} operations.`)
      }
      const checked = operations.map((operation, index) => {
        const entry = operation as { op?: unknown; args?: unknown }
        const manifest = hostManifest(entry?.op, "host-operation")
        if (!manifest)
          throw new Error(`operations[${index}]: unknown operation ${String(entry?.op)}; see blender_catalog.`)
        return { manifest, op: manifest.name, args: checkArgs(manifest, entry.args, manifest.name) }
      })
      if (checked.length > 1 && checked.some(({ manifest }) => manifest.undoable !== true)) {
        throw new Error("An operation with side effects must be the only operation of its apply.")
      }
      const network = checked.filter(({ manifest }) => manifest.network === true).map(({ op }) => op)
      binding(context)
      if (network.length > 0) await assert(context, PERMISSIONS.network, network)
      const result = await callHost(context, "apply", {
        script_handle: args.script_handle,
        if_revision: args.if_revision,
        command_id: args.command_id,
        ...(typeof args.summary === "string" ? { summary: args.summary } : {}),
        operations: checked.map(({ op, args: opArgs }) => ({ op, args: opArgs })),
        expect: args.expect ?? [],
      })
      return text(result)
    },

    async blender_catalog(args) {
      const entries = manifests
        .list()
        .filter((manifest) => manifest.kind === "host-read" || manifest.kind === "host-operation")
        .filter((manifest) => !args.domain || manifest.domain === args.domain)
        .filter((manifest) => !args.kind || manifest.kind === `host-${args.kind}`)
        .map((manifest: HostManifest) => ({
          name: manifest.name,
          kind: manifest.kind === "host-read" ? "read" : "operation",
          description: manifest.description,
          ...(manifest.kind === "host-operation" ? { undoable: manifest.undoable === true } : {}),
          ...(manifest.network ? { network: true } : {}),
          args: manifest.args ?? {},
        }))
      return text({ count: entries.length, entries })
    },

    async blender_hosts(_args, context) {
      await assert(context, PERMISSIONS.attach, ["*"])
      void legacy.refresh()
      return text(registry.hosts())
    },

    async blender_attach(args, context) {
      const offerID = String(args.offerID)
      const offer = registry.hosts().offers.find((candidate) => candidate.offerID === offerID)
      if (!offer) return text({ outcome: "gone", hint: "No such offer: call blender_hosts again." })
      await assert(context, PERMISSIONS.attach, [offer.label], { label: offer.label })
      return text(registry.attach(offerID, context.sessionID))
    },

    async blender_detach(_args, context) {
      return text(registry.detach(context.sessionID))
    },

    async blender_status() {
      await legacy.refresh()
      return text(await rpc.status())
    },

    async blender_install_host(_args, context) {
      await assert(context, PERMISSIONS.install, ["*"])
      return text(await handlers.installHost?.({}))
    },

    async blender_docs(args) {
      return text(blenderDocs(args))
    },

    async blender_audit(args, context) {
      const kind = args.action as AuditKind
      if (!AUDIT_KINDS.includes(kind)) throw new Error("Unknown audit action.")
      const file = resolveBlendFile(args.blend_file)
      if (kind === "path_info") return text(pathInfo(file))
      await assert(context, PERMISSIONS.audit, [file])
      const seconds = typeof args.timeout_seconds === "number" ? args.timeout_seconds : undefined
      return text(
        await runAudit(kind, file, {
          blender: options.blenderPath ?? "blender",
          timeoutMs: seconds && seconds > 0 ? Math.min(seconds, 3_600) * 1_000 : undefined,
          signal: context.signal,
        }),
      )
    },

    async blender_execute_code(args, context) {
      return text(await executeCode(args, context))
    },
  }

  const dangerous = new Set(
    manifests
      .list()
      .filter((manifest) => manifest.dangerous === true)
      .map((manifest) => manifest.name),
  )
  const allTools = buildPluginTools(
    manifests.list().filter((manifest) => manifest.domain === "tools"),
    APP,
    async (manifest, args, context) => {
      const executor = executors[manifest.name]
      if (!executor) throw new Error(`No executor for ${manifest.name}`)
      try {
        return await executor(args, context)
      } catch (error) {
        throw hostError(error)
      }
    },
  )
  let codeTool = false
  const activeTools = () => allTools.filter((tool) => codeTool || !dangerous.has(`${APP}_${tool.name}`))
  const skills = loadSkills()

  const registrations: Array<{ dispose: () => Promise<void> }> = []
  let disconnect = () => {}
  let unsubscribe = () => {}
  const cleanup = async () => {
    unsubscribe()
    disconnect()
    for (const registration of registrations.splice(0).reverse()) await registration.dispose().catch(() => {})
  }
  try {
    const registration = await ctx.rpc.register(rpc.definition as never, handlers as never)
    registrations.push(registration)
    disconnect = rpc.connect(registration as never)
    emitStatus = () => void registration.events.emit("status_changed" as never, {} as never).catch(() => {})
    registrations.push(
      await ctx.tool.transform((editor) => registerPluginTools(editor, APP, NAMESPACE_DESCRIPTION, activeTools())),
    )
    const toggleCodeTool = () => {
      const next = codeCapable()
      if (next === codeTool) return
      codeTool = next
      void ctx.tool.reload().catch(() => {})
    }
    const stop = registry.onChange(toggleCodeTool)
    unsubscribe = () => void stop()
    registrations.push(
      await ctx.skill.transform((editor) => {
        // A user skill of the same name wins (PKG-2).
        for (const skill of skills) if (!editor.get(skill.id)) editor.add(skill as never)
      }),
    )
  } catch (error) {
    await cleanup()
    throw error
  }
  return Object.assign(cleanup, {
    internals: { registry, rpc, handlers, tools: allTools, activeTools, executeCode, legacy, options },
  })
}
