import assert from "node:assert/strict"
import { execFileSync, spawnSync } from "node:child_process"
import { mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"
import type { Plugin } from "@opencode/plugin"
import { setupResolve } from "../plugin/src/plugin.ts"
import { checkPreconditions, readOptions } from "../plugin/src/preconditions.ts"

test("preconditions reject missing, old, malformed and untrusted command shapes", () => {
  assert.equal(checkPreconditions(readOptions({}, {})).ready, false)
  const valid = { studio: true, appVersion: "21.1", command: [process.execPath] }
  assert.deepEqual(checkPreconditions(valid), { ready: true, issues: [] })
  assert.equal(checkPreconditions({ ...valid, studio: false }).ready, false)
  for (const appVersion of ["20.3", "21.0", "21.1-beta", "x"])
    assert.equal(checkPreconditions({ ...valid, appVersion }).ready, false)
  assert.equal(checkPreconditions({ ...valid, appVersion: "22.0" }).ready, true)
  assert.equal(checkPreconditions({ ...valid, command: ["relative-command"] }).ready, false)
  assert.equal(checkPreconditions(valid, () => false).ready, false)
  for (const command of [[], "bad", [1], ["bad\ncommand"]]) assert.throws(() => readOptions({ command }, {}))
  assert.throws(() => readOptions({ studio: "true" }, {}))
  assert.throws(() => readOptions({ appVersion: 21.1 }, {}))
  const env = {
    NEURALNOMADS_RESOLVE_COMMAND: JSON.stringify([process.execPath]),
    NEURALNOMADS_RESOLVE_STUDIO: "true",
    NEURALNOMADS_RESOLVE_APP_VERSION: "21.1",
  }
  assert.deepEqual(readOptions({}, env), valid)
  assert.equal(
    readOptions({ studio: false, appVersion: "20.0", command: [process.execPath, "arg"] }, env).studio,
    false,
  )
})

function harness(options: Record<string, unknown> = {}, existing?: unknown, userSkill?: unknown) {
  const servers = new Map<string, unknown>(existing ? [["resolve", existing]] : [])
  const skills = new Map<string, unknown>(userSkill ? [["resolve-workflows", userSkill]] : [])
  let toolTransform: (editor: unknown) => void
  let status = "disabled"
  let failList = false
  let deny = false
  let executed = 0
  const asked: unknown[] = []
  const events: unknown[] = []
  const tools = new Map<string, { id: string; execute: (...args: unknown[]) => Promise<unknown> }>([
    ["resolve_future_tool", { id: "resolve_future_tool", execute: async () => ++executed }],
  ])
  let handlers: Record<string, (input: unknown) => Promise<unknown>>
  let definition: { id: string; methods: Record<string, unknown>; events: Record<string, unknown> }
  const ctx = {
    options,
    mcp: {
      transform: async (callback: (editor: unknown) => void) =>
        callback({ get: (id: string) => servers.get(id), set: (id: string, value: unknown) => servers.set(id, value) }),
      list: async () => {
        if (failList) throw Error("private-path-token")
        return {
          data: servers.has("resolve") ? [{ name: "resolve", status: { status, error: "private-path-token" } }] : [],
        }
      },
    },
    tool: {
      transform: async (callback: typeof toolTransform) => {
        toolTransform = callback
        replay()
      },
    },
    skill: {
      transform: async (callback: (editor: unknown) => void) =>
        callback({ get: (id: string) => skills.get(id), add: (value: { id: string }) => skills.set(value.id, value) }),
    },
    permission: {
      assert: async (input: unknown) => {
        asked.push(input)
        if (deny) throw Error("denied")
      },
    },
    rpc: {
      register: async (def: typeof definition, methods: typeof handlers) => {
        definition = def
        handlers = methods
        return {
          events: {
            emit: async (name: string, data: unknown) => {
              events.push({ name, data })
            },
          },
        }
      },
    },
    event: {
      subscribe: async function* () {
        yield { type: "mcp.status.changed" }
      },
    },
  }
  function replay() {
    toolTransform({
      list: () => [...tools.values()],
      update: (id: string, callback: (tool: unknown) => void) => callback(tools.get(id)),
    })
  }
  return {
    ctx: ctx as unknown as Plugin.Context,
    servers,
    skills,
    tools,
    asked,
    events,
    definition: () => definition,
    status: () => handlers.status?.({}),
    replay,
    setStatus: (value: string) => {
      status = value
    },
    failList: () => {
      failList = true
    },
    deny: () => {
      deny = true
    },
    executed: () => executed,
  }
}

test("declaration is disabled, checks prerequisites, preserves user entries and skills", async () => {
  const empty = harness()
  await setupResolve(empty.ctx)
  assert.equal(empty.servers.size, 0)
  const h = harness({ studio: true, appVersion: "21.1", command: [process.execPath, "vendor-arg"] })
  const cleanup = await setupResolve(h.ctx)
  assert.deepEqual(h.servers.get("resolve"), {
    type: "local",
    command: [process.execPath, "vendor-arg"],
    disabled: true,
  })
  assert.equal(h.skills.size, 1)
  const existing = { type: "remote", url: "https://vendor.invalid/mcp", disabled: false }
  const skill = { content: "user skill" }
  const owned = harness({}, existing, skill)
  await setupResolve(owned.ctx)
  assert.equal(owned.servers.get("resolve"), existing)
  assert.equal(owned.skills.get("resolve-workflows"), skill)
  cleanup()
})

test("strict vendor RPC maps native MCP state and emits empty invalidations", async () => {
  const h = harness({}, { type: "local" })
  await setupResolve(h.ctx)
  assert.equal(h.definition().id, "neuralnomads.resolve")
  assert.deepEqual(Object.keys(h.definition().methods), ["status"])
  assert.deepEqual(Object.keys(h.definition().events), ["status_changed"])
  for (const [native, expected] of [
    ["connected", "connected"],
    ["pending", "none"],
    ["disabled", "none"],
    ["failed", "error"],
    ["needs_auth", "error"],
  ]) {
    h.setStatus(native as string)
    assert.deepEqual(await h.status(), {
      contract: 1,
      integration: { id: "resolve", version: "0.2.0", family: "vendor-mcp", spec: 0 },
      connection: { state: expected },
    })
  }
  h.failList()
  assert.equal(JSON.stringify(await h.status()).includes("private-path-token"), false)
  assert.deepEqual(h.events, [{ name: "status_changed", data: {} }])
})

test("permission denial never executes and refreshed tool catalogs stay guarded", async () => {
  const h = harness()
  await setupResolve(h.ctx)
  const context = { sessionID: "session", agent: "build", messageID: "message", id: "call" }
  await h.tools.get("resolve_future_tool")?.execute({}, context)
  assert.equal(h.executed(), 1)
  assert.equal((h.asked[0] as { action: string }).action, "resolve_execute_code")
  let refreshed = 0
  h.tools.set("resolve_new_execution", { id: "resolve_new_execution", execute: async () => ++refreshed })
  h.replay()
  h.deny()
  const guarded = h.tools.get("resolve_new_execution")
  assert.ok(guarded)
  await assert.rejects(() => guarded.execute({}, context), /denied/)
  assert.equal(refreshed, 0)
})

test("packaged precondition script is read-only and returns bounded diagnostics", () => {
  const root = fileURLToPath(new URL("../plugin/", import.meta.url))
  execFileSync(process.execPath, ["scripts/build.ts"], { cwd: root })
  const script = join(root, "scripts/check-preconditions.mjs")
  const dir = mkdtempSync(join(tmpdir(), "resolve-test-"))
  try {
    const file = join(dir, "vendor-server")
    writeFileSync(file, "not executed")
    const options = { studio: true, appVersion: "21.1", command: [file] }
    const result = spawnSync(process.execPath, [script, JSON.stringify(options)], { encoding: "utf8" })
    assert.equal(result.status, 0, result.stderr)
    assert.deepEqual(JSON.parse(result.stdout), { ready: true, issues: [] })
    const missing = spawnSync(process.execPath, [script, "{}"], {
      encoding: "utf8",
      env: {
        ...process.env,
        NEURALNOMADS_RESOLVE_COMMAND: "",
        NEURALNOMADS_RESOLVE_STUDIO: "",
        NEURALNOMADS_RESOLVE_APP_VERSION: "",
      },
    })
    assert.equal(missing.status, 1)
    assert.equal(JSON.parse(missing.stdout).ready, false)
    const invalid = spawnSync(process.execPath, [script, '{"private-path-token'], { encoding: "utf8" })
    assert.equal(invalid.status, 1)
    assert.deepEqual(JSON.parse(invalid.stdout), { ready: false, issues: ["invalid_options"] })
    assert.equal(invalid.stderr, "")
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})
