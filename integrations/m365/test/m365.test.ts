import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import { after, before, beforeEach, test } from "node:test"
import type { Plugin } from "@opencode/plugin"
import type { Info } from "@opencode/plugin/promise/tool"
import JSZip from "jszip"
import { checkRepository, checkStatusOutput } from "../../../packages/conformance/src/index.ts"
import { readOptions, withAuthOptions } from "../plugin/src/options.ts"
import { setupM365 } from "../plugin/src/plugin.ts"
import {
  ensureDeviceCodeBootstrap,
  getAuthStatus,
  getValidAuth,
  pollForDeviceToken,
} from "../plugin/src/runtime/auth.ts"
import { dispatchDeclaredMethod, listHandledMethodNames, listPublicMethods } from "../plugin/src/runtime/dispatcher.ts"
import {
  buildGraphUrl,
  downloadGraphBytes,
  executeGraphRawRequest,
  executeGraphRequest,
} from "../plugin/src/runtime/graph.ts"
import { readState, stateFile, writeState } from "../plugin/src/state.ts"
import { checkZipBudget } from "../plugin/src/zip-budget.ts"

const root = mkdtempSync(join(tmpdir(), "m365-test-"))
const originalEnv = { ...process.env }
const originalFetch = globalThis.fetch
const options = readOptions(
  {
    clientId: "11111111-1111-4111-8111-111111111111",
    tenantId: "organizations",
    scopes: ["User.Read", "offline_access"],
  },
  {},
)
const token = {
  clientId: options.clientId,
  tenantId: options.tenantId,
  scope: "User.Read Files.ReadWrite.All Sites.ReadWrite.All",
  tokenType: "Bearer",
  accessToken: "FAKE_ACCESS_SENTINEL",
  refreshToken: "FAKE_REFRESH_SENTINEL",
  expiresAt: Date.now() + 3600000,
}
const inAuth = <T>(run: () => T) => withAuthOptions(options, run)
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } })

before(() => {
  process.env.HOME = join(root, "home")
  process.env.USERPROFILE = process.env.HOME
  process.env.LOCALAPPDATA = join(root, "local")
  process.env.XDG_STATE_HOME = join(root, "state")
  process.env.XDG_CONFIG_HOME = join(root, "config")
})
beforeEach(() => {
  rmSync(join(root, "local"), { recursive: true, force: true })
  rmSync(join(root, "state"), { recursive: true, force: true })
  globalThis.fetch = async () => {
    throw new Error("Unexpected network request in offline test")
  }
})
after(() => {
  globalThis.fetch = originalFetch
  process.env = originalEnv
  rmSync(root, { recursive: true, force: true })
})

test("configuration is user-owned, validated and options win over neutral env", () => {
  assert.equal(readOptions({}, {}).clientId, "")
  assert.equal(readOptions({}, {}).tenantId, "organizations")
  assert.equal(
    readOptions({ clientId: options.clientId }, { NEURALNOMADS_M365_CLIENT_ID: "invalid" }).clientId,
    options.clientId,
  )
  for (const bad of [{ clientId: "invalid" }, { tenantId: "evil/path" }, { scopes: ["bad scope"] }])
    assert.throws(() => readOptions(bad, {}))
  const status = withAuthOptions(readOptions({}, {}), getAuthStatus)
  assert.equal(status.configured, false)
  assert.equal(status.configuration, "client_id_required")
  assert.equal(existsSync(join(root, "local")), false)
})

test("private state, atomic writes, separate app/tenant and secret-free status", () =>
  inAuth(() => {
    writeState("auth", token)
    assert.deepEqual(readState("auth"), token)
    const file = stateFile("auth")
    assert.ok(!file.includes("opencode/config"))
    if (process.platform !== "win32") assert.equal(statSync(file).mode & 0o777, 0o600)
    else {
      const acl = execFileSync("icacls.exe", [file], { encoding: "utf8", windowsHide: true })
      assert.ok(!acl.includes("(I)"), acl)
    }
    const status = getAuthStatus()
    assert.equal(status.signed_in, true)
    assert.ok(status.scope_list.includes("User.Read"))
    assert.ok(!JSON.stringify(status).includes("SENTINEL"))
    const other = readOptions({ clientId: options.clientId, tenantId: "example.org" }, {})
    assert.equal(
      withAuthOptions(other, () => readState("auth")),
      null,
    )
  }))

test("device login and refresh use mocked OAuth, only granted scopes and no credential overrides", async () =>
  inAuth(async () => {
    const requests: string[] = []
    globalThis.fetch = async (input, init) => {
      requests.push(String(input))
      assert.equal(init?.redirect, "error")
      if (String(input).endsWith("/devicecode"))
        return json({
          device_code: "FAKE_DEVICE_SENTINEL",
          user_code: "USER-CODE",
          verification_uri: "https://microsoft.com/devicelogin",
          interval: 5,
          expires_in: 900,
        })
      assert.ok(String(init?.body).includes("client_id="))
      return json({
        access_token: "FAKE_ACCESS_SENTINEL",
        refresh_token: "FAKE_REFRESH_SENTINEL",
        expires_in: 3600,
        scope: "User.Read",
      })
    }
    const login = await ensureDeviceCodeBootstrap()
    assert.equal(login.pending.userCode, "USER-CODE")
    assert.equal((await pollForDeviceToken({ timeoutSeconds: 1 })).signed_in, true)
    assert.equal((await getValidAuth(true)).scope, "User.Read")
    assert.equal(requests.length, 3)
    await assert.rejects(dispatchDeclaredMethod("m365_auth_device_start", { client_id: options.clientId }))
    await assert.rejects(dispatchDeclaredMethod("m365_auth_device_poll", { device_code: "override" }))
    assert.equal(requests.length, 3)
  }))

test("Graph credentials never go to arbitrary origins and raw bodies remain Buffer-backed", async () =>
  inAuth(async () => {
    writeState("auth", token)
    let count = 0
    globalThis.fetch = async (input, init) => {
      count++
      assert.equal(new URL(String(input)).origin, "https://graph.microsoft.com")
      assert.ok(init)
      assert.equal((init.headers as Record<string, string>).Authorization, `Bearer ${token.accessToken}`)
      assert.equal(init?.redirect, "error")
      if (init?.body) assert.ok(Buffer.isBuffer(init.body))
      return json({ id: "mock" })
    }
    assert.equal((await executeGraphRequest({ path: "/me" })).status, 200)
    await executeGraphRawRequest({ path: "/drives/test/items/test/content", body: new Uint8Array([1, 2, 3]) })
    await assert.rejects(executeGraphRequest({ path: "https://attacker.example/steal" }))
    for (const path of [
      "https://graph.microsoft.com.evil.example/v1.0/me",
      "https://user:secret@graph.microsoft.com/v1.0/me",
      "https://graph.microsoft.com:444/v1.0/me",
      "/../outside",
    ])
      assert.throws(() => buildGraphUrl(path))
    assert.equal(count, 2)
  }))

test("401 refresh once, then failure without credential disclosure", async () =>
  inAuth(async () => {
    writeState("auth", token)
    let graphCalls = 0,
      refreshes = 0
    globalThis.fetch = async (input) => {
      if (String(input).startsWith("https://login.microsoftonline.com/")) {
        refreshes++
        return json({ access_token: "FAKE_RENEWED", expires_in: 3600, scope: "User.Read" })
      }
      graphCalls++
      return new Response("secret echoed by untrusted server", { status: 401 })
    }
    await assert.rejects(executeGraphRequest({ path: "/me" }), (error) => !String(error).includes("secret echoed"))
    assert.equal(graphCalls, 2)
    assert.equal(refreshes, 1)
  }))

test("download redirect strips authorization, rejects foreign origins and enforces streaming bound", async () =>
  inAuth(async () => {
    writeState("auth", token)
    globalThis.fetch = async (input, init) => {
      if (String(input).startsWith("https://graph.microsoft.com/"))
        return new Response(null, { status: 302, headers: { location: "https://example.sharepoint.com/file" } })
      assert.equal(init?.headers, undefined)
      assert.equal(init?.redirect, "error")
      return new Response(new Uint8Array([1, 2, 3]))
    }
    assert.deepEqual([...(await downloadGraphBytes({ path: "/drives/d/items/i/content" })).bytes], [1, 2, 3])
    await assert.rejects(downloadGraphBytes({ path: "/drives/d/items/i/content", max_bytes: 2 }), /size limit/)
    globalThis.fetch = async () =>
      new Response(null, { status: 302, headers: { location: "https://attacker.example/file" } })
    await assert.rejects(downloadGraphBytes({ path: "/drives/d/items/i/content" }), /redirect refused/)
  }))

test("all manifests have handlers, invalid inputs do not start login, missing scopes stay explicit", async () =>
  inAuth(async () => {
    assert.deepEqual(
      listPublicMethods()
        .map((m) => m.name)
        .sort(),
      listHandledMethodNames(),
    )
    await assert.rejects(dispatchDeclaredMethod("m365_graph_request", {}))
    writeState("auth", { ...token, scope: "User.Read" })
    await assert.rejects(dispatchDeclaredMethod("m365_list_sites", {}), /Missing Microsoft Graph scopes/)
  }))

test("logout during an in-flight refresh cannot recreate credentials", async () =>
  inAuth(async () => {
    writeState("auth", token)
    let release: (response: Response) => void = () => {}
    globalThis.fetch = () =>
      new Promise<Response>((done) => {
        release = done
      })
    const refresh = getValidAuth(true)
    await dispatchDeclaredMethod("m365_auth_logout")
    release(json({ access_token: "FAKE_RENEWED", expires_in: 3600, scope: "User.Read" }))
    await assert.rejects(refresh, /login changed/)
    assert.equal(readState("auth"), null)
  }))

test("Office archive budget rejects excessive expanded sizes and malformed input", async () => {
  const zip = new JSZip()
  zip.file("ppt/slides/slide1.xml", "x".repeat(1024))
  const bytes = await zip.generateAsync({ type: "uint8array", compression: "DEFLATE" })
  checkZipBudget(bytes)
  assert.throws(() => checkZipBudget(bytes, 100), /size limit/)
  assert.throws(() => checkZipBudget(new Uint8Array([1, 2])), /Unsupported/)
})

test("PowerPoint preview uses mocked Graph and leaves the original package untouched", async () =>
  inAuth(async () => {
    writeState("auth", token)
    const zip = new JSZip()
    zip.file("ppt/slides/slide1.xml", "<p:sld><a:t>Hello</a:t></p:sld>")
    const bytes = await zip.generateAsync({ type: "uint8array" })
    globalThis.fetch = async (input, init) => {
      assert.equal(init?.method, "GET")
      if (String(input).endsWith("/content")) return new Response(Buffer.from(bytes))
      if (String(input).includes("/items/"))
        return json({
          id: "i",
          name: "slides.pptx",
          file: { mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation" },
        })
      return json({ id: "d", name: "Documents" })
    }
    const result = (await dispatchDeclaredMethod("m365_powerpoint_replace_text", {
      drive_id: "d",
      item_id: "i",
      replacements: { Hello: "World" },
    })) as { preview_only: boolean; modified_part_count: number }
    assert.equal(result.preview_only, true)
    assert.equal(result.modified_part_count, 1)
  }))

test("RPC contract, detailed auth status, skills preservation and denied permission make no Graph call", async () => {
  let rpcHandlers: Record<string, () => unknown> = {}
  const tools: Info[] = [],
    skills: { id: string }[] = [],
    events: unknown[] = []
  const registration = { dispose: async () => {} }
  const ctx = {
    options: { clientId: options.clientId },
    rpc: {
      register: async (_definition: unknown, handlers: typeof rpcHandlers) => {
        rpcHandlers = handlers
        return {
          ...registration,
          events: {
            emit: async (...args: unknown[]) => {
              events.push(args)
            },
          },
        }
      },
    },
    permission: {
      assert: async () => {
        throw new Error("Permission denied")
      },
    },
    tool: {
      transform: async (edit: (editor: unknown) => void) => {
        edit({ namespace: () => {}, add: (tool: Info) => tools.push(tool) })
        return registration
      },
    },
    skill: {
      transform: async (edit: (editor: unknown) => void) => {
        edit({
          get: (id: string) => (id === "m365-auth" ? {} : undefined),
          add: (skill: { id: string }) => skills.push(skill),
        })
        return registration
      },
    },
  } as unknown as Plugin.Context
  const cleanup = await setupM365(ctx)
  const status = rpcHandlers.status!()
  assert.deepEqual(checkStatusOutput(status, { id: "m365", family: "api", spec: 0 }), [])
  assert.equal((rpcHandlers.authStatus!() as { configured: boolean }).configured, true)
  assert.equal(skills.length, 8)
  assert.ok(skills.every((s) => s.id.startsWith("m365-")))
  assert.equal(tools.length, 42)
  const ping = tools.find((t) => t.name === "ping")!
  await assert.rejects(ping.execute({}, { signal: new AbortController().signal } as never), /Permission denied/)
  assert.equal(events.length, 0)
  await cleanup()
})

test("repository marker and catalogue conformance includes the API family", () => {
  const report = checkRepository(resolve("."))
  assert.deepEqual(report.errors, [])
  assert.ok(readFileSync("catalog.json", "utf8").includes('"id": "m365"'))
})
