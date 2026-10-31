// Loading check (spec/integrations.md, PKG-5 to PKG-7, MRK-6): the built
// plugin is imported and set up in a child process whose HOME and XDG
// directories are fresh temporary folders. Loading writes no file or folder
// into the OpenCode configuration directories nor into the package, launches
// no process, opens no listener or connection, registers the RPC contract
// under the marker's ID and answers `status` like the marker.

import { execFile } from "node:child_process"
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, relative, resolve } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

import { checkRpcRegistration, checkStatusOutput, type MarkerIdentity } from "./contract.ts"

export type LoadablePackage = {
  dir: string
  packageJson: { name?: string; version?: string; exports?: unknown; main?: string }
  marker: (MarkerIdentity & { plugin: string; rpc: string }) | null
}

export type LoadReport = { errors: string[]; status?: unknown }

const harness = new URL("./load-harness.ts", import.meta.url)

// The ESM entry of the package: exports["."] (import or default), else main.
export function packageEntry(pkg: LoadablePackage): string | null {
  const exportsField = pkg.packageJson.exports
  let target: unknown = exportsField
  if (typeof target === "object" && target !== null && "." in target) target = (target as Record<string, unknown>)["."]
  if (typeof target === "object" && target !== null) {
    const conditions = target as Record<string, unknown>
    target = conditions.import ?? conditions.default
  }
  if (typeof target !== "string") target = pkg.packageJson.main
  return typeof target === "string" ? resolve(pkg.dir, target) : null
}

function snapshot(root: string, skip: Set<string> = new Set()): Map<string, string> {
  const files = new Map<string, string>()
  if (!existsSync(root)) return files
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (skip.has(entry.name)) continue
      const full = join(dir, entry.name)
      if (entry.isDirectory()) {
        // An empty folder (plugins/, skills/) is enough for OpenCode's watcher.
        files.set(`${relative(root, full)}/`, "dir")
        walk(full)
      } else {
        const stat = statSync(full)
        files.set(relative(root, full), `${stat.size}:${stat.mtimeMs}`)
      }
    }
  }
  walk(root)
  return files
}

function changes(before: Map<string, string>, after: Map<string, string>): string[] {
  const changed = [...after].filter(([file, stamp]) => before.get(file) !== stamp).map(([file]) => file)
  const removed = [...before.keys()].filter((file) => !after.has(file))
  return [...changed, ...removed]
}

export async function checkPluginLoad(
  pkg: LoadablePackage,
  options: { pluginOptions?: Record<string, unknown>; timeoutMs?: number } = {},
): Promise<LoadReport> {
  const label = pkg.packageJson.name ?? pkg.dir
  const errors: string[] = []
  const entry = packageEntry(pkg)
  if (!entry || !existsSync(entry)) return { errors: [`${label}: build the package first, its entry is missing`] }

  const root = mkdtempSync(join(tmpdir(), "conformance-load-"))
  try {
    const home = join(root, "home")
    const xdg = {
      XDG_CONFIG_HOME: join(root, "xdg-config"),
      XDG_STATE_HOME: join(root, "xdg-state"),
      XDG_DATA_HOME: join(root, "xdg-data"),
      XDG_CACHE_HOME: join(root, "xdg-cache"),
    }
    // The watched OpenCode configuration directories, with a configuration file as in real use.
    const watched = [join(xdg.XDG_CONFIG_HOME, "opencode"), join(home, ".config", "opencode")]
    for (const dir of watched) {
      mkdirSync(dir, { recursive: true })
      writeFileSync(join(dir, "opencode.json"), "{}\n")
    }
    for (const dir of Object.values(xdg)) mkdirSync(dir, { recursive: true })
    const before = watched.map((dir) => snapshot(dir))
    const packageBefore = snapshot(pkg.dir, new Set(["node_modules"]))

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
      CONFORMANCE_LOCATION: watched[0],
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
    const line = stdout.trim().split("\n").at(-1) ?? ""
    let result: {
      pluginID?: unknown
      registrations?: Array<{ id: string; methods: string[]; events: string[] }>
      status?: unknown
      error?: string
      sideEffects?: string[]
    }
    try {
      result = JSON.parse(line)
    } catch {
      return { errors: [`${label}: loading did not complete`] }
    }
    const sideEffects = [...new Set(result.sideEffects ?? [])]
    if (sideEffects.length > 0) errors.push(`${label}: loading has side effects (${sideEffects.join(", ")})`)
    else if (result.error) errors.push(`${label}: loading failed: ${result.error}`)

    watched.forEach((dir, index) => {
      const written = changes(before[index] ?? new Map(), snapshot(dir))
      if (written.length > 0)
        errors.push(`${label}: loading writes into the OpenCode configuration (${written.join(", ")})`)
    })
    const packageWritten = changes(packageBefore, snapshot(pkg.dir, new Set(["node_modules"])))
    if (packageWritten.length > 0)
      errors.push(`${label}: loading writes into the package (${packageWritten.join(", ")})`)

    const marker = pkg.marker
    if (marker && !result.error) {
      if (result.pluginID !== marker.plugin) errors.push(`${label}: plugin ID must be ${marker.plugin}`)
      const registration = result.registrations?.find((candidate) => candidate.id === marker.rpc)
      if (!registration) errors.push(`${label}: no RPC registered under ${marker.rpc}`)
      else errors.push(...checkRpcRegistration(registration, marker).map((error) => `${label}: ${error}`))
      if (registration) {
        errors.push(
          ...checkStatusOutput(result.status, marker, pkg.packageJson.version).map((error) => `${label}: ${error}`),
        )
      }
    }
    return { errors, status: result.status }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}
