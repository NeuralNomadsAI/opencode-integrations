// Plugin settings: the `options` of the plugin entry first, `NEURALNOMADS_BLENDER_*` variables as a fallback.

export type BlenderOptions = {
  // The Blender extensions repository folder to inspect and install into, instead of the default.
  hostInspectionDir?: string
  // Longest wait for one host request (a tool may give a longer one, such as a verification render).
  requestTimeoutMs: number
}

const DEFAULT_TIMEOUT_MS = 120_000

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined
}

export function readOptions(options: Record<string, unknown> | undefined, env: NodeJS.ProcessEnv): BlenderOptions {
  const source = options ?? {}
  const timeout = Number(source.requestTimeoutMs ?? env.NEURALNOMADS_BLENDER_REQUEST_TIMEOUT_MS)
  return {
    hostInspectionDir: text(source.hostInspectionDir) ?? text(env.NEURALNOMADS_BLENDER_HOST_INSPECTION_DIR),
    requestTimeoutMs: Number.isFinite(timeout) && timeout >= 1_000 ? Math.min(timeout, 600_000) : DEFAULT_TIMEOUT_MS,
  }
}
