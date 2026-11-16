// OpenCode for Blender: RPC status contract, tools and the working method. Loading has no side effect:
// no socket, process, download or installation (PKG-6); the host is reached only once a session is
// attached to an offer validated by hello.

import { readFileSync } from "node:fs"
import { join, resolve } from "node:path"

import type { Plugin } from "@opencode/plugin"
import type { ToolContext } from "@opencode/plugin/promise/tool"

import { type AttachmentStorage, createAttachments, trackAdvertise } from "./attachments.ts"
import { closedMessage, crashReport, silentMessage } from "./closed.ts"
import { extensionsDir, inspectHost, installHost } from "./host-install.ts"
import {
  buildPluginTools,
  createHostSession,
  createIntegrationRpc,
  HELLO_TIMEOUT_MS,
  HostError,
  type HostExpectation,
  hostErrorWith,
  type IntegrationMarker,
  isWslDaemon,
  loadMethodRegistry,
  type OfferRegistry,
  offerRegistryName,
  readScript,
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
  import: "blender_import",
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
// A save that failed while the .blend was briefly held by another process, retried once after SAVE_RETRY_MS.
export const TRANSIENT_SAVE = /unable to make version backup|file saved with @/i
export const SAVE_RETRY_MS = 1_000
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

// The longest script blender_execute_code reads from a file, and its reading (the kit's).
export { readScript, SCRIPT_MAX_BYTES } from "./kit.ts"

// The add-on's explanation follows the hint (HostError.detail: bounded, without path separators).
const hostError = hostErrorWith(HINTS, "Blender", (error) => error.detail ?? "")

function bounded(text: string) {
  return text.length > MAX_TEXT
    ? `${text.slice(0, MAX_TEXT)}\n[truncated: ${text.length - MAX_TEXT} more characters]`
    : text
}

// After a save: what reads the file back, as written on disk (the open document can differ from it).
export function verifySavedHint(saved: true | string) {
  return `blender_verify with saved: ${saved === true ? "true" : JSON.stringify(saved)} reads what this file holds.`
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
  return offerRegistryName(APP, expected)
}

export function createRegistry(marker: IntegrationMarker, options: BlenderOptions): OfferRegistry {
  const host = marker.host as NonNullable<IntegrationMarker["host"]>
  const expected = { protocol: host.protocol, range: host.range, appVersions: host.appVersions }
  return sharedOfferRegistry(APP, {
    expected,
    hello: (endpoint) => requestHost(endpoint, "hello", helloParams(options), { timeoutMs: HELLO_TIMEOUT_MS }),
  })
}

// A fixed program's run: its result, with what the open document's file lacks and what a save wrote.
type ProgramRun = { result?: unknown; unsaved?: unknown; saved?: boolean; saved_steps?: string[] }

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

  // The conversations' attached Blender and its calls (the kit's host session): ownership, automatic and
  // restored attachment, and what a call says of a Blender that crashed, closed or does not answer.
  const wsl = deps.wsl ?? (() => isWslDaemon())
  const hostSession = createHostSession({
    texts: {
      appName: "Blender",
      app: APP,
      part: "OpenCode Link",
      partKind: "add-on",
      noneOffered: "No Blender is offered: open Blender 5.3 or later with the OpenCode Link add-on enabled.",
      installHint: `install OpenCode Link ${pkg.version} (blender_install_host) and restart Blender`,
    },
    registry,
    bindings,
    attachments,
    attachMethod,
    crash: { crashReport, closedMessage, silentMessage },
    wsl,
    requestTimeoutMs: options.requestTimeoutMs,
    hostError,
  })
  const { attachedTo, ticket, callHost, hello } = hostSession
  // A fixed program through the host's code execution, read only (programs.ts).
  const programRun = async (context: ToolContext, name: ProgramName, params: unknown, timeoutMs?: number) => {
    const out = (await callHost(
      context,
      "execute_code",
      { code: programCode(name, params), read_only: true },
      timeoutMs,
    )) as ProgramRun & { status?: string; message?: string; traceback?: string }
    if (out.status === "failed")
      throw new Error(`blender_${name}: ${String(out.message ?? "")}\n${String(out.traceback ?? "").slice(-1200)}`)
    return out
  }
  const program = async (context: ToolContext, name: ProgramName, params: unknown, timeoutMs?: number) =>
    (await programRun(context, name, params, timeoutMs)).result

  // A save, and what it wrote: the OpenCode steps now in the file, and what the open document still lacks
  // (a copy leaves the open file as it was).
  const save = async (context: ToolContext, args: Record<string, unknown>) => {
    const out = await programRun(context, "save", args)
    const result = out.result as Record<string, unknown>
    return {
      ...result,
      // A save of the open file always says what it wrote of OpenCode's (none: an empty list).
      ...(out.saved ? { wrote_steps: out.saved_steps ?? [] } : {}),
      ...(out.unsaved ? { unsaved: out.unsaved } : {}),
      check: verifySavedHint(result.copy === true ? String(result.saved) : true),
    }
  }

  // Replays of a command_id without its revision answer the first result (the host replays its own).
  const commandLog = new Map<string, string>()

  const executors: Record<
    string,
    (args: Record<string, unknown>, context: ToolContext) => Promise<string | ToolContent[]>
  > = {
    async blender_hosts(args, context) {
      const out = await hostSession.hosts(args, context, {
        allow: (held, offer) => assert(held, PERMISSIONS.attach, [offer.label], { label: offer.label }),
        status: async () => {
          void legacy.refresh()
          return rpc.status()
        },
        list: "blenders",
      })
      return json(out)
    },

    async blender_install_host(_args, context) {
      await assert(context, PERMISSIONS.install, ["*"])
      return json(await handlers.installHost?.({}))
    },

    async blender_context(args, context) {
      const { release_quarantine: release, ...query } = args
      if (release === undefined)
        return json({
          ...((await callHost(context, "scene", query)) as object),
          attached_to: attachedTo(context.sessionID),
        })
      // After a failed change whose undo could not be proven: the agent read the scene (this revision) and
      // lifts the quarantine; the scene is read again with it.
      const released = (await callHost(context, "release", { revision: release })) as { released?: boolean }
      return json({
        ...((await callHost(context, "scene", query)) as object),
        attached_to: attachedTo(context.sessionID),
        released: released.released === true,
      })
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
      const out = (await callHost(
        context,
        "execute_code",
        {
          code,
          ...(script ? { filename: script.path } : {}),
          ...(args.read_only === true ? { read_only: true } : {}),
        },
        seconds ? seconds * 1_000 : undefined,
        held,
      )) as Record<string, unknown>
      // A run that saved the open file: how to read back what the file now holds (a fact, not a step to take).
      return json(out.saved === true ? { ...out, check: verifySavedHint(true) } : out)
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
      if (
        args.type === undefined &&
        args.operator === undefined &&
        args.data === undefined &&
        args.search === undefined
      )
        throw new Error("Give type, operator, data and/or search.")
      return json(await program(context, "inspect", args))
    },

    async blender_verify(args, context) {
      // saved: the file as written on disk, read by a background Blender, instead of the open document.
      const saved = args.saved !== undefined && args.saved !== false
      if (saved && args.saved !== true && typeof args.saved !== "string")
        throw new Error("saved: true (the open file as saved) or a .blend path.")
      const run = await programRun(context, saved ? "verify_saved" : "verify", args, VERIFY_TIMEOUT_MS)
      const { image_base64, ...rest } = run.result as { image_base64?: string } & Record<string, unknown>
      // Criteria first: what passed and failed, measured, then what the saved file lacks, then the facts.
      const { blender, facts, ...checks } = rest
      const text = json({ blender, ...checks, ...(run.unsaved ? { unsaved: run.unsaved } : {}), facts })
      if (!image_base64) return text
      return [
        { type: "text", text },
        { type: "file", uri: `data:image/png;base64,${image_base64}`, mime: "image/png", name: "verify.png" },
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

    async blender_import(args, context) {
      // A task from an earlier import: its state, waited for up to wait_s, or cancelled.
      const follow = async (id: string) => {
        const until = Date.now() + Math.min(Math.max(Number(args.wait_s ?? 0), 0), RENDER_WAIT_MAX_S) * 1_000
        let task = (await callHost(context, "tasks", {
          task_id: id,
          ...(args.cancel === true ? { cancel: true } : {}),
        })) as { state?: string }
        while (task.state === "running" && Date.now() < until && !context.signal.aborted && args.cancel !== true) {
          const seconds = Math.max(1, Math.min(60, Math.ceil((until - Date.now()) / 1_000)))
          task = (await callHost(context, "tasks", { task_id: id, wait_seconds: seconds }, (seconds + 30) * 1_000)) as {
            state?: string
          }
        }
        return task
      }
      if (typeof args.task === "string") return json(await follow(args.task))
      const source = args.source
      if (source !== "blenderkit" && source !== "sketchfab" && source !== "file")
        throw new Error("source is required: blenderkit, sketchfab or file (or give task).")
      const searching = args.action === "search"
      let op: string
      let opArgs: Record<string, unknown>
      let resource: string
      if (searching) {
        if (source === "file") throw new Error("search is for blenderkit and sketchfab; a file is imported by path.")
        if (typeof args.query !== "string" || !args.query.trim()) throw new Error("search needs query.")
        const page = Number.isInteger(args.page) ? (args.page as number) : 1
        op = source === "blenderkit" ? "addons.search_blenderkit_assets" : "addons.search_sketchfab_models"
        opArgs =
          source === "blenderkit"
            ? {
                query: args.query,
                page,
                asset_type: args.asset_type ?? "model",
                ...(args.free_only === true ? { free_only: true } : {}),
              }
            : { query: args.query, page, downloadable: true }
        resource = ""
      } else if (source === "file") {
        if (typeof args.path !== "string" || !args.path) throw new Error("import from file needs path.")
        const remote = /^https:\/\//i.test(args.path)
        const path = remote ? args.path : resolve(ctx.location?.directory ?? process.cwd(), args.path)
        op = "assets.import_file"
        opArgs = { source: path }
        resource = path
      } else {
        if (typeof args.id !== "string" || !args.id) throw new Error(`import from ${source} needs id (from a search).`)
        if (source === "blenderkit") {
          op = "addons.import_blenderkit_asset"
          opArgs = {
            asset_base_id: args.id,
            ...(args.asset_type ? { asset_type: args.asset_type } : {}),
            ...(typeof args.target_object === "string" ? { target_object: args.target_object } : {}),
            ...(Array.isArray(args.location) ? { model_location: args.location } : {}),
          }
        } else {
          op = "addons.import_sketchfab_model"
          opArgs = { uid: args.id }
        }
        resource = `${source}:${args.id}`
      }
      const held = await ticket(context)
      if (!searching) await assert(context, PERMISSIONS.import, [resource], { source, id: args.id, path: args.path })
      const result = (await callHost(
        context,
        "apply",
        {
          command_id: `i-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
          if_revision: (await hello(held.bound, context.signal)).revision,
          operations: [{ op, args: opArgs }],
        },
        undefined,
        held,
      )) as { status?: string; message?: string; results?: Array<{ result?: Record<string, unknown> }> }
      if (result.status !== "applied") throw new Error(`blender_import: ${String(result.message ?? result.status)}`)
      const out = result.results?.[0]?.result ?? {}
      // The add-on's task snapshot names its task task_id; wait_s holds on this first call too.
      const task = out.task as { task_id?: string; state?: string } | undefined
      if (!searching && typeof task?.task_id === "string" && task.state === "running" && Number(args.wait_s ?? 0) > 0)
        return json({ ...out, task: await follow(task.task_id) })
      return json(out)
    },

    async blender_save(args, context) {
      try {
        return json(await save(context, args))
      } catch (error) {
        // Blender renames the file aside while saving; another process holding it a moment (a background render
        // reading it) fails that rename, and the save changed nothing: once more, a second later, and said.
        const message = error instanceof Error ? error.message : String(error)
        if (!TRANSIENT_SAVE.test(message)) throw error
        await new Promise((done) => setTimeout(done, SAVE_RETRY_MS))
        return json({ ...(await save(context, args)), retried: message.split("\n")[0]?.slice(0, 300) })
      }
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
  // again (the host session's maintain).
  const { maintain } = hostSession

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
