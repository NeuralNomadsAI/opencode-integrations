import assert from "node:assert/strict"
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, it } from "node:test"
import {
  checkCodeExecutionGate,
  type ManifestLike,
  runSentinelScenario,
  startFakeHost,
} from "../../../packages/testing/src/index.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { methodsDir } from "../plugin/src/paths.ts"
import { announcement, loadPlugin } from "./helpers.ts"

const scratch = mkdtempSync(join(tmpdir(), "godot-conformance-"))
after(() => rmSync(scratch, { recursive: true, force: true }))

function manifests(): ManifestLike[] {
  return readdirSync(methodsDir, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
    .map((entry) => JSON.parse(readFileSync(join(entry.parentPath, entry.name), "utf8")) as ManifestLike)
}

it("checkCodeExecutionGate: declared dangerous, listed by the host, asked on every call", async () => {
  let marker = 0
  const cleanups: Array<() => Promise<void>> = []
  const errors = await checkCodeExecutionGate({
    app: "godot",
    manifests: manifests(),
    async probe() {
      // An editor whose execute_code does what the GDScript it is given says: write a file.
      const host = await startFakeHost({
        hostVersion: readPackage().version,
        appVersion: "4.7.2-stable (official)",
        capabilities: {
          methods: ["hello", "context", "execute_code"],
          operations: [],
          undo: "grouped_best_effort",
          background: false,
        },
        methods: {
          execute_code: (params) => {
            const file = /FileAccess\.open\("([^"]+)"/.exec(String(params.code))?.[1]
            if (file) writeFileSync(file, "ran")
            return { status: "executed" }
          },
        },
      })
      let answer: "allow" | "deny" = "allow"
      const plugin = await loadPlugin({
        answer: (action) => {
          if (action === "godot_execute_code" && answer === "deny") throw new Error("Permission denied")
        },
      })
      cleanups.push(async () => {
        await plugin.cleanup()
        await host.close()
      })
      assert.deepEqual(await plugin.rpc("advertise", announcement(host.endpoint, host.scriptHandle)), {
        outcome: "accepted",
      })
      return {
        helloMethods: (host.hello.capabilities as { methods: string[] }).methods,
        toolNames: [...plugin.tools.keys()],
        async call(permission) {
          answer = permission
          marker += 1
          const file = join(scratch, `executed-${marker}.txt`).replaceAll("\\", "/")
          const code = `FileAccess.open("${file}", FileAccess.WRITE).store_string("ran")`
          const asked = plugin.permissions.length
          let error: string | undefined
          try {
            await plugin.tool("godot_execute_code", { code })
          } catch (thrown) {
            error = String(thrown)
          }
          return {
            executed: existsSync(file),
            permissionsAsked: plugin.permissions.slice(asked),
            ...(error ? { error } : {}),
          }
        },
      }
    },
  })
  for (const cleanup of cleanups.splice(0)) await cleanup()
  assert.deepEqual(errors, [])
})

it("RPC sentinels stay out of every output and event, including bindingRead", async () => {
  const plugin = await loadPlugin()
  try {
    const result = await runSentinelScenario(
      { call: (method, input) => plugin.rpc(method, input as object), events: plugin.events },
      { sessionID: "ses_test", hostVersion: readPackage().version, appVersion: "4.7.2-stable (official)" },
    )
    assert.deepEqual(result.errors, [])
  } finally {
    await plugin.cleanup()
  }
})
