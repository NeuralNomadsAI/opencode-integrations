// Isolated Blender runs for tests: a private temporary root holds Blender's user resources, the
// temporary folder (so Blender's quit.blend never lands in the user's), HOME and XDG folders.
// System script and extension overrides are cleared. The executable comes from BLENDER_TEST_BIN;
// tests that need Blender skip without it.

import { type ChildProcess, spawn } from "node:child_process"
import { cpSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, isAbsolute, join, relative, resolve } from "node:path"

import { payloadEntries } from "../../plugin/scripts/host-payload.ts"

export const blenderBin =
  process.env.BLENDER_TEST_BIN && existsSync(process.env.BLENDER_TEST_BIN) ? process.env.BLENDER_TEST_BIN : undefined

// Only operating-system process essentials cross the boundary, never provider/npm/user secrets.
const INHERITED = /^(SystemRoot|WINDIR|SystemDrive|COMSPEC|PATH|PATHEXT|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i

export type IsolatedRoot = { root: string; env: NodeJS.ProcessEnv; extensions: string; cleanup(): void }

export function isolatedRoot(prefix = "blender-test-"): IsolatedRoot {
  const root = mkdtempSync(join(tmpdir(), prefix))
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => INHERITED.test(key)))
  const folders = {
    HOME: join(root, "home"),
    USERPROFILE: join(root, "home"),
    APPDATA: join(root, "home", "AppData", "Roaming"),
    LOCALAPPDATA: join(root, "home", "AppData", "Local"),
    TEMP: join(root, "tmp"),
    TMP: join(root, "tmp"),
    TMPDIR: join(root, "tmp"),
    XDG_CONFIG_HOME: join(root, "xdg-config"),
    XDG_STATE_HOME: join(root, "xdg-state"),
    XDG_DATA_HOME: join(root, "xdg-data"),
    XDG_CACHE_HOME: join(root, "xdg-cache"),
    BLENDER_USER_RESOURCES: join(root, "blender-user"),
    BLENDER_USER_CONFIG: join(root, "blender-user", "config"),
    BLENDER_USER_SCRIPTS: join(root, "blender-user", "scripts"),
    BLENDER_USER_DATAFILES: join(root, "blender-user", "datafiles"),
    BLENDER_USER_EXTENSIONS: join(root, "blender-user", "extensions"),
  }
  for (const folder of Object.values(folders)) mkdirSync(folder, { recursive: true })
  return {
    root,
    env: { ...env, ...folders, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1" },
    extensions: join(folders.BLENDER_USER_RESOURCES, "extensions", "user_default"),
    cleanup: () => rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }),
  }
}

// Installs the add-on from its sources, exactly as the payload archive lays it out.
export function installAddon(iso: IsolatedRoot) {
  const target = join(iso.extensions, "opencode_link")
  for (const entry of payloadEntries()) {
    const file = join(target, ...entry.name.split("/"))
    mkdirSync(dirname(file), { recursive: true })
    writeFileSync(file, entry.data)
  }
  return target
}

// The sources' add-on, or with OPENCODE_LINK_DIR (a folder of this repository holding opencode_link/)
// another copy of it, to reproduce a defect against earlier bytes.
export function installTestAddon(iso: IsolatedRoot) {
  const copy = process.env.OPENCODE_LINK_DIR
  if (!copy) return installAddon(iso)
  const target = join(iso.extensions, "opencode_link")
  mkdirSync(iso.extensions, { recursive: true })
  cpSync(join(copy, "opencode_link"), target, { recursive: true })
  return target
}

export type BlenderRun = { child: ChildProcess; output: () => string; exited: Promise<number | null> }

export function runBlender(
  iso: IsolatedRoot,
  script: string,
  args: string[] = [],
  extraEnv: NodeJS.ProcessEnv = {},
  // Without factory startup Blender loads the owned profile's saved preferences and enabled add-ons;
  // `blend` opens that file from the command line, as when a user opens a saved file.
  // `timeout` bounds the run in milliseconds (150 s by default).
  {
    factoryStartup = true,
    blend,
    timeout = 150_000,
  }: { factoryStartup?: boolean; blend?: string; timeout?: number } = {},
): BlenderRun {
  if (!blenderBin) throw new Error("BLENDER_TEST_BIN is not set")
  // Additional values are test-owned Blender resources only, never arbitrary environment overrides.
  for (const [key, value] of Object.entries(extraEnv)) {
    const path = value ? relative(iso.root, resolve(value)) : ".."
    if (!/^BLENDER_(USER|SYSTEM)_[A-Z_]+$/.test(key) || !value || isAbsolute(path) || path.startsWith("..")) {
      throw new Error("Blender test overrides must be resource paths inside the owned profile")
    }
  }
  let output = ""
  const child = spawn(
    blenderBin,
    [
      "-b",
      ...(factoryStartup ? ["--factory-startup"] : []),
      "--disable-autoexec",
      ...(blend ? [blend] : []),
      "--python-exit-code",
      "3",
      "--python-expr",
      "import sys; sys.dont_write_bytecode = True",
      "--python",
      script,
      "--",
      ...args,
    ],
    {
      env: { ...iso.env, ...extraEnv, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1" },
      cwd: iso.root,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      timeout,
    },
  )
  child.stdout?.on("data", (chunk) => {
    output += chunk
  })
  child.stderr?.on("data", (chunk) => {
    output += chunk
  })
  const exited = new Promise<number | null>((resolve) => child.once("close", (code) => resolve(code)))
  return { child, output: () => output, exited }
}
