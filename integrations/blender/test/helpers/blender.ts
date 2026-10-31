// Isolated Blender runs for tests: a private temporary root holds Blender's user resources, the
// temporary folder (so Blender's quit.blend never lands in the user's), HOME and XDG folders.
// System script and extension overrides are cleared. The executable comes from BLENDER_TEST_BIN;
// tests that need Blender skip without it.

import { type ChildProcess, spawn } from "node:child_process"
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"

import { payloadEntries } from "../../plugin/scripts/host-payload.ts"

export const blenderBin =
  process.env.BLENDER_TEST_BIN && existsSync(process.env.BLENDER_TEST_BIN) ? process.env.BLENDER_TEST_BIN : undefined

// Variables a test run never inherits: Blender overrides and anything else pointing at user folders.
const DROPPED =
  /^(BLENDER_|PYTHON|XDG_|OPENCODE_|NEURALNOMADS_|APPDATA$|LOCALAPPDATA$|HOME$|USERPROFILE$|TEMP$|TMP$|TMPDIR$)/i

export type IsolatedRoot = { root: string; env: NodeJS.ProcessEnv; extensions: string; cleanup(): void }

export function isolatedRoot(prefix = "blender-test-"): IsolatedRoot {
  const root = mkdtempSync(join(tmpdir(), prefix))
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !DROPPED.test(key)))
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
  }
  for (const folder of Object.values(folders)) mkdirSync(folder, { recursive: true })
  return {
    root,
    env: { ...env, ...folders },
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

export type BlenderRun = { child: ChildProcess; output: () => string; exited: Promise<number | null> }

export function runBlender(
  iso: IsolatedRoot,
  script: string,
  args: string[] = [],
  extraEnv: NodeJS.ProcessEnv = {},
): BlenderRun {
  if (!blenderBin) throw new Error("BLENDER_TEST_BIN is not set")
  let output = ""
  const child = spawn(
    blenderBin,
    ["-b", "--factory-startup", "--python-exit-code", "3", "--python", script, "--", ...args],
    { env: { ...iso.env, ...extraEnv }, cwd: iso.root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] },
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
