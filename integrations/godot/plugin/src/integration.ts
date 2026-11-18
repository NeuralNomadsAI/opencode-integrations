// OpenCode for Godot: RPC status contract, tools and the working method. Loading has no side effect: no socket,
// process or installation; the editor is reached only once a conversation is attached to an offer validated by
// hello. The conversations' attachment and host calls are the kit's host session, as for Blender, Houdini and Gaffer.

import { readFileSync, statSync, writeFileSync } from "node:fs"
import { isAbsolute, join, resolve } from "node:path"
import { pathToFileURL } from "node:url"

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
import { type EntryPut, methodAttacher } from "./method.ts"
import { type GodotOptions, readOptions } from "./options.ts"
import { godotProject, methodsDir, packageRoot } from "./paths.ts"
import { backgroundScript, type ProgramName, programCode } from "./programs.ts"
import {
  type Background,
  cancelTask,
  getTask,
  previewFrames,
  type Spawner,
  startBackground,
  taskStatus,
  waitResult,
  workDir,
} from "./tasks.ts"

export const APP = "godot"
const NAMESPACE_DESCRIPTION = "Godot, through OpenCode Link: read and change the attached editor's project."

export const PERMISSIONS = {
  attach: "godot_attach",
  install: "godot_install_host",
  executeCode: "godot_execute_code",
} as const

const MAX_TEXT = 100_000
// Longest wait of one godot_run call that follows a task.
export const RUN_WAIT_MAX_S = 3_600

const HINTS: Record<string, string> = {
  stale_handle: "The attached Godot editor changed: godot_hosts shows the editors offered.",
  stale_revision: "The scene changed since your last read: call godot_context and use its revision.",
  busy: "Godot is busy (a modal dialog, an import or a long run): try again shortly.",
  quarantined:
    "A failed change could not be proven undone: read the scene (godot_context), check it, then give that read's revision as release_quarantine.",
  method_disabled: "Code execution is not available in the attached Godot.",
  method_not_found:
    "The attached Godot's OpenCode Link lacks this method: install this plugin's OpenCode Link (godot_install_host) and restart the editor.",
  invalid_args: "Godot refused the request.",
  unreachable: "The attached Godot is unreachable: it may have closed or stopped offering its project.",
  timeout:
    "Godot did not answer in time; the change may still complete. Call godot_context before acting again: the scene may already hold it.",
  cancelled: "The request was cancelled; a change may still complete in Godot. Call godot_context before acting again.",
}

// The installed part's own text and data follow the hint (the kit's message after its "Host refused" prefix).
const hostError = hostErrorWith(HINTS, "Godot", (error: HostError) =>
  error.message.replace(/^Host refused the request \([^)]*\)[.:]?\s*/, ""),
)

export type GodotDeps = {
  env?: NodeJS.ProcessEnv
  // A private registry for tests; the daemon shares one registry across Locations.
  registry?: OfferRegistry
  // The clock of the stored attachments' lease, for tests.
  now?: () => number
  wsl?: () => boolean
  // How background Godots start, for tests.
  spawn?: Spawner
}

type PackageJson = { version: string; opencodeIntegration: IntegrationMarker }

export function readPackage(): PackageJson {
  return JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf8")) as PackageJson
}

// The offer list shared by the daemon's Locations, named after this version's host contract (the kit's
// sharedOfferRegistry). One Godot editor offers its project; hello names it.
export function createRegistry(marker: IntegrationMarker): OfferRegistry {
  const host = marker.host as NonNullable<IntegrationMarker["host"]>
  return sharedOfferRegistry(APP, {
    expected: { protocol: host.protocol, range: host.range, appVersions: host.appVersions },
    hello: (endpoint, scriptHandle) =>
      requestHost(endpoint, "hello", { script_handle: scriptHandle }, { timeoutMs: HELLO_TIMEOUT_MS }),
  })
}

function bounded(text: string) {
  return text.length > MAX_TEXT
    ? `${text.slice(0, MAX_TEXT)}\n[truncated: ${text.length - MAX_TEXT} more characters]`
    : text
}

const json = (value: unknown) => bounded(JSON.stringify(value))

// After a save: what reads the files back, as written on disk (the open scene can differ from them).
export function verifySavedHint(saved: true | string) {
  return `godot_verify with saved: ${saved === true ? "true" : JSON.stringify(saved)} reads what this file holds.`
}

type ProgramRun = {
  result?: unknown
  status?: string
  message?: string
  line?: number
  traceback?: string
  hint?: string
}
type EditorContext = {
  executable: string
  project: { path: string; main_scene: string }
  scene: { path: string; unsaved: boolean } | null
}

// Godot's own path form, forward slashes, for a background Godot's arguments.
const godotPath = (path: string) => path.replaceAll("\\", "/")

export async function setupGodot(ctx: Plugin.Context, deps: GodotDeps = {}) {
  const env = deps.env ?? process.env
  const options: GodotOptions = readOptions(ctx.options as Record<string, unknown> | undefined, env)
  const pkg = readPackage()
  const marker = pkg.opencodeIntegration
  const registry = deps.registry ?? createRegistry(marker)
  const manifests = loadMethodRegistry(methodsDir)
  const wsl = deps.wsl ?? isWslDaemon
  const locationProject = () => godotProject(ctx.location?.directory)

  const rpc = createIntegrationRpc({
    marker,
    version: pkg.version,
    registry,
    nativeSession: {
      location: ctx.location,
      get: ctx.session?.get ? (input) => ctx.session.get(input as Parameters<typeof ctx.session.get>[0]) : undefined,
    },
    inspectHost: async () => inspectHost(locationProject()),
    installHost: async () => installHost(marker, locationProject()),
    wsl: deps.wsl,
  })
  const bindings = rpc.bindings as SessionBindings
  // Attachments kept across an OpenCode restart and a scene change.
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

  // The method joins a conversation when it uses a Godot editor (an instructions entry, where OpenCode has them).
  const instructions = (ctx as unknown as { session?: { instructions?: { entry?: { put?: EntryPut } } } }).session
  const put = instructions?.instructions?.entry?.put
  const attachMethod = methodAttacher(put ? (input) => put.call(instructions?.instructions?.entry, input) : undefined)

  const hostSession = createHostSession({
    texts: {
      appName: "Godot",
      app: APP,
      part: "OpenCode Link",
      partKind: "OpenCode Link",
      noneOffered:
        "No Godot editor is offered: open the project in Godot 4.7 or later with OpenCode Link installed in it (godot_install_host); the editor offers its project when it starts.",
      installHint: `install OpenCode Link ${pkg.version} (godot_install_host) and restart the editor`,
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
  const { attachedTo, ticket, callHost, maintain } = hostSession

  // Every host call names its conversation: the dock's Stop drops that conversation's queued requests.
  const call = (
    context: ToolContext,
    method: string,
    params: Record<string, unknown>,
    timeoutMs?: number,
    captured?: Parameters<typeof callHost>[4],
  ) => callHost(context, method, { session_id: context.sessionID, ...params }, timeoutMs, captured)

  // A fixed program through the host's code execution, read only (programs.ts).
  const program = async (
    context: ToolContext,
    name: ProgramName,
    params: unknown,
    held?: Parameters<typeof callHost>[4],
  ) => {
    const out = (await call(
      context,
      "execute_code",
      { code: programCode(name), params, read_only: true },
      undefined,
      held,
    )) as ProgramRun
    if (out.status === "failed")
      throw new Error(
        `godot_${name}: ${String(out.message ?? "")}${out.hint ? `\n${out.hint}` : ""}${out.traceback ? `\n${String(out.traceback).slice(-1200)}` : ""}`,
      )
    return out.result
  }

  const editor = async (context: ToolContext) =>
    (await call(context, "context", { limit: 0, depth: 0 })) as EditorContext & Record<string, unknown>

  // A scene file of the project: res://, a path relative to the project, or an absolute path.
  const sceneFile = (path: string) =>
    path.startsWith("res://") || isAbsolute(path) ? godotPath(path) : `res://${godotPath(path).replace(/^\.?\//, "")}`

  // The scene a background Godot reads: a file, or a working copy of the open scene written by the editor.
  const backgroundScene = async (
    context: ToolContext,
    info: EditorContext,
    work: string,
    scene: unknown,
    source: unknown,
    held: Parameters<typeof callHost>[4],
  ) => {
    if (typeof scene === "string" && scene && scene !== "main") return sceneFile(scene)
    if (!info.scene) throw new Error("No scene is open in the editor: give scene (a scene file of the project).")
    if (source === "saved") {
      if (!info.scene.path) throw new Error("The open scene was never saved: save it (godot_save path) first.")
      return info.scene.path
    }
    const file = godotPath(join(work, "scene.tscn"))
    await program(context, "copy", { file }, held)
    return file
  }

  const follow = async (id: string, waitS: unknown, context: ToolContext) => {
    const task = getTask(id)
    if (!task) throw new Error(`No Godot task ${id} in this OpenCode.`)
    const until = Date.now() + Math.min(Math.max(Number(waitS ?? 0), 0), RUN_WAIT_MAX_S) * 1_000
    let status = taskStatus(task)
    while (status.state === "running" && Date.now() < until && !context.signal.aborted) {
      await new Promise((resolve) => setTimeout(resolve, 250))
      status = taskStatus(task)
    }
    return withFrames(task, status)
  }

  // A finished capture's frames are attachments, never only paths in the text.
  const withFrames = (task: Background, status: ReturnType<typeof taskStatus>): string | ToolContent[] => {
    const frames = status.state === "done" ? previewFrames(task) : []
    if (!frames.length) return json(status)
    return [
      { type: "text", text: json(status) },
      ...frames.map((frame) => ({
        type: "file" as const,
        uri: pathToFileURL(frame).href,
        mime: "image/png",
        name: frame.split(/[\\/]/).at(-1) ?? "frame.png",
      })),
    ]
  }

  const executors: Record<
    string,
    (args: Record<string, unknown>, context: ToolContext) => Promise<string | ToolContent[]>
  > = {
    async godot_hosts(args, context) {
      const out = await hostSession.hosts(args, context, {
        allow: (held, offer) => assert(held, PERMISSIONS.attach, [offer.label], { label: offer.label }),
        status: () => rpc.status(),
        list: "editors",
      })
      // Two editors of the same project on the same scene offer the same label: said, so that the right one is attached.
      const labels = registry.hosts().offers.map((offer) => offer.label)
      const twice = [...new Set(labels.filter((label, index) => labels.indexOf(label) !== index))]
      return json({
        ...out,
        ...(twice.length
          ? {
              same_label: `Several Godot editors show the same project and scene (${twice.join(", ")}): their offers differ by offerID only; attach the one you mean, or close the others.`,
            }
          : {}),
      })
    },

    async godot_install_host(args, context) {
      if (wsl()) return json(await handlers.installHost?.({}))
      let project: string | undefined
      if (typeof args.project === "string" && args.project) {
        project = godotProject(resolve(ctx.location?.directory ?? process.cwd(), args.project))
        if (!project) throw new Error(`${args.project} is not a Godot project folder (no project.godot).`)
      } else if (registry.binding(context.sessionID)) {
        project = godotProject((await editor(context)).project.path)
      }
      project ??= locationProject()
      await assert(context, PERMISSIONS.install, [project ?? "*"])
      return json(installHost(marker, project))
    },

    async godot_context(args, context) {
      const { release_quarantine: release, ...query } = args
      if (release === undefined)
        return json({
          ...((await call(context, "context", query)) as object),
          attached_to: attachedTo(context.sessionID),
        })
      // After a failed change whose undo could not be proven: the agent read the scene (this revision) and lifts the
      // quarantine; the scene is read again with it.
      const released = (await call(context, "release", { revision: release })) as { released?: boolean }
      return json({
        ...((await call(context, "context", query)) as object),
        attached_to: attachedTo(context.sessionID),
        released: released.released === true,
      })
    },

    // The dangerous method: every run asks permission.
    async godot_execute_code(args, context) {
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
      return json(out)
    },

    async godot_inspect(args, context) {
      if (args.search === undefined && args.class === undefined && args.node === undefined && args.tree !== true)
        throw new Error("Give search, class, node and/or tree.")
      return json(await program(context, "inspect", args))
    },

    async godot_verify(args, context) {
      const frames = typeof args.frames === "number" ? Math.min(Math.max(Math.round(args.frames), 0), 100_000) : 0
      const saved = args.saved !== undefined && args.saved !== false
      if (saved && args.saved !== true && typeof args.saved !== "string")
        throw new Error("saved: true (the open scene as saved) or a scene file.")
      const checks = { criteria: args.criteria ?? [], errors: args.errors ?? true }
      const held = await ticket(context)
      if (saved || frames || (Array.isArray(checks.criteria) && checks.criteria.length > 0))
        await assert(context, PERMISSIONS.executeCode, [held.label ?? "*"], { verification: checks, frames })
      if (!saved && !frames) return json(await program(context, "verify", checks, held))
      // A background Godot reads the file (or a working copy of the open scene, for frames): the editor stays free.
      const info = (await call(context, "context", { limit: 0, depth: 0 }, undefined, held)) as EditorContext
      const work = workDir("verify")
      const scene = await backgroundScene(
        context,
        info,
        work,
        typeof args.saved === "string" ? args.saved : undefined,
        saved ? "saved" : "copy",
        held,
      )
      const timeoutS =
        typeof args.timeout_s === "number" ? Math.min(Math.max(Math.round(args.timeout_s), 5), 3_600) : 300
      const result = join(work, "result.json")
      writeFileSync(join(work, "background.gd"), backgroundScript())
      writeFileSync(join(work, "params.json"), JSON.stringify({ ...checks, scene, frames, result: godotPath(result) }))
      const task = startBackground(
        {
          kind: "verify",
          executable: info.executable,
          project: info.project.path,
          args: [
            "--headless",
            ...(frames ? ["--fixed-fps", "60"] : []),
            "--script",
            godotPath(join(work, "background.gd")),
            "--",
            godotPath(join(work, "params.json")),
          ],
          work,
          scene,
          frames,
        },
        deps.spawn,
      )
      const measured = await waitResult(task, result, timeoutS, { signal: context.signal })
      let file: Record<string, unknown> = {}
      if (saved) {
        const path = scene.startsWith("res://") ? join(info.project.path, scene.slice("res://".length)) : scene
        try {
          const stat = statSync(path)
          file = { file: path, bytes: stat.size, saved_at: stat.mtime.toISOString() }
        } catch {}
      }
      return json({
        ...measured,
        read: {
          scene,
          ...file,
          ...(frames ? { frames } : {}),
          by: saved
            ? "a background Godot on the file as written on disk, not the open scene"
            : "a background Godot on a working copy of the open scene, run for these frames",
        },
      })
    },

    async godot_run(args, context) {
      const { task: id, wait_s, cancel } = args
      if (id !== undefined) {
        const task = getTask(String(id))
        if (task && cancel === true) return json(cancelTask(task))
        return await follow(String(id), wait_s, context)
      }
      const held = await ticket(context)
      await assert(context, "godot_run", [held.label ?? "*"], { scene: args.scene, capture: args.capture })
      const info = (await call(context, "context", { limit: 0, depth: 0 }, undefined, held)) as EditorContext
      const frames = typeof args.frames === "number" ? Math.min(Math.max(Math.round(args.frames), 1), 100_000) : 60
      const capture = args.capture === true || (typeof args.capture === "object" && args.capture !== null)
      const settings = (typeof args.capture === "object" && args.capture !== null ? args.capture : {}) as {
        fps?: unknown
        size?: unknown
      }
      const fps = typeof settings.fps === "number" ? Math.min(Math.max(Math.round(settings.fps), 1), 240) : 30
      const size = Array.isArray(settings.size) && settings.size.length === 2 ? settings.size.map(Number) : undefined
      if (args.scene === "main" && !info.project.main_scene)
        throw new Error("The project has no main scene: give scene (a scene file), or set one in the Project Settings.")
      const work = workDir(capture ? "capture" : "run")
      const scene =
        args.scene === "main" ? "" : await backgroundScene(context, info, work, args.scene, args.source, held)
      const movie = capture ? godotPath(join(work, "frame.png")) : undefined
      const task = startBackground(
        {
          kind: capture ? "capture" : "run",
          executable: info.executable,
          project: info.project.path,
          args: [
            ...(capture
              ? [
                  "--write-movie",
                  movie as string,
                  "--fixed-fps",
                  String(fps),
                  ...(size?.every((value) => Number.isInteger(value) && value >= 16 && value <= 8192)
                    ? ["--resolution", `${size[0]}x${size[1]}`]
                    : []),
                ]
              : ["--headless", "--fixed-fps", "60"]),
            "--quit-after",
            String(frames),
            ...(scene ? [scene] : []),
          ],
          work,
          scene: scene || info.project.main_scene,
          frames,
          ...(movie ? { movie } : {}),
        },
        deps.spawn,
      )
      return await follow(task.id, wait_s, context)
    },

    async godot_save(args, context) {
      const result = (await program(context, "save", args)) as Record<string, unknown>
      return json({ ...result, check: verifySavedHint(result?.copy === true ? String(result.path) : true) })
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
