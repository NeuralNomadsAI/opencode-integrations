// Read-only detection of the earlier Blender installation (notice `legacy_install`): a plugin shim
// in OpenCode's global configuration, or the earlier bridge answering on its fixed loopback port.
// The port probe opens a socket, so it never runs while loading nor inside `status` (PKG-6): it runs
// with `hosts`, `installHost` and the tools, and `status` reports its last result.

import { existsSync } from "node:fs"
import net from "node:net"
import { join } from "node:path"

import { globalConfigDirs, type PathEnv } from "./paths.ts"

export const LEGACY_PORT = 9876
const PROBE_TIMEOUT_MS = 500
const LEGACY_PROTOCOL = "opencode-blender-bridge"
const SHIMS = ["blender.ts", "blender.js"]

export function legacyShimFound(paths: PathEnv): boolean {
  return globalConfigDirs(paths).some((dir) => SHIMS.some((name) => existsSync(join(dir, "plugins", name))))
}

// Whether a listener on 127.0.0.1:<port> answers the earlier bridge's ping.
export async function legacyListenerFound(port = LEGACY_PORT, timeoutMs = PROBE_TIMEOUT_MS): Promise<boolean> {
  return await new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port })
    let buffer = ""
    const finish = (found: boolean) => {
      clearTimeout(timer)
      socket.destroy()
      resolve(found)
    }
    const timer = setTimeout(() => finish(false), timeoutMs)
    socket.setEncoding("utf8")
    socket.on("connect", () => socket.write(`${JSON.stringify({ type: "ping" })}\n`))
    socket.on("data", (chunk: string) => {
      buffer += chunk
      if (buffer.length > 64 * 1024) return finish(false)
      const newline = buffer.indexOf("\n")
      if (newline < 0) return
      try {
        const reply = JSON.parse(buffer.slice(0, newline)) as { protocol?: { name?: unknown } }
        finish(reply?.protocol?.name === LEGACY_PROTOCOL)
      } catch {
        finish(false)
      }
    })
    socket.on("error", () => finish(false))
    socket.on("end", () => finish(false))
  })
}

export class LegacyDetector {
  private listener = false
  private probing: Promise<void> | null = null
  private readonly paths: PathEnv
  private readonly port: number
  private readonly changed: () => void

  constructor(paths: PathEnv, port: number, changed: () => void) {
    this.paths = paths
    this.port = port
    this.changed = changed
  }

  // Synchronous and socket-free: safe inside `status`.
  found(): boolean {
    return this.listener || legacyShimFound(this.paths)
  }

  // Probes the earlier bridge's port once at a time; emits a change when the answer differs.
  refresh(): Promise<void> {
    this.probing ??= legacyListenerFound(this.port)
      .then((found) => {
        if (found !== this.listener) {
          this.listener = found
          this.changed()
        }
      })
      .finally(() => {
        this.probing = null
      })
    return this.probing
  }
}
