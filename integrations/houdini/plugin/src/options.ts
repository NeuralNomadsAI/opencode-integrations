// Plugin settings: the `options` of the plugin entry first, `NEURALNOMADS_HOUDINI_*` variables as a fallback.

export type HoudiniOptions = {
  // The Houdini user preferences folder to inspect and install into, instead of the newest one found.
  hostInspectionDir?: string
  // Longest wait for one host request (a tool may give a longer one, such as a verification).
  requestTimeoutMs: number
}

// The installed part waits up to an hour for Houdini's main thread to finish a request; a call waits this long by
// default, then says whether Houdini still runs it (status).
const DEFAULT_TIMEOUT_MS = 120_000

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined
}

export function readOptions(options: Record<string, unknown> | undefined, env: NodeJS.ProcessEnv): HoudiniOptions {
  const source = options ?? {}
  const timeout = Number(source.requestTimeoutMs ?? env.NEURALNOMADS_HOUDINI_REQUEST_TIMEOUT_MS)
  return {
    hostInspectionDir: text(source.hostInspectionDir) ?? text(env.NEURALNOMADS_HOUDINI_HOST_INSPECTION_DIR),
    requestTimeoutMs: Number.isFinite(timeout) && timeout >= 1_000 ? Math.min(timeout, 600_000) : DEFAULT_TIMEOUT_MS,
  }
}
