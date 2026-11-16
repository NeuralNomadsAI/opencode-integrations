import assert from "node:assert/strict"
import { spawn, spawnSync } from "node:child_process"
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createInterface } from "node:readline"
import { after, it } from "node:test"
import { type HostError, requestHost } from "../../../packages/integration-kit/src/index.ts"
import {
  checkCodeExecutionGate,
  checkHostListener,
  type ManifestLike,
  runSentinelScenario,
  startFakeHost,
} from "../../../packages/testing/src/index.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { methodsDir } from "../plugin/src/paths.ts"
import { announcement, loadPlugin } from "./helpers.ts"

const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
const scratch = mkdtempSync(join(tmpdir(), "gaffer-conformance-"))
after(() => rmSync(scratch, { recursive: true, force: true }))

function manifests(): ManifestLike[] {
  return readdirSync(methodsDir, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
    .map((entry) => JSON.parse(readFileSync(join(entry.parentPath, entry.name), "utf8")) as ManifestLike)
}

// The installed part's real HostServer under system Python, with only the Gaffer application stubbed.
async function servedHost() {
  const child = spawn(python, ["-I", join(import.meta.dirname, "python", "stub_host.py")], {
    stdio: ["pipe", "pipe", "inherit"],
    windowsHide: true,
  })
  const line = await new Promise<string>((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill()
      reject(new Error("host startup timed out"))
    }, 10_000)
    createInterface({ input: child.stdout }).once("line", (line) => {
      clearTimeout(timer)
      resolve(line)
    })
    child.once("exit", () => {
      clearTimeout(timer)
      reject(new Error("host exited"))
    })
  })
  const endpoint = JSON.parse(line) as { host: string; port: number; token: string }
  return {
    endpoint,
    close: async () => {
      const stopped = new Promise((resolve) => child.once("exit", resolve))
      child.stdin.end()
      await stopped
    },
  }
}

it("the actual Gaffer host transport: authenticated, loopback-only, hello per script, refusals with their data", async () => {
  const host = await servedHost()
  try {
    assert.match(host.endpoint.token, /^[0-9a-f]{64}$/)
    assert.deepEqual((await checkHostListener(host.endpoint, 2_000, { script_handle: "test-script" })).errors, [])
    // hello answers the script it is asked for, and only an open one.
    const hello = (await requestHost(host.endpoint, "hello", { script_handle: "test-script" })) as {
      script_handle: string
    }
    assert.equal(hello.script_handle, "test-script")
    await assert.rejects(requestHost(host.endpoint, "hello", { script_handle: "closed-script" }), /stale_handle/)
    // A refusal keeps the host's message, its data (made JSON-safe) and its own code (data.reason).
    await assert.rejects(requestHost(host.endpoint, "refuse", { script_handle: "test-script" }), (error: HostError) => {
      assert.equal(error.code, "invalid_args")
      assert.match(error.message, /\(invalid_args\): SceneView renderer is not registered: Arnold /)
      assert.deepEqual(error.data?.registered_renderers, ["OpenGL"])
      assert.equal(error.data?.reason, "unsupported_renderer")
      assert.equal(typeof error.data?.plug, "string")
      return true
    })
  } finally {
    await host.close()
  }
})

it("checkCodeExecutionGate: declared dangerous, listed by the host, asked on every call", async () => {
  let marker = 0
  const cleanups: Array<() => Promise<void>> = []
  const errors = await checkCodeExecutionGate({
    app: "gaffer",
    manifests: manifests(),
    async probe() {
      // A Gaffer whose execute_code really runs the Python it is given.
      const host = await startFakeHost({
        hostVersion: readPackage().version,
        appVersion: "1.6.15.0",
        capabilities: {
          methods: ["hello", "context", "apply", "execute_code"],
          operations: [],
          undo: "transactional",
          background: false,
        },
        methods: {
          execute_code: (params) => {
            const run = spawnSync(python, ["-I", "-c", String(params.code)], { windowsHide: true, encoding: "utf8" })
            return { status: run.status === 0 ? "executed" : "failed", output: run.stdout }
          },
        },
      })
      let answer: "allow" | "deny" = "allow"
      const plugin = await loadPlugin({
        answer: (action) => {
          if (action === "gaffer_execute_code" && answer === "deny") throw new Error("Permission denied")
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
          const file = join(scratch, `executed-${marker}.txt`)
          const code = `open(${JSON.stringify(file)}, "w").write("ran")`
          const asked = plugin.permissions.length
          let error: string | undefined
          try {
            await plugin.tool("gaffer_execute_code", { code })
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
      { sessionID: "ses_test", hostVersion: readPackage().version, appVersion: "1.6.15.0" },
    )
    assert.deepEqual(result.errors, [])
  } finally {
    await plugin.cleanup()
  }
})
