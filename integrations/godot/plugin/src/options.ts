// Plugin settings: the `options` of the plugin entry first, `NEURALNOMADS_GODOT_*` variables as a fallback.

export type GodotOptions = {
  // Longest wait for one host request (a tool may give a longer one, such as a code run).
  requestTimeoutMs: number
}

// OpenCode Link waits up to an hour for the editor's main loop to finish a request; a call waits this long by
// default, then says whether Godot still runs it (status).
const DEFAULT_TIMEOUT_MS = 120_000

export function readOptions(options: Record<string, unknown> | undefined, env: NodeJS.ProcessEnv): GodotOptions {
  const source = options ?? {}
  const timeout = Number(source.requestTimeoutMs ?? env.NEURALNOMADS_GODOT_REQUEST_TIMEOUT_MS)
  return {
    requestTimeoutMs: Number.isFinite(timeout) && timeout >= 1_000 ? Math.min(timeout, 600_000) : DEFAULT_TIMEOUT_MS,
  }
}
