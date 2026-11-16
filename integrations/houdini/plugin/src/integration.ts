// OpenCode for Houdini: RPC status contract, tools and the working method. Loading has no side effect: no
// socket, process or installation; the host is reached only once a conversation is attached to an offer validated
// by hello. The conversations' attachment and host calls are the kit's host session, as for Blender.

import { readFileSync } from "node:fs"
import { join } from "node:path"

import type { Plugin } from "@opencode/plugin"
import type { ToolContext } from "@opencode/plugin/promise/tool"

import { closedMessage, crashReport, silentMessage } from "./closed.ts"
import { inspectHost, installHost } from "./host-install.ts"
import {
  type AttachmentStorage,
  buildPluginTools,
  createAttachments,
  createHostSession,
  createIntegrationRpc,
  HELLO_TIMEOUT_MS,
  HostError,
  hostErrorWith,
  type IntegrationMarker,
  isWslDaemon,
  loadMethodRegistry,
  type OfferRegistry,
  readScript,
  registerPluginTools,
  requestHost,
  type SessionBindings,
  sharedOfferRegistry,
  type ToolContent,
  trackAdvertise,
} from "./kit.ts"
import { legacyInstalls } from "./legacy.ts"
import { type EntryPut, methodAttacher } from "./method.ts"
import { type HoudiniOptions, readOptions } from "./options.ts"
import { defaultPathEnv, defaultPrefsDir, methodsDir, type PathEnv, packageRoot } from "./paths.ts"
import { type ProgramName, programCode } from "./programs.ts"
import { addTask, cancelTask, getTask, isRenderStart, taskStatus } from "./tasks.ts"

export const APP = "houdini"
const NAMESPACE_DESCRIPTION = "Houdini, through OpenCode Link: read and change the attached scene."

export const PERMISSIONS = {
  attach: "houdini_attach",
  install: "houdini_install_host",
  executeCode: "houdini_execute_code",
  write: "houdini_write",
} as const

const MAX_TEXT = 100_000
// A verification may cook the network, render or start a background hython on the saved file.
const VERIFY_TIMEOUT_MS = 600_000
// Longest wait of one houdini_render call that follows a task.
export const RENDER_WAIT_MAX_S = 3_600

const HINTS: Record<string, string> = {
  stale_handle:
    "The attached Houdini opened another file: call houdini_context again (houdini_hosts shows the open Houdinis).",
  stale_revision: "The scene changed since your last read: call houdini_context and use its revision.",
  expectation_failed: "An expect clause does not hold: nothing was changed.",
  mode_conflict: "houdini_apply changes the scene only in Houdini's interface: in hython, use houdini_execute_code.",
  busy: "Houdini is busy (a modal dialog, a long cook or render): try again shortly.",
  quarantined:
    "A failed change could not be proven undone: read the scene (houdini_context), check it, then give that read's revision as release_quarantine.",
  method_disabled: "Code execution is not available in the attached Houdini.",
  method_not_found:
    "The attached Houdini's OpenCode Link lacks this method: install this plugin's OpenCode Link (houdini_install_host) and restart Houdini.",
  invalid_args: "Houdini refused the request.",
  unreachable: "The attached Houdini is unreachable: it may have closed or stopped offering itself.",
  timeout:
    "Houdini did not answer in time; the change may still complete. Call houdini_context, then retry with the same command_id if it did not: a replayed command_id applies nothing twice.",
  cancelled:
    "The request was cancelled; a change may still complete in Houdini. Call houdini_context, then retry with the same command_id if it did not.",
}

// The installed part's own text and data follow the hint (the kit's message after its "Host refused" prefix).
const hostError = hostErrorWith(HINTS, "Houdini", (error: HostError) =>
  error.message.replace(/^Host refused the request \([^)]*\)[.:]?\s*/, ""),
)

export type HoudiniDeps = {
  env?: NodeJS.ProcessEnv
  paths?: PathEnv
  // A private registry for tests; the daemon shares one registry across Locations.
  registry?: OfferRegistry
  // The clock of the stored attachments' lease, for tests.
  now?: () => number
  wsl?: () => boolean
  // The installed payload's folder, for tests.
  state?: string
}

type PackageJson = { version: string; opencodeIntegration: IntegrationMarker }

export function readPackage(): PackageJson {
  return JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf8")) as PackageJson
}

// The offer list shared by the daemon's Locations, named after this version's host contract (the kit's
// sharedOfferRegistry): after an upgrade without restarting OpenCode, the new host is judged by this range.
export function createRegistry(marker: IntegrationMarker): OfferRegistry {
  const host = marker.host as NonNullable<IntegrationMarker["host"]>
  return sharedOfferRegistry(APP, {
    expected: { protocol: host.protocol, range: host.range, appVersions: host.appVersions },
    hello: (endpoint) => requestHost(endpoint, "hello", {}, { timeoutMs: HELLO_TIMEOUT_MS }),
  })
}

function bounded(text: string) {
  return text.length > MAX_TEXT
    ? `${text.slice(0, MAX_TEXT)}\n[truncated: ${text.length - MAX_TEXT} more characters]`
    : text
}

function json(value: unknown) {
  return bounded(JSON.stringify(value))
}

// After a save: what reads the file back, as written on disk (the open scene can differ from it).
export function verifySavedHint(saved: true | string) {
  return `houdini_verify with saved: ${saved === true ? "true" : JSON.stringify(saved)} reads what this file holds.`
}

type ProgramRun = { result?: unknown; status?: string; message?: string; traceback?: string; hint?: string }

export async function setupHoudini(ctx: Plugin.Context, deps: HoudiniDeps = {}) {
  const env = deps.env ?? process.env
  const paths = deps.paths ?? defaultPathEnv()
  const options: HoudiniOptions = readOptions(ctx.options as Record<string, unknown> | undefined, env)
  const pkg = readPackage()
  const marker = pkg.opencodeIntegration
  const registry = deps.registry ?? createRegistry(marker)
  const preferences = () => options.hostInspectionDir ?? defaultPrefsDir(paths) ?? undefined
  const legacy = () => legacyInstalls(paths, options.hostInspectionDir).filter((found) => found.enabled)
  const manifests = loadMethodRegistry(methodsDir)

  const rpc = createIntegrationRpc({
    marker,
    version: pkg.version,
    registry,
    nativeSession: {
      location: ctx.location,
      get: ctx.session?.get ? (input) => ctx.session.get(input as Parameters<typeof ctx.session.get>[0]) : undefined,
    },
    inspectHost: async () => inspectHost(preferences()),
    installHost: async () => installHost(marker, preferences(), deps.state),
    wsl: deps.wsl,
    notices: async () => (legacy().length ? ["legacy_install"] : []),
  })
  const bindings = rpc.bindings as SessionBindings
  // Attachments kept across an OpenCode restart and a file change (a new offer under the same key).
  const attachments = createAttachments({
    app: APP,
    registry,
    storage: (ctx as unknown as { storage?: AttachmentStorage }).storage,
    now: deps.now,
    owns: (sessionID) => bindings.snapshot(sessionID),
  })
  const handlers: Record<string, (input: unknown, context?: { signal?: AbortSignal }) => Promise<unknown>> = {
    ...rpc.handlers,
    advertise: trackAdvertise(registry, (input) => rpc.handlers.advertise?.(input) as Promise<unknown>, APP),
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

  // The method joins a conversation when it uses a Houdini (an instructions entry, where OpenCode has them).
  const instructions = (ctx as unknown as { session?: { instructions?: { entry?: { put?: EntryPut } } } }).session
  const put = instructions?.instructions?.entry?.put
  const attachMethod = methodAttacher(put ? (input) => put.call(instructions?.instructions?.entry, input) : undefined)

  const hostSession = createHostSession({
    texts: {
      appName: "Houdini",
      app: APP,
      part: "OpenCode Link",
      partKind: "OpenCode Link",
      noneOffered:
        "No Houdini is offered: start Houdini 21 or later with OpenCode Link installed (houdini_install_host); it offers itself when it starts.",
      installHint: `install OpenCode Link ${pkg.version} (houdini_install_host) and restart Houdini`,
    },
    registry,
    bindings,
    attachments,
    attachMethod,
    crash: { crashReport, closedMessage, silentMessage },
    wsl: deps.wsl ?? (() => isWslDaemon()),
    requestTimeoutMs: options.requestTimeoutMs,
    hostError,
  })
  const { attachedTo, ticket, callHost, hello, maintain } = hostSession

  // Every host call names its conversation: the installed part keeps per-conversation cancellation.
  const call = (
    context: ToolContext,
    method: string,
    params: Record<string, unknown>,
    timeoutMs?: number,
    captured?: Parameters<typeof callHost>[4],
  ) => callHost(context, method, { session_id: context.sessionID, ...params }, timeoutMs, captured)

  // A fixed program through the host's code execution, read only (programs.ts).
  const program = async (context: ToolContext, name: ProgramName, params: unknown, timeoutMs?: number) => {
    const out = (await call(
      context,
      "execute_code",
      { code: programCode(name, params), read_only: true },
      timeoutMs,
    )) as ProgramRun
    if (out.status === "failed")
      throw new Error(
        `houdini_${name}: ${String(out.message ?? "")}${out.hint ? `\n${out.hint}` : ""}\n${String(out.traceback ?? "").slice(-1200)}`,
      )
    return out.result
  }

  const withImage = (value: unknown, name: string): string | ToolContent[] => {
    const { image_base64, ...rest } = (value ?? {}) as { image_base64?: string } & Record<string, unknown>
    const text = json(rest)
    if (typeof image_base64 !== "string" || !image_base64) return text
    return [
      { type: "text", text },
      { type: "file", uri: `data:image/png;base64,${image_base64}`, mime: "image/png", name },
    ]
  }

  const executors: Record<
    string,
    (args: Record<string, unknown>, context: ToolContext) => Promise<string | ToolContent[]>
  > = {
    async houdini_hosts(args, context) {
      const out = await hostSession.hosts(args, context, {
        allow: (held, offer) => assert(held, PERMISSIONS.attach, [offer.label], { label: offer.label }),
        status: () => rpc.status(),
        list: "houdinis",
      })
      const found = legacy()
      // Two Houdinis with the same file open offer the same label: said, so that the right one is attached.
      const labels = registry.hosts().offers.map((offer) => offer.label)
      const twice = [...new Set(labels.filter((label, index) => labels.indexOf(label) !== index))]
      return json({
        ...out,
        ...(twice.length
          ? {
              same_label: `Several Houdinis have the same file open (${twice.join(", ")}): their offers differ by offerID only; attach the one you mean, or close the others.`,
            }
          : {}),
        ...(found.length
          ? {
              legacy_install: {
                found,
                fix: 'An earlier OpenCode package also loads in Houdini (another panel and host): remove it, or set "enable": false in it, then restart Houdini.',
              },
            }
          : {}),
      })
    },

    async houdini_install_host(_args, context) {
      await assert(context, PERMISSIONS.install, ["*"])
      return json(await handlers.installHost?.({}))
    },

    async houdini_context(args, context) {
      const { release_quarantine: release, ...query } = args
      if (release === undefined)
        return json({
          ...((await call(context, "context", query)) as object),
          attached_to: attachedTo(context.sessionID),
        })
      // After a failed change whose undo could not be proven: the agent read the scene (this revision) and lifts
      // the quarantine; the scene is read again with it.
      const released = (await call(context, "release", { revision: release })) as { released?: boolean }
      return json({
        ...((await call(context, "context", query)) as object),
        attached_to: attachedTo(context.sessionID),
        released: released.released === true,
      })
    },

    // The typed intent, unchanged: a dry run by default; a change needs confirm and the write permission.
    async houdini_apply(args, context) {
      const held = await ticket(context)
      const applying = args.dry_run === false
      if (applying) {
        if (args.confirm !== true) throw new Error("A change needs dry_run: false and confirm: true.")
        await assert(context, PERMISSIONS.write, [held.label ?? "*"], { operations: args.operations })
      }
      return json(await call(context, "apply", args, undefined, applying ? held : undefined))
    },

    // The dangerous method: every run asks permission.
    async houdini_execute_code(args, context) {
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
      const out = (await call(
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
      return json(out.saved === true ? { ...out, check: verifySavedHint(true) } : out)
    },

    async houdini_inspect(args, context) {
      if (args.search === undefined && args.type === undefined && args.node === undefined && args.hou === undefined)
        throw new Error("Give search, type, node and/or hou.")
      return json(await program(context, "inspect", args))
    },

    async houdini_verify(args, context) {
      // saved: the file as written on disk, read by a background hython, instead of the open scene.
      const saved = args.saved !== undefined && args.saved !== false
      if (saved && args.saved !== true && typeof args.saved !== "string")
        throw new Error("saved: true (the open file as saved) or a .hip, .hipnc or .hiplc path.")
      return withImage(await program(context, saved ? "verify_saved" : "verify", args, VERIFY_TIMEOUT_MS), "verify.png")
    },

    async houdini_render(args, context) {
      const { task: id, wait_s, cancel, ...params } = args
      if (id !== undefined) {
        const task = getTask(String(id))
        if (!task) throw new Error(`No render or PDG task ${String(id)} in this OpenCode.`)
        if (cancel === true) return json(cancelTask(task))
        // A whole render can be waited for in one call (the tool call itself has no time limit).
        const until = Date.now() + Math.min(Math.max(Number(wait_s ?? 0), 0), RENDER_WAIT_MAX_S) * 1_000
        let status = taskStatus(task)
        while (
          (status.state === "rendering" || status.state === "cooking") &&
          Date.now() < until &&
          !context.signal.aborted
        ) {
          await new Promise((resolve) => setTimeout(resolve, 500))
          status = taskStatus(task)
        }
        return json(status)
      }
      if (params.rop === undefined && params.top === undefined)
        throw new Error(
          "Give rop (a render node) or top (a PDG network or node) to start a task, or task to follow one.",
        )
      const started = await program(context, "render", params)
      if (!isRenderStart(started)) throw new Error(`houdini_render: the render did not start: ${json(started)}`)
      const task = addTask(started)
      const until = Date.now() + Math.min(Math.max(Number(wait_s ?? 0), 0), RENDER_WAIT_MAX_S) * 1_000
      let status = taskStatus(task)
      while (
        (status.state === "rendering" || status.state === "cooking") &&
        Date.now() < until &&
        !context.signal.aborted
      ) {
        await new Promise((resolve) => setTimeout(resolve, 500))
        status = taskStatus(task)
      }
      return json(status)
    },

    async houdini_save(args, context) {
      const result = (await program(context, "save", args)) as Record<string, unknown>
      const copy = typeof result?.copy === "string" ? result.copy : result?.copy === true ? String(result.path) : null
      return json({ ...result, check: verifySavedHint(copy ?? true) })
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
    internals: { registry, rpc, handlers, tools: allTools, executors, options, attachments, maintain },
  })
}
