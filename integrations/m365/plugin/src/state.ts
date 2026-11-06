import { execFileSync } from "node:child_process"
import { createHash, randomUUID } from "node:crypto"
import { chmodSync, existsSync, lstatSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, isAbsolute, join, relative, resolve } from "node:path"
import { authOptions } from "./options.ts"

export function stateFile(name: string) {
  const root =
    process.platform === "win32"
      ? join(process.env.LOCALAPPDATA || join(homedir(), "AppData", "Local"), "NeuralNomads", "opencode-m365")
      : join(process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"), "neuralnomads", "opencode-m365")
  for (const config of [
    join(homedir(), ".config", "opencode"),
    join(process.env.XDG_CONFIG_HOME || join(homedir(), ".config"), "opencode"),
  ]) {
    const rel = relative(resolve(config), resolve(root))
    if (!rel || (!isAbsolute(rel) && rel !== ".." && !rel.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`)))
      throw new Error("Microsoft 365 state must be outside the OpenCode configuration.")
  }
  const { clientId, tenantId } = authOptions()
  const account = createHash("sha256")
    .update(`${clientId.toLowerCase()}\n${tenantId.toLowerCase()}`)
    .digest("hex")
    .slice(0, 24)
  const path = join(root, account, `${name}.json`)
  for (let parent = path; ; parent = dirname(parent)) {
    if (existsSync(parent) && lstatSync(parent).isSymbolicLink())
      throw new Error("Microsoft 365 state cannot use symbolic links.")
    if (dirname(parent) === parent) break
  }
  return path
}

function privatePath(path: string, directory: boolean) {
  if (lstatSync(path).isSymbolicLink()) throw new Error("Microsoft 365 state cannot use symbolic links.")
  if (process.platform === "win32") {
    const identity = execFileSync("whoami.exe", ["/user", "/fo", "csv", "/nh"], { encoding: "utf8", windowsHide: true })
    const sid = identity.match(/S-1-\d+(?:-\d+)+/)?.[0]
    if (!sid) throw new Error("Cannot determine the current Windows identity.")
    execFileSync("icacls.exe", [path, "/inheritance:r", "/grant:r", `*${sid}:${directory ? "(OI)(CI)" : ""}F`], {
      windowsHide: true,
      stdio: "pipe",
    })
  } else chmodSync(path, directory ? 0o700 : 0o600)
}

export function readState<T>(name: string): T | null {
  const path = stateFile(name)
  if (!existsSync(path)) return null
  if (lstatSync(path).isSymbolicLink()) throw new Error("Microsoft 365 state cannot use symbolic links.")
  return JSON.parse(readFileSync(path, "utf8")) as T
}

export function writeState(name: string, value: unknown) {
  const path = stateFile(name)
  const directory = dirname(path)
  mkdirSync(directory, { recursive: true, mode: 0o700 })
  privatePath(dirname(directory), true)
  privatePath(directory, true)
  const temporary = `${path}.${randomUUID()}.tmp`
  try {
    writeFileSync(temporary, `${JSON.stringify(value)}\n`, { flag: "wx", mode: 0o600 })
    privatePath(temporary, false)
    renameSync(temporary, path)
  } finally {
    rmSync(temporary, { force: true })
  }
}

export function removeState(name: string) {
  rmSync(stateFile(name), { force: true })
}
