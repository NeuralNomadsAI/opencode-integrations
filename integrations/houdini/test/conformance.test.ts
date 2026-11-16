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
} from "../../../packages/testing/src/index.ts"
import { readPackage } from "../plugin/src/integration.ts"
import { methodsDir } from "../plugin/src/paths.ts"
import { announcement, loadPlugin } from "./helpers.ts"

const python = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3")
const probe = join(import.meta.dirname, "host_probe.py")
const scratch = mkdtempSync(join(tmpdir(), "houdini-conformance-"))
after(() => rmSync(scratch, { recursive: true, force: true }))

function manifests(): ManifestLike[] {
  return readdirSync(methodsDir, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
    .map((entry) => JSON.parse(readFileSync(join(entry.parentPath, entry.name), "utf8")) as ManifestLike)
}

// The installed part's real listener and dispatcher under system Python, with only the HOM boundary stubbed.
async function servedHost() {
  const child = spawn(python, ["-I", probe, "--serve"], {
    stdio: ["pipe", "pipe", "inherit"],
    windowsHide: true,
  })
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
  return {
    endpoint,
    close: async () => {
      const stopped = new Promise((resolve) => child.once("exit", resolve))
      child.stdin.end("stop\n")
      await stopped
    },
  }
}

it("the actual Houdini host transport: authenticated, loopback-only, refusals with their text, data and code", async () => {
  const host = await servedHost()
  try {
    assert.deepEqual((await checkHostListener(host.endpoint)).errors, [])
    // A refusal keeps the host's message, its data and its own code (data.reason).
    await assert.rejects(
      requestHost(host.endpoint, "apply", {
        script_handle: "hip:test-document",
        session_id: "ses_test",
        if_revision: 0,
        command_id: "stale",
      }),
      (error: HostError) => {
        assert.equal(error.code, "stale_revision")
        assert.match(error.message, /\(stale_revision\): The Houdini graph changed after the intent was prepared /)
        assert.deepEqual(error.data, { expected_revision: 0, current_revision: 1, reason: "revision_conflict" })
        return true
      },
    )
    // status answers without the main thread.
    assert.deepEqual(await requestHost(host.endpoint, "status", {}), { busy: false, queued: 0 })
  } finally {
    await host.close()
  }
})

it("checkCodeExecutionGate: declared dangerous, listed by the host, asked on every call", async () => {
  let marker = 0
  const cleanups: Array<() => Promise<void>> = []
  const errors = await checkCodeExecutionGate({
    app: "houdini",
    manifests: manifests(),
    async probe() {
      const host = await servedHost()
      let answer: "allow" | "deny" = "allow"
      const plugin = await loadPlugin({
        answer: (action) => {
          if (action === "houdini_execute_code" && answer === "deny") throw new Error("Permission denied")
        },
      })
      cleanups.push(async () => {
        await plugin.cleanup()
        await host.close()
      })
      const hello = (await requestHost(host.endpoint, "hello", {})) as {
        script_handle: string
        capabilities: { methods: string[] }
      }
      assert.deepEqual(await plugin.rpc("advertise", announcement(host.endpoint, hello.script_handle)), {
        outcome: "accepted",
      })
      return {
        helloMethods: hello.capabilities.methods,
        toolNames: [...plugin.tools.keys()],
        async call(permission) {
          answer = permission
          marker += 1
          const file = join(scratch, `executed-${marker}.txt`)
          const code = `open(${JSON.stringify(file)}, "w").write("ran")`
          const asked = plugin.permissions.length
          let error: string | undefined
          try {
            await plugin.tool("houdini_execute_code", { code })
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
      { sessionID: "ses_test", hostVersion: readPackage().version, appVersion: "22.0.429" },
    )
    assert.deepEqual(result.errors, [])
  } finally {
    await plugin.cleanup()
  }
})

it("host code runs, undo, quarantine, status and string-code boundaries under system Python", () => {
  const result = spawnSync(python, ["-I", probe], { windowsHide: true, encoding: "utf8" })
  assert.equal(result.status, 0, result.stderr)
})

it("hints read Houdini's own knowledge (stub hou)", () => {
  const result = spawnSync(python, ["-I", join(import.meta.dirname, "hints_probe.py")], {
    windowsHide: true,
    encoding: "utf8",
  })
  assert.equal(result.status, 0, result.stderr)
})
