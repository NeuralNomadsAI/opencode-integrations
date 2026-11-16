// Plugin settings: the `options` of the plugin entry first, `NEURALNOMADS_GAFFER_*` variables as a fallback.

export type GafferOptions = {
  // Gaffer's GUI startup folder to inspect and install into, instead of ~/gaffer/startup/gui.
  hostStartupDir?: string
  // Longest wait for one host request (a tool may give a longer one, such as a verification).
  requestTimeoutMs: number
}

// The installed part waits up to an hour for Gaffer's main thread to finish a request; a call waits this long by
// default, then says whether Gaffer still runs it (status).
const DEFAULT_TIMEOUT_MS = 120_000

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined
}

export function readOptions(options: Record<string, unknown> | undefined, env: NodeJS.ProcessEnv): GafferOptions {
  const source = options ?? {}
  const timeout = Number(source.requestTimeoutMs ?? env.NEURALNOMADS_GAFFER_REQUEST_TIMEOUT_MS)
  return {
    hostStartupDir: text(source.hostStartupDir) ?? text(env.NEURALNOMADS_GAFFER_HOST_STARTUP_DIR),
    requestTimeoutMs: Number.isFinite(timeout) && timeout >= 1_000 ? Math.min(timeout, 600_000) : DEFAULT_TIMEOUT_MS,
  }
}
