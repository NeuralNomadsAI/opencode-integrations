import { spawn } from "node:child_process"
import { readdirSync, readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { setTimeout as sleep } from "node:timers/promises"
import { fileURLToPath, pathToFileURL } from "node:url"

export type Package = { name: string; dir: string }
type Result = { code: number; output: string }
export type Run = (args: string[], capture?: boolean) => Promise<Result>
export type Options = { dryRun: boolean; trustOnly: boolean }

export function parseArgs(args: string[]): Options {
  for (const arg of args) {
    if (!["--dry-run", "--trust-only"].includes(arg)) throw new Error(`Unknown option: ${arg}`)
  }
  return { dryRun: args.includes("--dry-run"), trustOnly: args.includes("--trust-only") }
}

export function packages(root: string): Package[] {
  return readdirSync(join(root, "integrations"), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .sort((a, b) => a.name.localeCompare(b.name))
    .flatMap((entry) => {
      const dir = `integrations/${entry.name}/plugin`
      let pkg: { name?: string; private?: boolean }
      try {
        pkg = JSON.parse(readFileSync(join(root, dir, "package.json"), "utf8"))
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return []
        throw error
      }
      if (pkg.private) return []
      if (!/^[a-z][a-z0-9-]*$/.test(entry.name) || !/^@neuralnomads\/opencode-[a-z0-9-]+$/.test(pkg.name ?? "")) {
        throw new Error(`Invalid integration package: ${dir}`)
      }
      return [{ name: pkg.name as string, dir }]
    })
}

export function supportsTrust(version: string): boolean {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version.trim())
  if (!match) return false
  return Number(match[1]) > 11 || (Number(match[1]) === 11 && Number(match[2]) >= 15)
}

export function commandPlan(pkg: Package, exists: boolean, trusted: boolean, trustOnly: boolean): string[][] {
  if (!exists && trustOnly) return []
  if (exists && !trustOnly) return []
  return [
    ...(!exists ? [["publish", "--workspace", pkg.dir, "--access", "public", "--tag", "latest"]] : []),
    ...(!trusted
      ? [
          [
            "trust",
            "github",
            pkg.name,
            "--file",
            "publish.yml",
            "--repo",
            "NeuralNomadsAI/opencode-integrations",
            "--env",
            "npm-publish",
            "--allow-publish",
            "--yes",
          ],
        ]
      : []),
    // Also repair MFA after a previous run published/trusted successfully but failed at the final access call.
    ["access", "set", "mfa=publish", pkg.name],
  ]
}

function hasTrust(output: string): boolean {
  // npm 12 emits no JSON for an empty list, and one object for its single supported configuration.
  if (!output.trim()) return false
  const value = JSON.parse(output)
  if (Array.isArray(value) && value.length === 0) return false
  if (value && typeof value === "object" && typeof value.type === "string" && typeof value.id === "string") return true
  throw new Error("Unexpected npm trust list response; refusing to replace trust")
}

export async function bootstrap(
  pkgs: Package[],
  options: Options,
  run: Run,
  log: (text: string) => void = console.log,
  pause: () => Promise<unknown> = () => sleep(2000),
): Promise<boolean> {
  const published: string[] = []
  const trusted: string[] = []
  const done: string[] = []
  const failed: string[] = []
  const pending: { pkg: Package; commands: string[][] }[] = []
  let called = false
  const registry: Run = async (args, capture) => {
    if (called) await pause()
    called = true
    return run(args, capture)
  }
  const requireSuccess = (result: Result, label: string) => {
    if (result.code !== 0) throw new Error(`${label} failed (exit ${result.code}); see npm output above`)
  }

  log(
    'At the first npm web prompt, tick "skip two-factor authentication for the next 5 minutes". Keep this terminal open for the whole batch.',
  )
  const identity = await registry(["whoami"])
  if (identity.code !== 0)
    throw new Error(
      "npm whoami failed. Run npm login, then retry with an account that can publish @neuralnomads packages and has 2FA enabled.",
    )

  for (const pkg of pkgs) {
    try {
      const view = await registry(["view", pkg.name, "version", "--json"], true)
      let exists = true
      if (view.code !== 0) {
        // Only an explicit E404 means unpublished; network/auth errors must never cause a publish.
        const error = JSON.parse(view.output).error
        if (error?.code !== "E404") throw new Error(`npm view failed (${error?.code ?? view.code})`)
        exists = false
      }
      if (exists && !options.trustOnly) {
        done.push(`${pkg.name} (published; use --trust-only to configure trust)`)
        continue
      }
      if (!exists && options.trustOnly) {
        done.push(`${pkg.name} (unpublished; run without --trust-only first)`)
        continue
      }
      let configured = false
      if (exists) {
        const list = await registry(["trust", "list", pkg.name, "--json"], true)
        requireSuccess(list, `trust list ${pkg.name}`)
        configured = hasTrust(list.output)
        if (configured) done.push(`${pkg.name} (trust exists; only enforcing MFA)`)
      }
      pending.push({ pkg, commands: commandPlan(pkg, exists, configured, options.trustOnly) })
    } catch (error) {
      failed.push(`${pkg.name}: ${(error as Error).message}`)
    }
  }

  if (pending.length && !options.dryRun) {
    for (const args of [["ci"], ["run", "build"], ["run", "check:markers"]]) {
      requireSuccess(await run(args), args.join(" "))
    }
  }
  for (const { pkg, commands } of pending) {
    try {
      for (const args of commands) {
        log(`${options.dryRun ? "Would run: " : ""}npm ${args.join(" ")}`)
        if (options.dryRun) continue
        requireSuccess(await registry(args), `${args[0]} ${pkg.name}`)
        if (args[0] === "publish") published.push(pkg.name)
        if (args[0] === "trust") trusted.push(pkg.name)
      }
    } catch (error) {
      failed.push(`${pkg.name}: ${(error as Error).message}; retry --trust-only if publication succeeded`)
    }
  }
  for (const [label, items] of [
    ["Published", published],
    ["Trusted", trusted],
    ["Already done / skipped", done],
    ["Failed", failed],
  ] as const) {
    log(`${label}: ${items.length}${items.length ? `\n  ${items.join("\n  ")}` : ""}`)
  }
  return failed.length === 0
}

// Mutating commands inherit all terminal streams. Read-only JSON queries tee stdout, preserving auth URLs/errors.
function terminalRun(root: string, useNpx: boolean): Run {
  return async (args, capture = false) => {
    const command = useNpx ? "npx" : "npm"
    const argv = useNpx ? ["--yes", "npm@12", ...args] : args
    // Windows .cmd shims need a shell. Only fixed switches and validated workspace/package names reach it.
    if (argv.some((arg) => !/^[a-zA-Z0-9@/_.=:-]+$/.test(arg))) throw new Error("Unsafe npm argument")
    return new Promise((resolve, reject) => {
      const child = spawn(command, argv, {
        cwd: root,
        shell: process.platform === "win32",
        stdio: ["inherit", capture ? "pipe" : "inherit", "inherit"],
      })
      let output = ""
      child.stdout?.on("data", (chunk: Buffer) => {
        output += chunk.toString()
        process.stdout.write(chunk)
      })
      child.on("error", reject)
      child.on("close", (code) => resolve({ code: code ?? 1, output }))
    })
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  if (!process.stdin.isTTY || !process.stdout.isTTY || !process.stderr.isTTY) {
    throw new Error(
      "An interactive TTY is required. Run npm run npm:bootstrap in your terminal so npm can show its authentication URL.",
    )
  }
  const root = dirname(dirname(fileURLToPath(import.meta.url)))
  const local = terminalRun(root, false)
  const version = await local(["--version"], true)
  if (version.code !== 0) throw new Error("Cannot determine npm version")
  const useNpx = !supportsTrust(version.output)
  if (useNpx)
    console.log("npm trust requires npm >=11.15. Using npx --yes npm@12 for this batch; your global npm is unchanged.")
  if (!(await bootstrap(packages(root), options, terminalRun(root, useNpx)))) process.exitCode = 1
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error: Error) => {
    console.error(`npm-bootstrap: ${error.message}`)
    process.exitCode = 1
  })
}
