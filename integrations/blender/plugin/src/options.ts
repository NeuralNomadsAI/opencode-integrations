// Plugin settings: the `options` of the plugin entry first, `NEURALNOMADS_BLENDER_*` variables as a fallback.

export type BlenderOptions = {
  // The dangerous method: also needs the add-on preference; off by default.
  allowCodeExecution: boolean
  // The Blender extensions repository folder to inspect and install into, instead of the default.
  hostInspectionDir?: string
  // Blender executable for the audit tool.
  blenderPath?: string
  requestTimeoutMs: number
}

const DEFAULT_TIMEOUT_MS = 30_000

function flag(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value
  if (typeof value !== "string") return undefined
  const normalized = value.trim().toLowerCase()
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off", ""].includes(normalized)) return false
  return undefined
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined
}

export function readOptions(options: Record<string, unknown> | undefined, env: NodeJS.ProcessEnv): BlenderOptions {
  const source = options ?? {}
  const timeout = Number(source.requestTimeoutMs ?? env.NEURALNOMADS_BLENDER_REQUEST_TIMEOUT_MS)
  return {
    allowCodeExecution: flag(source.allowCodeExecution) ?? flag(env.NEURALNOMADS_BLENDER_ALLOW_CODE_EXECUTION) ?? false,
    hostInspectionDir: text(source.hostInspectionDir) ?? text(env.NEURALNOMADS_BLENDER_HOST_INSPECTION_DIR),
    blenderPath: text(source.blenderPath) ?? text(env.NEURALNOMADS_BLENDER_BIN),
    requestTimeoutMs: Number.isFinite(timeout) && timeout >= 1_000 ? Math.min(timeout, 600_000) : DEFAULT_TIMEOUT_MS,
  }
}
