// A managed service entirely inside an owned HOME/XDG/TEMP tree; never discover the user's daemon.
import assert from "node:assert/strict"
import { spawn, spawnSync } from "node:child_process"
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import net from "node:net"
import { join } from "node:path"

import type { IsolatedRoot } from "./blender.ts"

export const opencodeBin =
  process.env.OPENCODE_TEST_BIN && existsSync(process.env.OPENCODE_TEST_BIN) ? process.env.OPENCODE_TEST_BIN : undefined
export const pluginDir = join(import.meta.dirname, "..", "..", "plugin")
export const nativeSkip = !opencodeBin
  ? "OPENCODE_TEST_BIN is not set to a real OpenCode 2.2.5 binary"
  : !existsSync(join(pluginDir, "dist", "index.js"))
    ? "build the plugin first"
    : false
export const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export async function until<T>(label: string, timeoutMs: number, probe: () => Promise<T | undefined>): Promise<T> {
  const deadline = Date.now() + timeoutMs
  let last: unknown
  while (Date.now() < deadline) {
    try {
      const value = await probe()
      if (value !== undefined) return value
    } catch (error) {
      last = error
    }
    await wait(100)
  }
  throw new Error(`timed out waiting for ${label}${last ? `: ${String(last)}` : ""}`)
}

async function freePort() {
  const server = net.createServer()
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject)
    server.listen(0, "127.0.0.1", resolve)
  })
  const { port } = server.address() as net.AddressInfo
  await new Promise<void>((resolve) => server.close(() => resolve()))
  return port
}

export async function startPrivateService(iso: IsolatedRoot) {
  if (!opencodeBin) throw new Error("OPENCODE_TEST_BIN is not set")
  const globalConfig = join(iso.env.XDG_CONFIG_HOME as string, "opencode")
  const registration = join(iso.env.XDG_STATE_HOME as string, "opencode", "service.json")
  mkdirSync(globalConfig, { recursive: true })
  writeFileSync(join(globalConfig, "opencode.json"), JSON.stringify({ plugins: [{ package: pluginDir, options: {} }] }))
  const port = await freePort()
  writeFileSync(join(globalConfig, "service.json"), JSON.stringify({ port }))
  const systemFolders = {
    BLENDER_SYSTEM_SCRIPTS: join(iso.root, "blender-system-scripts"),
    BLENDER_SYSTEM_DATAFILES: join(iso.root, "blender-system-datafiles"),
    BLENDER_SYSTEM_PYTHON: join(iso.root, "blender-system-python"),
  }
  for (const folder of Object.values(systemFolders)) mkdirSync(folder, { recursive: true })
  const env = {
    ...iso.env,
    ...systemFolders,
    OPENCODE_TEST_HOME: iso.env.HOME,
    OPENCODE_CONFIG_PROJECT_DISABLE: "1",
    OPENCODE_DISABLE_CLAUDE_CODE_SKILLS: "1",
    OPENCODE_DISABLE_MODELS_FETCH: "1",
    OPENCODE_DISABLE_AUTOUPDATE: "1",
    OPENCODE_DISABLE_FFF: "1",
  }
  const version = spawnSync(opencodeBin, ["--version"], { cwd: iso.root, env, windowsHide: true, encoding: "utf8" })
  assert.equal(version.status, 0, String(version.error ?? version.stderr))
  assert.match(version.stdout.trim(), /^opencode v2\.2\.5$/)
  const child = spawn(opencodeBin, ["serve", "--service", "--print-logs"], {
    cwd: iso.root,
    env,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  })
  let logs = ""
  child.stdout.on("data", (chunk) => {
    logs = `${logs}${chunk}`.slice(-20_000)
  })
  child.stderr.on("data", (chunk) => {
    logs = `${logs}${chunk}`.slice(-20_000)
  })
  child.on("error", (error) => {
    logs += String(error)
  })
  const stopped = new Promise<void>((resolve) => child.once("close", () => resolve()))
  const close = async () => {
    if (child.exitCode !== null || child.signalCode !== null) {
      await stopped
      return
    }
    child.kill()
    let timer: ReturnType<typeof setTimeout> | undefined
    await Promise.race([
      stopped,
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, 5_000)
      }),
    ])
    clearTimeout(timer)
    if (child.exitCode === null && child.signalCode === null) {
      if (process.platform === "win32") {
        const killer = spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
          windowsHide: true,
          stdio: "ignore",
        })
        await new Promise<void>((resolve) => killer.once("close", () => resolve()))
      } else child.kill("SIGKILL")
      await stopped
    }
  }
  try {
    const info = await until("the private service registration", 60_000, async () => {
      if (child.exitCode !== null) throw new Error(`service exited: ${logs}`)
      const parsed = existsSync(registration)
        ? (JSON.parse(readFileSync(registration, "utf8")) as { url?: string; password?: string })
        : {}
      // Ignore an old registration when restarting this owned service.
      return parsed.url && new URL(parsed.url).port === String(port) && parsed.password ? parsed : undefined
    })
    const authorization = `Basic ${Buffer.from(`opencode:${info.password}`).toString("base64")}`
    const request = async (method: string, path: string, body?: unknown, directory?: string) => {
      const url = new URL(path, info.url)
      if (directory) url.searchParams.set("location[directory]", directory)
      const response = await fetch(url, {
        method,
        headers: { authorization, "content-type": "application/json" },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: AbortSignal.timeout(30_000),
      })
      const text = await response.text()
      if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${text}`)
      return text ? JSON.parse(text) : undefined
    }
    const rpc = async (directory: string, method: string, input: unknown = {}) =>
      (await request("POST", `/api/rpc/neuralnomads.blender/${method}`, { input }, directory)).output
    await until("the private plugin", 90_000, () => rpc(globalConfig, "status"))
    return { iso, globalConfig, request, rpc, close, logs: () => logs }
  } catch (error) {
    await close()
    throw new Error(`${String(error)}\n${logs}`)
  }
}
