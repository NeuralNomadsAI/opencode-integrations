// Loads a built plugin in a child process with fresh HOME and XDG folders and reads what CodeNomad reads: the
// plugin ID, the RPC registered under the marker's `rpc`, and its `status` answer.

import { execFile } from "node:child_process"
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

import { checkRpcRegistration, checkStatusOutput, type MarkerIdentity } from "./status.ts"

export type LoadablePackage = {
  dir: string
  packageJson: { name?: string; version?: string; exports?: unknown; main?: string }
  marker: (MarkerIdentity & { plugin: string; rpc: string }) | null
}

export type LoadReport = { errors: string[]; status?: unknown }

const harness = new URL("./load-harness.ts", import.meta.url)

// The ESM entry of the package: exports["."] (import or default), else main.
export function packageEntry(pkg: LoadablePackage): string | null {
  let target: unknown = pkg.packageJson.exports
  if (typeof target === "object" && target !== null && "." in target) target = (target as Record<string, unknown>)["."]
  if (typeof target === "object" && target !== null) {
    const conditions = target as Record<string, unknown>
    target = conditions.import ?? conditions.default
  }
  if (typeof target !== "string") target = pkg.packageJson.main
  return typeof target === "string" ? resolve(pkg.dir, target) : null
}

export async function checkPluginLoad(
  pkg: LoadablePackage,
  options: { pluginOptions?: Record<string, unknown>; timeoutMs?: number } = {},
): Promise<LoadReport> {
  const label = pkg.packageJson.name ?? pkg.dir
  const entry = packageEntry(pkg)
  if (!entry || !existsSync(entry)) return { errors: [`${label}: build the package first, its entry is missing`] }
  const root = mkdtempSync(join(tmpdir(), "integration-load-"))
  try {
    const home = join(root, "home")
    const xdg = {
      XDG_CONFIG_HOME: join(root, "xdg-config"),
      XDG_STATE_HOME: join(root, "xdg-state"),
      XDG_DATA_HOME: join(root, "xdg-data"),
      XDG_CACHE_HOME: join(root, "xdg-cache"),
    }
    // The global configuration Location, with a configuration file as in real use.
    const location = join(xdg.XDG_CONFIG_HOME, "opencode")
    for (const dir of [home, location, ...Object.values(xdg)]) mkdirSync(dir, { recursive: true })
    writeFileSync(join(location, "opencode.json"), "{}\n")
    const env: NodeJS.ProcessEnv = {
      PATH: process.env.PATH,
      SystemRoot: process.env.SystemRoot,
      HOME: home,
      USERPROFILE: home,
      APPDATA: join(home, "AppData", "Roaming"),
      LOCALAPPDATA: join(home, "AppData", "Local"),
      TMPDIR: root,
      TEMP: root,
      TMP: root,
      LOAD_LOCATION: location,
      ...xdg,
    }
    const stdout = await new Promise<string>((done) => {
      execFile(
        process.execPath,
        [fileURLToPath(harness), pathToFileURL(entry).href, JSON.stringify(options.pluginOptions ?? {})],
        { cwd: root, env, timeout: options.timeoutMs ?? 20_000, maxBuffer: 4 * 1024 * 1024, windowsHide: true },
        (_, out) => done(out),
      )
    })
    let result: {
      pluginID?: unknown
      registrations?: Array<{ id: string; methods: string[]; events: string[] }>
      status?: unknown
      error?: string
    }
    try {
      result = JSON.parse(stdout.trim().split("\n").at(-1) ?? "")
    } catch {
      return { errors: [`${label}: loading did not complete`] }
    }
    if (result.error) return { errors: [`${label}: loading failed: ${result.error}`] }
    const marker = pkg.marker
    if (!marker) return { errors: [], status: result.status }
    const errors: string[] = []
    if (result.pluginID !== marker.plugin) errors.push(`${label}: plugin ID is not ${marker.plugin}`)
    const registration = result.registrations?.find((candidate) => candidate.id === marker.rpc)
    if (!registration) errors.push(`${label}: no RPC registered under ${marker.rpc}`)
    else {
      errors.push(...checkRpcRegistration(registration, marker).map((error) => `${label}: ${error}`))
      errors.push(
        ...checkStatusOutput(result.status, marker, pkg.packageJson.version).map((error) => `${label}: ${error}`),
      )
    }
    return { errors, status: result.status }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}
