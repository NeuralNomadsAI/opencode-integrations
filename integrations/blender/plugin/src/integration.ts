// OpenCode for Blender: RPC status contract, tools and the working method. Loading has no side effect:
// no socket, process, download or installation (PKG-6); the host is reached only once a session is
// attached to an offer validated by hello.

import { readFileSync, statSync } from "node:fs"
import { join, resolve } from "node:path"

import type { Plugin } from "@opencode/plugin"
import type { ToolContext } from "@opencode/plugin/promise/tool"

import { type AttachmentStorage, createAttachments, trackAdvertise } from "./attachments.ts"
import { extensionsDir, inspectHost, installHost } from "./host-install.ts"
import {
  type BindingAuthority,
  buildPluginTools,
  createIntegrationRpc,
  HELLO_TIMEOUT_MS,
  type HostBinding,
  HostError,
  type HostExpectation,
  type IntegrationMarker,
  loadMethodRegistry,
  type OfferRegistry,
  registerPluginTools,
  requestHost,
  type SessionBindings,
  sharedOfferRegistry,
  type ToolContent,
} from "./kit.ts"
import { LEGACY_PORT, LegacyDetector } from "./legacy.ts"
import { type EntryPut, methodAttacher } from "./method.ts"
import { type BlenderOptions, readOptions } from "./options.ts"
import { defaultPathEnv, methodsDir, type PathEnv, packageRoot } from "./paths.ts"
import { type ProgramName, programCode } from "./programs.ts"
import { addTask, cancelTask, getTask, type RenderStart, taskStatus } from "./tasks.ts"
import { validatePlan } from "./validate.ts"

export const APP = "blender"
const NAMESPACE_DESCRIPTION = "Blender, through the OpenCode Link add-on: read and change the attached scene."

export const PERMISSIONS = {
  attach: "blender_attach",
  install: "blender_install_host",
  executeCode: "blender_execute_code",
} as const

// The Lab tools the add-on runs (host method lab_tool, their code bundled in the add-on).
export const LAB_TOOLS = [
  "get_objects_summary",
  "get_object_detail_summary",
  "get_blendfile_summary_datablocks",
  "get_blendfile_summary_missing_files",
  "get_blendfile_summary_of_linked_libraries",
  "get_blendfile_summary_path_info",
  "get_blendfile_summary_usage_guess",
  "get_screenshot_of_area_as_image",
  "get_screenshot_of_window_as_image",
  "get_screenshot_of_window_as_json",
  "jump_to_tab_by_name",
  "jump_to_tab_by_space_type",
  "jump_to_view3d_object_by_name",
  "jump_to_view3d_object_data_by_name",
  "render_thumbnail_to_path",
  "render_viewport_to_path",
] as const

const MAX_TEXT = 100_000
const VERIFY_TIMEOUT_MS = 600_000
// Longest wait of one blender_render call that follows a task.
export const RENDER_WAIT_MAX_S = 3_600

const HINTS: Record<string, string> = {
  stale_handle:
    "The attached Blender opened another document: call blender_context again (blender_hosts shows the open Blenders).",
  stale_revision: "The document changed since your last read: call blender_context and use its revision.",
  expectation_failed: "An expect clause does not hold: nothing was changed.",
  mode_conflict: "Blender is not in Object mode: ask the user to leave Edit or Sculpt mode.",
  busy: "Blender is busy (a modal operator, render or dialog): try again shortly.",
  quarantined:
    "OpenCode Link stopped accepting changes after a failed restoration: the user must check the scene and press Reset OpenCode Link.",
  method_disabled: "Code execution is not available in the attached Blender.",
  unreachable: "The attached Blender is unreachable: it may have closed or stopped offering the session.",
  timeout:
    "Blender did not answer in time; the change may still complete. Call blender_context, then retry with the same command_id if it did not: a replayed command_id applies nothing twice.",
  cancelled:
    "The request was cancelled; a change may still complete in Blender. Call blender_context, then retry with the same command_id if it did not.",
}

export type BlenderDeps = {
  env?: NodeJS.ProcessEnv
  paths?: PathEnv
  // A private registry and clock for tests; the daemon shares one registry across Locations.
  registry?: OfferRegistry
  // The clock of the stored attachments' lease, for tests.
  now?: () => number
  legacyPort?: number
  wsl?: () => boolean
}

type PackageJson = { version: string; opencodeIntegration: IntegrationMarker }

export function readPackage(): PackageJson {
  return JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf8")) as PackageJson
}

// The longest script blender_execute_code reads from a file: the host reads requests of at most 1 MiB.
export const SCRIPT_MAX_BYTES = 900 * 1024

// A Python script on disk (absolute, or relative to the Location's directory), as UTF-8 text.
export function readScript(path: string, base?: string) {
  const file = resolve(base ?? process.cwd(), path)
  let bytes: Buffer
  try {
    if (statSync(file).size > SCRIPT_MAX_BYTES)
      throw new Error(`${file} is over ${SCRIPT_MAX_BYTES} bytes: split it, or have it exec() its parts.`)
    bytes = readFileSync(file)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") throw new Error(`No script ${file}.`)
    throw error
  }
  const code = new TextDecoder("utf-8", { fatal: true }).decode(bytes).replace(/^﻿/, "")
  return { path: file, code }
}

function hostError(error: unknown): Error {
  if (error instanceof HostError) {
    const hint = HINTS[error.code] ?? `Blender refused the request (${error.code}).`
    return new Error(error.detail ? `${hint} ${error.detail}` : hint)
  }
  return error instanceof Error ? error : new Error(String(error))
}

function bounded(text: string) {
  return text.length > MAX_TEXT
    ? `${text.slice(0, MAX_TEXT)}\n[truncated: ${text.length - MAX_TEXT} more characters]`
    : text
}

function json(value: unknown) {
  return bounded(JSON.stringify(value))
}

// The add-on's hello takes no parameter: its code execution is gated by OpenCode's permission (SEC-3).
export function helloParams(_options: BlenderOptions) {
  return {}
}

// The name of the offer list shared by the daemon's Locations. It carries the add-on contract the list judges
// hellos by: after an upgrade without restarting OpenCode, an older plugin version stays loaded in the same
// process, and a list keyed by the application alone would judge this version's add-on by the older range.
export function registryName(expected: HostExpectation) {
  return `${APP}.protocol-${expected.protocol}.part-${expected.range}.app-${expected.appVersions}`
}

export function createRegistry(marker: IntegrationMarker, options: BlenderOptions): OfferRegistry {
  const host = marker.host as NonNullable<IntegrationMarker["host"]>
  const expected = { protocol: host.protocol, range: host.range, appVersions: host.appVersions }
  return sharedOfferRegistry(registryName(expected), {
    expected,
    hello: (endpoint) => requestHost(endpoint, "hello", helloParams(options), { timeoutMs: HELLO_TIMEOUT_MS }),
  })
}

type Ticket = { authority: BindingAuthority; bound: HostBinding }
type Hello = { script_handle: string; revision: number; host_version?: string; document?: unknown }

export async function setupBlender(ctx: Plugin.Context, deps: BlenderDeps = {}) {
  const env = deps.env ?? process.env
  const paths = deps.paths ?? defaultPathEnv()
  const options = readOptions(ctx.options as Record<string, unknown> | undefined, env)
  const pkg = readPackage()
  const marker = pkg.opencodeIntegration
  const registry = deps.registry ?? createRegistry(marker, options)
  const extensions = () => extensionsDir(options.hostInspectionDir, paths)
  const manifests = loadMethodRegistry(methodsDir)

  let emitStatus: () => void = () => {}
  const legacy = new LegacyDetector(paths, deps.legacyPort ?? LEGACY_PORT, () => emitStatus())

  const rpc = createIntegrationRpc({
    marker,
    version: pkg.version,
    registry,
    nativeSession: {
      location: ctx.location,
      get: ctx.session?.get ? (input) => ctx.session.get(input as Parameters<typeof ctx.session.get>[0]) : undefined,
    },
    inspectHost: async () => inspectHost(extensions()),
    installHost: async () => {
      void legacy.refresh()
      return installHost(marker, extensions())
    },
    wsl: deps.wsl,
    notices: async () => (legacy.found() ? ["legacy_install"] : []),
  })
  const bindings = rpc.bindings as SessionBindings
  // Attachments kept across an OpenCode restart (attachments.ts), in the plugin's storage.
  const attachments = createAttachments({
    registry,
    storage: (ctx as unknown as { storage?: AttachmentStorage }).storage,
    now: deps.now,
    owns: (sessionID) => bindings.snapshot(sessionID),
  })
  const handlers: Record<string, (input: unknown, context?: { signal?: AbortSignal }) => Promise<unknown>> = {
    ...rpc.handlers,
    hosts: async (input: unknown) => {
      void legacy.refresh()
      return rpc.handlers.hosts?.(input)
    },
    advertise: trackAdvertise(registry, (input) => rpc.handlers.advertise?.(input) as Promise<unknown>),
    attachHost: async (input: unknown, context?: { signal?: AbortSignal }) => {
      const result = (await rpc.handlers.attachHost?.(input, context)) as { outcome?: string }
      const sessionID = (input as { sessionID?: unknown } | null)?.sessionID
      if (result?.outcome === "attached" && typeof sessionID === "string") await attachments.remember(sessionID)
      return result
    },
    // The stored attachment goes first, once the session is verifiably this Location's: the detach's own change
    // event must not find it and attach the conversation back.
    detachHost: async (input: unknown, context?: { signal?: AbortSignal }) => {
      const sessionID = (input as { sessionID?: unknown } | null)?.sessionID
      if (typeof sessionID === "string" && (await bindings.snapshot(sessionID, context?.signal)))
        await attachments.forget(sessionID)
      return rpc.handlers.detachHost?.(input, context)
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

  // The method joins a conversation when it uses a Blender (an instructions entry, where OpenCode has them).
  const session = (ctx as unknown as { session?: { instructions?: { entry?: { put?: EntryPut } } } }).session
  const put = session?.instructions?.entry?.put
  const attachMethod = methodAttacher(put ? (input) => put.call(session?.instructions?.entry, input) : undefined)

  // Whether a conversation other than `sessionID` is attached to the offer: the conversations this daemon recorded
  // (attachments.ts); with one offer, also the registry's count of attached sessions, which are all on it.
  const heldElsewhere = (offerID: string, sessionID: string) => {
    if (attachments.heldBy(offerID, sessionID)) return true
    const offers = registry.hosts().offers
    if (offers.length !== 1 || offers[0]?.offerID !== offerID) return false
    return registry.counts().attachedSessions - (registry.binding(sessionID)?.offerID === offerID ? 1 : 0) > 0
  }

  // The conversation's attached Blender. Without one, the only offered Blender, compatible and attached to no
  // conversation, attaches itself (spec FAM-5): checked against the ownership snapshot with no wait in between.
  const ticket = async (context: ToolContext): Promise<Ticket> => {
    const authority = await bindings.snapshot(context.sessionID, context.signal)
    if (!authority || context.signal.aborted || !registry.current(authority)) {
      throw new Error("This conversation's ownership is unverifiable: try again.")
    }
    let bound = registry.binding(context.sessionID)
    // Its stored attachment first: the Blender it used before an OpenCode restart.
    if (!bound && attachments.has(context.sessionID)) {
      await attachments.restore(context.sessionID)
      bound = registry.binding(context.sessionID)
    }
    if (!bound) {
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
        if (!offers.length)
          throw new Error("No Blender is offered: open Blender 5.3 or later with the OpenCode Link add-on enabled.")
        const list = offers
          .map(
            (offer) =>
              `${offer.label} [${offer.offerID}]${heldElsewhere(offer.offerID, context.sessionID) ? " (attached to another conversation)" : ""}${offer.protocol === "incompatible" ? " (incompatible add-on)" : ""}`,
          )
          .join(", ")
        throw new Error(
          `No Blender is attached to this conversation. Offered: ${list}. Attach one with blender_hosts attach.`,
        )
      }
    }
    const current = registry.authority()
    if (!current) throw new Error("This conversation's ownership is unverifiable: try again.")
    await attachMethod(context.sessionID)
    return { authority: current, bound }
  }

  const callHost = async (
    context: ToolContext,
    method: string,
    params: Record<string, unknown>,
    timeoutMs = options.requestTimeoutMs,
    captured?: Ticket,
  ) => {
    const held = captured ?? (await ticket(context))
    if (captured && !(await bindings.verify(context.sessionID, held.authority, context.signal))) {
      throw new Error("Application attachment changed during permission; retry the action.")
    }
    if (context.signal.aborted || !registry.current(held.authority))
      throw new Error("Application attachment is unverifiable.")
    const { bound } = held
    if (!bound.methods.includes(method)) {
      throw new Error(
        `The attached Blender's OpenCode Link has no ${method} method: install OpenCode Link ${pkg.version} (blender_install_host) and restart Blender.`,
      )
    }
    try {
      return await requestHost(
        bound.endpoint,
        method,
        { script_handle: bound.scriptHandle, ...params },
        { signal: context.signal, timeoutMs },
      )
    } catch (error) {
      throw hostError(error)
    }
  }

  const hello = async (bound: HostBinding, signal: AbortSignal) =>
    (await requestHost(bound.endpoint, "hello", {}, { signal, timeoutMs: HELLO_TIMEOUT_MS })) as Hello

  // A fixed program through the host's code execution, read only (programs.ts).
  const program = async (context: ToolContext, name: ProgramName, params: unknown, timeoutMs?: number) => {
    const out = (await callHost(
      context,
      "execute_code",
      { code: programCode(name, params), read_only: true },
      timeoutMs,
    )) as {
      status?: string
      result?: unknown
      message?: string
      traceback?: string
    }
    if (out.status === "failed")
      throw new Error(`blender_${name}: ${String(out.message ?? "")}\n${String(out.traceback ?? "").slice(-1200)}`)
    return out.result
  }

  // Replays of a command_id without its revision answer the first result (the host replays its own).
  const commandLog = new Map<string, string>()

  const executors: Record<
    string,
    (args: Record<string, unknown>, context: ToolContext) => Promise<string | ToolContent[]>
  > = {
    async blender_hosts(args, context) {
      const out: Record<string, unknown> = {}
      // Only a conversation of this Location sees and changes its attachment (RPC-12).
      if (!(await bindings.snapshot(context.sessionID, context.signal)))
        throw new Error("This conversation's ownership is unverifiable: try again.")
      if (args.detach === true) {
        await attachments.forget(context.sessionID) // first: the detach's change event must not restore it
        out.detached = (await bindings.detach(context.sessionID, context.signal)).outcome
      }
      if (typeof args.attach === "string") {
        const authority = await bindings.snapshot(context.sessionID, context.signal)
        if (!authority) throw new Error("This conversation's ownership is unverifiable: try again.")
        const offer = registry.hosts().offers.find((candidate) => candidate.offerID === args.attach)
        if (!offer) throw new Error(`No offered Blender ${String(args.attach)}: call blender_hosts again.`)
        await assert(context, PERMISSIONS.attach, [offer.label], { label: offer.label })
        if (!(await bindings.verify(context.sessionID, authority, context.signal)) || !registry.current(authority)) {
          throw new Error("The offers or this conversation changed while permission was asked: try again.")
        }
        out.attached = registry.attach(offer.offerID, context.sessionID).outcome
        if (out.attached === "attached") {
          await attachments.remember(context.sessionID)
          await attachMethod(context.sessionID)
        }
      }
      void legacy.refresh()
      const status = await rpc.status()
      const mine = registry.binding(context.sessionID)?.offerID
      out.host = status.host
      out.blenders = registry.hosts().offers.map((offer) => ({
        ...offer,
        attached:
          offer.offerID === mine
            ? "this conversation"
            : heldElsewhere(offer.offerID, context.sessionID)
              ? "another conversation"
              : null,
      }))
      return json(out)
    },

    async blender_install_host(_args, context) {
      await assert(context, PERMISSIONS.install, ["*"])
      return json(await handlers.installHost?.({}))
    },

    async blender_context(args, context) {
      return json(await callHost(context, "scene", args))
    },

    async blender_apply(args, context) {
      const { plan, commandId } = validatePlan(args)
      const held = await ticket(context)
      const key = commandId && `${held.bound.offerID}:${commandId}`
      const replay = key ? commandLog.get(key) : undefined
      if (replay) return json({ ...JSON.parse(replay), replayed: true })
      const expect = plan.expect.map((clause) => {
        if ("type" in clause || "bone" in clause)
          throw new Error("expect: OpenCode Link checks exists and absent only (type and bone are not supported).")
        return { ref: `object:${clause.ref}`, predicate: clause.exists === false ? "absent" : "exists" }
      })
      // Without if_revision, the change applies to the document as it is now (FAM-3).
      const revision = plan.if_revision ?? (await hello(held.bound, context.signal)).revision
      const result = await callHost(
        context,
        "apply",
        {
          command_id: commandId ?? `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
          if_revision: revision,
          ...(plan.summary ? { summary: plan.summary } : {}),
          ...(expect.length ? { expect } : {}),
          operations: plan.operations.map(({ op, ...opArgs }) => ({ op, args: opArgs })),
        },
        undefined,
        held,
      )
      if (key) commandLog.set(key, JSON.stringify(result))
      return json(result)
    },

    // The dangerous method (SEC-3): listed by the add-on while it is enabled; every run asks permission.
    async blender_execute_code(args, context) {
      // A script file is read here, byte for byte: no escaping of the code through a JSON or JavaScript string.
      const script =
        typeof args.path === "string" && args.path ? readScript(args.path, ctx.location?.directory) : undefined
      if (script && args.code !== undefined) throw new Error("Give code or path, not both.")
      const code = script?.code ?? String(args.code ?? "")
      if (!code.trim()) throw new Error(script ? `${script.path} is empty.` : "code or path is required.")
      const held = await ticket(context)
      if (!held.bound.methods.includes("execute_code")) throw new HostError("method_disabled", "")
      await assert(context, PERMISSIONS.executeCode, [script?.path ?? "*"], {
        ...(script ? { path: script.path } : {}),
        code: code.length > 4_000 ? `${code.slice(0, 4_000)}\n…` : code,
      })
      const seconds = typeof args.timeout_s === "number" ? Math.min(Math.max(args.timeout_s, 1), 3_600) : undefined
      return json(
        await callHost(
          context,
          "execute_code",
          {
            code,
            ...(script ? { filename: script.path } : {}),
            ...(args.read_only === true ? { read_only: true } : {}),
          },
          seconds ? seconds * 1_000 : undefined,
          held,
        ),
      )
    },

    async blender_lab(args, context) {
      const name = String(args.tool)
      if (!(LAB_TOOLS as readonly string[]).includes(name)) throw new Error(`Unknown Lab tool ${name}.`)
      const reply = (await callHost(context, "lab_tool", { tool: name, args: args.args ?? {} })) as Record<
        string,
        unknown
      >
      if (reply.status !== "ok") throw new Error(String(reply.message ?? "Blender Lab tool failed").slice(0, 3_000))
      const result = (reply.result ?? {}) as Record<string, unknown>
      if (typeof result.image_base64 !== "string") return json(reply)
      const { image_base64, ...rest } = result
      return [
        { type: "text", text: json({ ...reply, result: rest }) },
        { type: "file", uri: `data:image/png;base64,${image_base64}`, mime: "image/png", name: `${name}.png` },
      ]
    },

    async blender_inspect(args, context) {
      if (args.type === undefined && args.operator === undefined && args.data === undefined)
        throw new Error("Give type, operator and/or data.")
      return json(await program(context, "inspect", args))
    },

    async blender_verify(args, context) {
      const result = (await program(context, "verify", args, VERIFY_TIMEOUT_MS)) as {
        facts?: unknown
        image_base64?: string
        blender?: string
      }
      const text = json({ blender: result.blender, facts: result.facts })
      if (!result.image_base64) return text
      return [
        { type: "text", text },
        { type: "file", uri: `data:image/png;base64,${result.image_base64}`, mime: "image/png", name: "verify.png" },
      ]
    },

    async blender_render(args, context) {
      const { task: id, wait_s, cancel, ...params } = args
      if (id !== undefined) {
        const task = getTask(String(id))
        if (!task) throw new Error(`No render task ${String(id)} in this OpenCode.`)
        if (cancel === true) return json(cancelTask(task))
        // A whole render can be waited for in one call (the tool call itself has no time limit).
        const until = Date.now() + Math.min(Math.max(Number(wait_s ?? 0), 0), RENDER_WAIT_MAX_S) * 1_000
        let status = taskStatus(task)
        while (
          (status.state === "rendering" || status.state === "encoding") &&
          Date.now() < until &&
          !context.signal.aborted
        ) {
          await new Promise((resolve) => setTimeout(resolve, 500))
          status = taskStatus(task)
        }
        return json(status)
      }
      if (typeof params.output !== "string" || !params.output)
        throw new Error("output is required to start a render (or give task).")
      const started = (await program(context, "render", params)) as RenderStart
      const task = addTask(started)
      return json({ ...taskStatus(task), ...(started.saved_first ? { saved_first: true } : {}) })
    },

    async blender_save(args, context) {
      return json(await program(context, "save", args))
    },
  }

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

  // Renews or forgets the stored attachments and attaches back the conversations whose Blender offers itself
  // again: one run at a time, and once more when the offers changed during it.
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

  const registrations: Array<{ dispose: () => Promise<void> }> = []
  let disconnect = () => {}
  let unsubscribe = () => {}
  const cleanup = async () => {
    unsubscribe()
    disconnect()
    for (const registration of registrations.splice(0).reverse()) await registration.dispose().catch(() => {})
  }
  try {
    await attachments.load()
    const registration = await ctx.rpc.register(rpc.definition as never, handlers as never)
    registrations.push(registration)
    disconnect = rpc.connect(registration as never)
    emitStatus = () => void registration.events.emit("status_changed" as never, {} as never).catch(() => {})
    registrations.push(
      await ctx.tool.transform((editor) => registerPluginTools(editor, APP, NAMESPACE_DESCRIPTION, allTools)),
    )
    unsubscribe = registry.onChange(() => void maintain())
    void maintain()
  } catch (error) {
    await cleanup()
    throw error
  }
  return Object.assign(cleanup, {
    internals: { registry, rpc, handlers, tools: allTools, executors, legacy, options, attachments, maintain },
  })
}
