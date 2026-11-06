import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { join } from "node:path"
import { createInterface } from "node:readline"
import { it } from "node:test"
import {
  checkCodeExecutionGate,
  checkHostListener,
  runSentinelScenario,
} from "../../../packages/conformance/src/index.ts"
import { requestHost } from "../plugin/src/kit.ts"
import { loadPlugin } from "./helpers.ts"

it("real Gaffer HostServer: loopback-only, 256-bit token, constant-time authentication and valid hello", async () => {
  const child = spawn(
    process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3"),
    ["-I", join(import.meta.dirname, "python", "stub_host.py")],
    { stdio: ["pipe", "pipe", "pipe"], windowsHide: true },
  )
  const line = await new Promise<string>((resolve, reject) => {
    createInterface({ input: child.stdout }).once("line", resolve)
    child.once("exit", (code) => reject(new Error(`Host exited ${code}`)))
    child.once("error", reject)
  })
  const endpoint = JSON.parse(line)
  try {
    assert.match(endpoint.token, /^[0-9a-f]{64}$/)
    assert.deepEqual((await checkHostListener(endpoint, 2_000, { script_handle: "test-script" })).errors, [])
    const plugin = await loadPlugin()
    try {
      assert.deepEqual(
        await plugin.rpc("advertise", {
          host: "127.0.0.1",
          port: endpoint.port,
          token: endpoint.token,
          script_handle: "test-script",
          label: "Gaffer stub",
          protocol: 1,
          offer_key: "test-offer",
        }),
        { outcome: "accepted" },
      )
      const hello = (await requestHost(endpoint, "hello", { script_handle: "test-script" })) as {
        capabilities: { methods: string[] }
      }
      const errors = await checkCodeExecutionGate({
        app: "gaffer",
        manifests: [],
        defaults: { appPreference: false, pluginOption: false },
        probe: async () => ({
          helloMethods: hello.capabilities.methods,
          toolNames: [...plugin.tools.keys()],
          call: async () => ({ executed: false, permissionsAsked: [], error: "method_disabled" }),
        }),
      })
      assert.deepEqual(errors, [])
    } finally {
      await plugin.cleanup()
    }
  } finally {
    child.stdin.end()
    await new Promise((resolve) => child.once("exit", resolve))
  }
})

it("Gaffer RPC sentinel scenario: every output and event is credential-free", async () => {
  const plugin = await loadPlugin()
  try {
    const report = await runSentinelScenario(
      { call: plugin.rpc, events: plugin.events },
      { sessionID: "ses_test", hostVersion: "0.2.0" },
    )
    assert.deepEqual(report.errors, [])
  } finally {
    await plugin.cleanup()
  }
})
