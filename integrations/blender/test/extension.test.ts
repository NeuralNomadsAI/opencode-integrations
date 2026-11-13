// The payload archive as users get it: Blender's extension system validates it, installs it in a private
// profile with `extension install-file`, and the installed add-on's hello is judged ok by the plugin, through
// the offer list the plugin uses in OpenCode. Needs BLENDER_TEST_BIN (a Blender 5.3 executable).

import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { existsSync, readFileSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { after, it } from "node:test"

import { createRegistry, readPackage } from "../plugin/src/integration.ts"
import { readOptions } from "../plugin/src/options.ts"
import { blenderBin, isolatedRoot, runBlender } from "./helpers/blender.ts"
import { announcement, loadPlugin } from "./helpers/plugin.ts"

const pkg = readPackage()
const marker = pkg.opencodeIntegration
const host = marker.host as NonNullable<typeof marker.host>
const archive = join(import.meta.dirname, "..", "plugin", ...host.payload.split("/"))
const iso = isolatedRoot("blender-extension-")
after(() => iso.cleanup())

// One `blender --command extension ...` run in the private profile.
function extension(...args: string[]) {
  if (!blenderBin) throw new Error("BLENDER_TEST_BIN is not set")
  const run = spawnSync(blenderBin, ["--factory-startup", "--command", "extension", ...args], {
    env: iso.env,
    cwd: iso.root,
    encoding: "utf8",
    timeout: 120_000,
    windowsHide: true,
  })
  return { status: run.status, output: `${run.stdout}${run.stderr}` }
}

it("Blender validates the payload archive, installs it and its hello is judged ok", {
  skip: blenderBin ? false : "BLENDER_TEST_BIN is not set",
  timeout: 300_000,
}, async () => {
  const validated = extension("validate", archive)
  assert.equal(validated.status, 0, validated.output)
  assert.match(validated.output, /Success parsing TOML/)

  const installed = extension("install-file", "--repo", "user_default", archive)
  assert.equal(installed.status, 0, installed.output)
  const manifest = readFileSync(join(iso.extensions, "opencode_link", "blender_manifest.toml"), "utf8")
  assert.match(manifest, new RegExp(`^version = "${pkg.version.replaceAll(".", "\\.")}"$`, "m"))

  const stopFile = join(iso.root, "stop")
  const run = runBlender(iso, join(import.meta.dirname, "blender", "serve.py"), [
    "--listen-only",
    "--seconds",
    "120",
    "--stop-file",
    stopFile,
  ])
  try {
    const line = await new Promise<string>((resolve, reject) => {
      const timer = setInterval(() => {
        const found = run
          .output()
          .split("\n")
          .find((entry) => entry.startsWith("ENDPOINT "))
        if (found) {
          clearInterval(timer)
          resolve(found)
        }
      }, 100)
      void run.exited.then((code) => {
        clearInterval(timer)
        reject(new Error(`Blender exited with ${code}: ${run.output().slice(-2000)}`))
      })
    })
    const { port, token, script_handle } = JSON.parse(line.slice("ENDPOINT ".length))
    const registry = createRegistry(marker, readOptions(undefined, {}))
    const plugin = await loadPlugin({ registry })
    try {
      const endpoint = { host: "127.0.0.1" as const, port, token }
      assert.deepEqual(await plugin.rpc("advertise", announcement(endpoint, script_handle)), { outcome: "accepted" })
      const offers = (await plugin.rpc("hosts")) as { offers: Array<{ protocol: string; appVersion?: string }> }
      assert.equal(offers.offers[0]?.protocol, "ok", JSON.stringify(offers))
      const status = (await plugin.rpc("status")) as { host: { state: string; partVersion?: string } }
      assert.equal(status.host.state, "ok", JSON.stringify(status.host))
      assert.equal(status.host.partVersion, pkg.version)
    } finally {
      await plugin.cleanup()
      registry.retire()
    }
  } finally {
    writeFileSync(stopFile, "")
    await run.exited
  }
  assert.ok(existsSync(stopFile))
})
