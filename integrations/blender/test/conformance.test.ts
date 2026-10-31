// CNF-1: the conformance behaviour checks against this integration's own plugin and host. The
// host is the add-on's wire and core under the system Python (stub application), and also the
// real add-on in a background Blender when BLENDER_TEST_BIN names a Blender 5.2 executable.

import assert from "node:assert/strict"
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, describe, it } from "node:test"

import {
  type CodeExecutionSettings,
  checkCodeExecutionGate,
  checkHostListener,
  type ManifestLike,
  runSentinelScenario,
} from "../../../packages/conformance/src/index.ts"
import { helloParams } from "../plugin/src/integration.ts"
import { HostError, requestHost } from "../plugin/src/kit.ts"
import { readOptions } from "../plugin/src/options.ts"
import { methodsDir } from "../plugin/src/paths.ts"
import { blenderBin, installAddon, isolatedRoot, runBlender } from "./helpers/blender.ts"
import { announcement, type HostProcess, loadPlugin, startStubHost } from "./helpers/plugin.ts"

const scratch = mkdtempSync(join(tmpdir(), "blender-conformance-"))
after(() => rmSync(scratch, { recursive: true, force: true }))

function manifests(dir = methodsDir): ManifestLike[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory()
      ? manifests(join(dir, entry.name))
      : entry.name.endsWith(".json")
        ? [JSON.parse(readFileSync(join(dir, entry.name), "utf8")) as ManifestLike]
        : [],
  )
}

type HostFactory = { name: string; start(allowCode: boolean): Promise<HostProcess & { scriptHandle: string }> }

const stub: HostFactory = {
  name: "add-on core (system Python)",
  start: async (allowCode) => ({ ...(await startStubHost(allowCode)), scriptHandle: "stub-document" }),
}

const blender: HostFactory = {
  name: "add-on in Blender (background)",
  async start(allowCode) {
    const iso = isolatedRoot("blender-cnf-")
    installAddon(iso)
    const stopFile = join(iso.root, "stop")
    const run = runBlender(iso, join(import.meta.dirname, "blender", "serve.py"), [
      "--listen-only",
      "--seconds",
      "120",
      "--stop-file",
      stopFile,
      ...(allowCode ? ["--allow-code"] : []),
    ])
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
    return {
      endpoint: { host: "127.0.0.1", port, token },
      scriptHandle: script_handle,
      close: async () => {
        await import("node:fs").then((fs) => fs.writeFileSync(stopFile, ""))
        await run.exited
        iso.cleanup()
      },
    }
  },
}

const hosts = blenderBin ? [stub, blender] : [stub]

for (const factory of hosts) {
  describe(`conformance against the ${factory.name}`, () => {
    it("checkHostListener: loopback only, token required, valid hello", async () => {
      const host = await factory.start(false)
      try {
        const report = await checkHostListener(host.endpoint)
        assert.deepEqual(report.errors, [])
      } finally {
        await host.close()
      }
    })

    it("checkCodeExecutionGate: absent by default, both settings needed, asked on every call", async () => {
      let marker = 0
      const errors = await checkCodeExecutionGate({
        app: "blender",
        manifests: manifests(),
        defaults: {
          appPreference: false,
          pluginOption: readOptions(undefined, {}).allowCodeExecution,
        },
        async probe(settings: CodeExecutionSettings) {
          const host = await factory.start(settings.appPreference)
          let answer: "allow" | "deny" = "allow"
          const plugin = await loadPlugin({
            options: { allowCodeExecution: settings.pluginOption },
            answer: () => answer,
          })
          const advertised = await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle))
          assert.deepEqual(advertised, { outcome: "accepted" })
          const offerID = plugin.registry.hosts().offers[0]?.offerID as string
          assert.deepEqual(plugin.registry.attach(offerID, "ses_test"), { outcome: "attached" })
          const hello = (await requestHost(
            host.endpoint,
            "hello",
            helloParams(readOptions({ allowCodeExecution: settings.pluginOption }, {})),
          )) as {
            capabilities: { methods: string[] }
          }
          const toolNames = plugin.toolNames()
          cleanups.push(async () => {
            await plugin.cleanup()
            await host.close()
          })
          return {
            helloMethods: hello.capabilities.methods,
            toolNames,
            async call(permission) {
              answer = permission
              marker += 1
              const file = join(scratch, `executed-${marker}.txt`)
              const code = `open(${JSON.stringify(file)}, "w").write("ran")`
              const asked = plugin.permissions.length
              let error: string | undefined
              try {
                if (toolNames.includes("blender_execute_code")) {
                  await plugin.tool("blender_execute_code", { script_handle: host.scriptHandle, code })
                } else {
                  await plugin.internals.executeCode({ script_handle: host.scriptHandle, code }, plugin.context())
                }
              } catch (thrown) {
                error =
                  thrown instanceof HostError
                    ? thrown.code
                    : /Code execution is disabled/.test(String(thrown))
                      ? "method_disabled"
                      : String(thrown)
              }
              return {
                executed: existsSync(file),
                permissionsAsked: plugin.permissions.slice(asked).map((request) => request.action),
                ...(error ? { error } : {}),
              }
            },
          }
        },
      })
      for (const cleanup of cleanups.splice(0)) await cleanup()
      assert.deepEqual(errors, [])
    })
  })
}

const cleanups: Array<() => Promise<void>> = []

describe("conformance of the plugin's RPC handlers", () => {
  it("runSentinelScenario: no token, port, script handle or offer key in outputs or events", async () => {
    const plugin = await loadPlugin()
    try {
      const report = await runSentinelScenario({
        call: (method, input) => plugin.rpc(method, input),
        events: plugin.events,
      })
      assert.deepEqual(report.errors, [])
    } finally {
      await plugin.cleanup()
    }
  })
})
