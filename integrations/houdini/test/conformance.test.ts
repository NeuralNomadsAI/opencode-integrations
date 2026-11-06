import assert from "node:assert/strict"
import { spawn, spawnSync } from "node:child_process"
import { join } from "node:path"
import { createInterface } from "node:readline"
import { it } from "node:test"
import {
  checkCodeExecutionGate,
  checkHostListener,
  runSentinelScenario,
} from "../../../packages/conformance/src/index.ts"
import { requestHost } from "../../../packages/integration-kit/src/index.ts"
import { loadPlugin } from "./helpers.ts"

const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
const probe = join(import.meta.dirname, "host_probe.py")

it("the actual Houdini host transport is authenticated, loopback-only and has no code execution", async () => {
  const child = spawn(python, ["-I", probe, "--serve"], { stdio: ["pipe", "pipe", "inherit"], windowsHide: true })
  const line = await new Promise<string>((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill()
      reject(new Error("host startup timed out"))
    }, 10000)
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
  const plugin = await loadPlugin()
  try {
    assert.deepEqual((await checkHostListener(endpoint)).errors, [])
    const hello = (await requestHost(endpoint, "hello")) as { capabilities: { methods: string[] } }
    assert.deepEqual(
      await checkCodeExecutionGate({
        app: "houdini",
        manifests: [],
        defaults: { appPreference: false, pluginOption: false },
        probe: async () => ({
          helloMethods: hello.capabilities.methods,
          toolNames: [...plugin.tools.keys()].map((name) => `houdini_${name}`),
          call: async () => ({ executed: false, permissionsAsked: [], error: "method_disabled" }),
        }),
      }),
      [],
    )
    await assert.rejects(
      requestHost(endpoint, "execute_code", { script_handle: "hip:test-document", code: "raise Exception()" }),
    )
  } finally {
    await plugin.cleanup()
    const stopped = new Promise((resolve) => child.once("exit", resolve))
    child.stdin.end("stop\n")
    await stopped
  }
})

it("RPC sentinels stay out of every output and event, including bindingRead", async () => {
  const plugin = await loadPlugin()
  try {
    const result = await runSentinelScenario(
      { call: (method, input) => plugin.rpc(method, input as object), events: plugin.events },
      { sessionID: "ses_test", hostVersion: "0.2.0", appVersion: "22.0.429" },
    )
    assert.deepEqual(result.errors, [])
  } finally {
    await plugin.cleanup()
  }
})

it("host preconditions, revision, headless and string-code boundaries", () => {
  const result = spawnSync(python, ["-I", probe], { windowsHide: true, encoding: "utf8" })
  assert.equal(result.status, 0, result.stderr)
})
