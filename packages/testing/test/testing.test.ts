import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { networkInterfaces, tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { after, describe, it } from "node:test"

import {
  CATALOG_MAX_BYTES,
  type CodeExecutionDriver,
  type CodeExecutionSettings,
  checkCatalogBytes,
  checkCodeExecutionGate,
  checkDangerousManifests,
  checkHello,
  checkHostListener,
  checkPluginLoad,
  checkRepository,
  checkStatusOutput,
  type FakeHost,
  type MarkerIdentity,
  runSentinelScenario,
  startFakeHost,
} from "../src/index.ts"

const roots: string[] = []
const hosts: FakeHost[] = []
after(async () => {
  for (const host of hosts) await host.close()
  for (const root of roots) rmSync(root, { recursive: true, force: true })
})

function tempDir(prefix: string) {
  const root = mkdtempSync(join(tmpdir(), prefix))
  roots.push(root)
  return root
}

async function fakeHost(options: Parameters<typeof startFakeHost>[0] = {}) {
  const host = await startFakeHost(options)
  hosts.push(host)
  return host
}

const repositoryRoot = join(import.meta.dirname, "..", "..", "..")
const payloadBytes = Buffer.from("host payload")
const payloadDigest = createHash("sha256").update(payloadBytes).digest("hex")

function marker(id: string, overrides: Record<string, unknown> = {}) {
  return {
    spec: 0,
    id,
    displayName: `OpenCode for ${id}`,
    family: "native",
    plugin: `neuralnomads.${id}`,
    rpc: `neuralnomads.${id}`,
    host: {
      app: id,
      protocol: 1,
      range: "^0.1.0",
      appVersions: ">=5.2",
      payload: `host/${id}/part-0.1.0.zip`,
      sha256: payloadDigest,
    },
    ...overrides,
  }
}

function packageJson(id: string, overrides: Record<string, unknown> = {}) {
  return { name: `@neuralnomads/opencode-${id}`, version: "0.1.0", opencodeIntegration: marker(id), ...overrides }
}

function catalogEntry(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    package: `@neuralnomads/opencode-${id}`,
    range: "^0.1",
    displayName: `OpenCode for ${id}`,
    family: "native",
    plugin: `neuralnomads.${id}`,
    rpc: `neuralnomads.${id}`,
    summary: { en: "Demo." },
    app: { name: id, versions: ">=5.2" },
    host: { protocol: 1, range: "^0.1.0" },
    docs: `https://github.com/NeuralNomadsAI/opencode-integrations/tree/main/integrations/${id}`,
    status: "preview",
    ...overrides,
  }
}

function repository(fixture: { packages?: Record<string, unknown>; catalog?: unknown } = {}) {
  const root = tempDir("integration-markers-")
  const write = (file: string, content: string | Buffer) => {
    mkdirSync(dirname(join(root, file)), { recursive: true })
    writeFileSync(join(root, file), content)
  }
  write("catalog.json", JSON.stringify(fixture.catalog ?? { schemaVersion: 1, integrations: [] }))
  for (const [dir, json] of Object.entries(fixture.packages ?? {})) {
    write(`integrations/${dir}/plugin/package.json`, JSON.stringify(json))
    write(`integrations/${dir}/plugin/host/${dir}/part-0.1.0.zip`, payloadBytes)
  }
  return root
}

const errorsOf = (fixture: Parameters<typeof repository>[0]) => checkRepository(repository(fixture)).errors.join("\n")

describe("markers and catalog", () => {
  it("accept this repository", () => {
    assert.deepEqual(checkRepository(repositoryRoot).errors, [])
    assert.deepEqual(checkCatalogBytes(readFileSync(join(repositoryRoot, "catalog.json"))).errors, [])
  })

  it("accept an integration listed in the catalog", () => {
    const report = checkRepository(
      repository({
        packages: { demo: packageJson("demo") },
        catalog: { schemaVersion: 1, integrations: [catalogEntry("demo")] },
      }),
    )
    assert.deepEqual(report.errors, [])
    assert.deepEqual(report.integrations, ["@neuralnomads/opencode-demo"])
  })

  it("find a missing marker, IDs unlike the package name and a host outside native", () => {
    const { opencodeIntegration: _, ...withoutMarker } = packageJson("demo")
    assert.match(errorsOf({ packages: { demo: withoutMarker } }), /no opencodeIntegration marker/)
    const rpc = packageJson("demo", { opencodeIntegration: marker("demo", { rpc: "neuralnomads.other" }) })
    assert.match(errorsOf({ packages: { demo: rpc } }), /marker rpc is not "neuralnomads.demo"/)
    assert.match(
      errorsOf({ packages: { demo: packageJson("demo", { opencodeIntegration: marker("other") }) } }),
      /marker id is not "demo"/,
    )
    const api = packageJson("demo", { opencodeIntegration: marker("demo", { family: "api" }) })
    assert.match(errorsOf({ packages: { demo: api } }), /host is present exactly for a native integration/)
  })

  it("find a payload unlike its digest and a version outside host.range", () => {
    const wrong = marker("demo")
    wrong.host.sha256 = "0".repeat(64)
    assert.match(
      errorsOf({ packages: { demo: packageJson("demo", { opencodeIntegration: wrong }) } }),
      /host payload sha256 differs from the marker/,
    )
    assert.match(
      errorsOf({ packages: { demo: packageJson("demo", { version: "0.2.0" }) } }),
      /host.range "\^0.1.0" does not contain the embedded part 0.2.0/,
    )
  })

  it("find a catalog entry unlike its marker or without a package", () => {
    const errors = errorsOf({
      packages: { demo: packageJson("demo") },
      catalog: {
        schemaVersion: 1,
        integrations: [catalogEntry("demo", { host: { protocol: 2, range: "^0.1.0" } }), catalogEntry("ghost")],
      },
    })
    assert.match(errors, /catalog entry demo: host.protocol differs from the marker/)
    assert.match(errors, /catalog entry ghost: no package @neuralnomads\/opencode-ghost/)
  })

  it("refuse a catalog CodeNomad would refuse", () => {
    const bytes = (value: unknown) => Buffer.from(JSON.stringify(value))
    const entry = catalogEntry("demo")
    const duplicates = checkCatalogBytes(bytes({ schemaVersion: 1, integrations: [entry, entry] })).errors
    for (const field of ["id", "package", "plugin", "rpc"])
      assert.ok(duplicates.includes(`catalog.json: two entries share a ${field}`), field)
    const many = Array.from({ length: 65 }, (_, index) => catalogEntry(`app${index}`))
    assert.match(checkCatalogBytes(bytes({ schemaVersion: 1, integrations: many })).errors.join("\n"), /over 64/)
    assert.match(checkCatalogBytes(Buffer.alloc(CATALOG_MAX_BYTES + 1, 0x20)).errors.join("\n"), /over 262144 bytes/)
    assert.match(checkCatalogBytes(Buffer.from("{")).errors.join("\n"), /not JSON/)
    const errors = (overrides: Record<string, unknown>) =>
      checkCatalogBytes(bytes({ schemaVersion: 1, integrations: [catalogEntry("demo", overrides)] })).errors.join("\n")
    assert.match(errors({ extra: true }), /unknown field extra/)
    assert.match(errors({ host: undefined }), /host is present exactly for native/)
    assert.match(errors({ docs: "https://example.com/" }), /docs is not in this repository/)
    assert.match(errors({ summary: { fr: "Sans anglais" } }), /summary needs en/)
  })
})

const native: MarkerIdentity = { spec: 0, id: "demo", family: "native", host: { range: "^0.1.0" } }

function status(overrides: Record<string, unknown> = {}) {
  return {
    contract: 1,
    integration: { id: "demo", version: "0.1.0", family: "native", spec: 0 },
    host: { state: "absent", expectedRange: "^0.1.0", action: "install" },
    connection: { state: "none", offers: 0, attachedSessions: 0 },
    ...overrides,
  }
}

describe("status output", () => {
  it("accepts what CodeNomad reads, notices it does not know included", () => {
    assert.deepEqual(checkStatusOutput(status(), native, "0.1.0"), [])
    assert.deepEqual(checkStatusOutput(status({ notices: ["legacy_install", "future_notice"] }), native), [])
  })

  it("finds a status unlike the marker, the package version or the fields CodeNomad reads", () => {
    const wrongFamily = status({ integration: { id: "demo", version: "0.1.0", family: "api", spec: 0 } })
    assert.match(checkStatusOutput(wrongFamily, native).join("\n"), /differ from the marker/)
    assert.match(checkStatusOutput(status(), native, "0.2.0").join("\n"), /package version 0.2.0/)
    const { host: _, ...withoutHost } = status()
    assert.match(checkStatusOutput(withoutHost, native).join("\n"), /a native integration reports no host/)
    const path = status({ host: { state: "ok", appVersion: "C:\\Blender\\5.3" } })
    assert.match(checkStatusOutput(path, native).join("\n"), /host.appVersion is not display text/)
    const offers = status({ connection: { state: "available", offers: 17 } })
    assert.match(checkStatusOutput(offers, native).join("\n"), /connection.offers is not 0..16/)
    assert.match(checkStatusOutput(status({ contract: 2 }), native).join("\n"), /contract is not 1/)
  })
})

describe("plugin loading", () => {
  const apiMarker = {
    spec: 0,
    id: "demo",
    displayName: "OpenCode for demo",
    family: "api" as const,
    plugin: "neuralnomads.demo",
    rpc: "neuralnomads.demo",
  }

  function pluginPackage(options: { rpc?: string; family?: string } = {}) {
    const dir = join(tempDir("integration-plugin-"), "plugin")
    mkdirSync(join(dir, "dist"), { recursive: true })
    const packageJson = {
      name: "@neuralnomads/opencode-demo",
      version: "0.1.0",
      type: "module",
      exports: { ".": { import: "./dist/index.js" } },
    }
    writeFileSync(join(dir, "package.json"), JSON.stringify(packageJson))
    const statusOutput = {
      contract: 1,
      integration: { id: "demo", version: "0.1.0", family: options.family ?? "api", spec: 0 },
      connection: { state: "signed_out" },
    }
    writeFileSync(
      join(dir, "dist", "index.js"),
      `export default {
  id: "neuralnomads.demo",
  async setup(ctx) {
    await ctx.rpc.register(
      { id: ${JSON.stringify(options.rpc ?? "neuralnomads.demo")}, methods: { status: {} }, events: { status_changed: {} } },
      { status: async () => (${JSON.stringify(statusOutput)}) },
    )
    ctx.tool.transform(() => {})
  },
}
`,
    )
    return { dir, packageJson, marker: apiMarker }
  }

  it("reads the status of a plugin loaded in isolation", async () => {
    const report = await checkPluginLoad(pluginPackage())
    assert.deepEqual(report.errors, [])
    assert.equal((report.status as { connection: { state: string } }).connection.state, "signed_out")
  })

  it("finds a wrong RPC ID, a status unlike the marker and a missing build", async () => {
    assert.match(
      (await checkPluginLoad(pluginPackage({ rpc: "neuralnomads.other" }))).errors.join("\n"),
      /no RPC registered under neuralnomads.demo/,
    )
    assert.match(
      (await checkPluginLoad(pluginPackage({ family: "native" }))).errors.join("\n"),
      /differ from the marker/,
    )
    const unbuilt = pluginPackage()
    rmSync(join(unbuilt.dir, "dist"), { recursive: true })
    assert.match((await checkPluginLoad(unbuilt)).errors.join("\n"), /build the package first/)
  })

  it("finds a native plugin without the methods its application part and CodeNomad call", async () => {
    const pkg = pluginPackage()
    const errors = (await checkPluginLoad({ ...pkg, marker: { ...apiMarker, family: "native" as const } })).errors
    const text = errors.join("\n")
    for (const method of ["detachHost", "advertise", "withdraw"])
      assert.match(text, new RegExp(`method ${method} is not`))
    assert.match(text, /event hosts_changed is not declared/)
  })
})

describe("application part listener", () => {
  const hasExternalInterface = Object.values(networkInterfaces())
    .flat()
    .some((entry) => entry && !entry.internal && entry.family === "IPv4")

  it("accepts a loopback host that requires its token", async () => {
    const host = await fakeHost()
    const report = await checkHostListener(host.endpoint)
    assert.deepEqual(report.errors, [])
    assert.deepEqual(checkHello(report.hello), [])
  })

  it("fails a host that answers without the token", async () => {
    const host = await fakeHost({ requireToken: false })
    const errors = (await checkHostListener(host.endpoint)).errors.join("\n")
    assert.match(errors, /hello succeeds without a token/)
    assert.match(errors, /hello succeeds with a wrong token/)
  })

  // Listening on every interface raises a firewall prompt on developer machines: CI only.
  it("fails a host reachable outside loopback", { skip: !process.env.CI || !hasExternalInterface }, async () => {
    const host = await fakeHost({ listen: "0.0.0.0" })
    assert.match((await checkHostListener(host.endpoint)).errors.join("\n"), /reachable on a non-loopback interface/)
  })

  it("fails a non-loopback advertised address and a wrong capability hash", async () => {
    const host = await fakeHost({ helloOverride: (hello) => ({ ...hello, capability_hash: "0".repeat(64) }) })
    const errors = (await checkHostListener({ ...host.endpoint, host: "localhost" })).errors.join("\n")
    assert.match(errors, /advertised address localhost is not 127.0.0.1/)
    assert.match(errors, /capability_hash is not the sha256/)
  })
})

describe("code execution gate", () => {
  type Behaviour = {
    declared?: boolean
    defaults?: CodeExecutionSettings
    enabledWhen?: (settings: CodeExecutionSettings) => boolean
    asks?: boolean
  }

  // A plugin and host pair driven in memory; each flag breaks one rule.
  function driver(behaviour: Behaviour = {}): CodeExecutionDriver & { executions: number } {
    const enabledWhen = behaviour.enabledWhen ?? ((settings) => settings.appPreference && settings.pluginOption)
    const state = { executions: 0 }
    return {
      app: "demo",
      get executions() {
        return state.executions
      },
      manifests: [
        { name: "demo_scene_list", risk: "read", execution: { method: "scene.list" } },
        {
          name: "demo_execute_code",
          risk: "destructive",
          dangerous: behaviour.declared ?? true,
          execution: { method: "execute_code" },
        },
      ],
      defaults: behaviour.defaults ?? { appPreference: false, pluginOption: false },
      async probe(settings) {
        const enabled = enabledWhen(settings)
        return {
          helloMethods: enabled ? ["hello", "scene.list", "execute_code"] : ["hello", "scene.list"],
          toolNames: enabled ? ["demo_scene_list", "demo_execute_code"] : ["demo_scene_list"],
          async call(answer) {
            if (!enabled) return { executed: false, permissionsAsked: [], error: "method_disabled" }
            const asked = behaviour.asks === false ? [] : ["demo_execute_code"]
            if (behaviour.asks !== false && answer === "deny") return { executed: false, permissionsAsked: asked }
            state.executions++
            return { executed: true, permissionsAsked: asked }
          },
        }
      },
    }
  }

  it("accepts the declared method, absent by default and asked on every call", async () => {
    assert.deepEqual(await checkCodeExecutionGate(driver()), [])
  })

  it("fails a method that is not declared dangerous", async () => {
    assert.match(
      (await checkCodeExecutionGate(driver({ declared: false }))).join("\n"),
      /demo_execute_code executes supplied code but is not declared dangerous/,
    )
  })

  it("fails a method present in hello with the default settings", async () => {
    const errors = await checkCodeExecutionGate(driver({ defaults: { appPreference: true, pluginOption: true } }))
    assert.match(errors.join("\n"), /defaults to true/)
    const always = await checkCodeExecutionGate(driver({ enabledWhen: () => true }))
    assert.match(always.join("\n"), /hello lists execute_code with application preference false, plugin option false/)
  })

  it("fails a method active when only one option is true", async () => {
    const errors = await checkCodeExecutionGate(driver({ enabledWhen: (s) => s.appPreference || s.pluginOption }))
    assert.match(errors.join("\n"), /hello lists execute_code with application preference true, plugin option false/)
    assert.match(errors.join("\n"), /code runs with application preference false, plugin option true/)
  })

  it("fails a method that runs without the named ask permission", async () => {
    const errors = await checkCodeExecutionGate(driver({ asks: false }))
    assert.match(errors.join("\n"), /does not ask demo_execute_code/)
    assert.match(errors.join("\n"), /runs although the permission was denied/)
  })

  // A method listed while the application part is enabled (no settings): present, asked on every call.
  function present(behaviour: { listed?: boolean; registered?: boolean; asks?: boolean } = {}) {
    const gated = driver({ asks: behaviour.asks })
    const enabled = { appPreference: true, pluginOption: true }
    return {
      app: gated.app,
      manifests: gated.manifests,
      async probe() {
        const probe = await gated.probe(enabled)
        return {
          ...probe,
          helloMethods: behaviour.listed === false ? ["hello", "scene.list"] : probe.helloMethods,
          toolNames: behaviour.registered === false ? ["demo_scene_list"] : probe.toolNames,
        }
      },
    }
  }

  it("accepts a method listed while the application part is enabled and asked on every call", async () => {
    assert.deepEqual(await checkCodeExecutionGate(present()), [])
  })

  it("fails such a method when hello or the plugin omits it, or a call is not asked", async () => {
    assert.match((await checkCodeExecutionGate(present({ listed: false }))).join("\n"), /hello omits execute_code/)
    assert.match(
      (await checkCodeExecutionGate(present({ registered: false }))).join("\n"),
      /the plugin omits demo_execute_code/,
    )
    const errors = (await checkCodeExecutionGate(present({ asks: false }))).join("\n")
    assert.match(errors, /does not ask demo_execute_code/)
    assert.match(errors, /runs although the permission was denied/)
  })

  it("checks manifests statically", () => {
    assert.deepEqual(checkDangerousManifests("demo", [{ name: "demo_scene_list", risk: "read" }]), [])
    const errors = checkDangerousManifests("demo", [
      { name: "demo_eval", risk: "write" },
      { name: "demo_run", risk: "write", dangerous: true },
      { name: "demo_other", risk: "destructive", dangerous: true },
    ]).join("\n")
    assert.match(errors, /demo_eval executes supplied code/)
    assert.match(errors, /at most one dangerous method/)
    assert.match(errors, /must be named demo_execute_code/)
    assert.match(errors, /demo_run must have risk "destructive"/)
  })
})

describe("sentinel scenario", () => {
  // A target that serves the contract but leaks what its host advertised.
  function leakyTarget(leak: "hosts" | "event" | "hello" | "binding") {
    const offers: Array<Record<string, unknown>> = []
    const events: Array<{ name: string; data: unknown }> = []
    let bound = false
    const authority = { serviceGeneration: "service_1", registryGeneration: "registry_1", revision: 0 }
    return {
      events,
      async call(method: string, input: unknown) {
        const value = input as Record<string, unknown>
        switch (method) {
          case "advertise": {
            const { rawHostRequest } = await import("../src/fake-host.ts")
            const response = (await rawHostRequest(
              { host: "127.0.0.1", port: value.port as number },
              { id: "x", token: value.token, method: "hello", params: {} },
            )) as { result: { document?: string; app_version: string } }
            const known = offers.some((offer) => offer.key === value.offer_key)
            if (!known) offers.push({ key: value.offer_key, label: value.label, hello: response.result })
            events.push({ name: "hosts_changed", data: leak === "event" ? { script_handle: value.script_handle } : {} })
            return { outcome: known ? "renewed" : "accepted" }
          }
          case "hosts":
            return {
              offers: offers.map((offer) => {
                const hello = offer.hello as { document?: string; app_version: string }
                return {
                  offerID: "offer_abcdefgh",
                  label: leak === "hosts" ? `${offer.label} ${offer.key}` : offer.label,
                  protocol: "ok",
                  ...(leak === "hello" ? { document: hello.document, appVersion: hello.app_version } : {}),
                }
              }),
            }
          case "attachHost":
            bound = true
            authority.revision++
            return { outcome: "attached" }
          case "detachHost":
            bound = false
            authority.revision++
            return { outcome: "detached" }
          case "bindingRead":
            return bound
              ? {
                  state: "attached",
                  authority: { ...authority },
                  binding: { offerID: "offer_abcdefgh", label: leak === "binding" ? offers[0]?.key : "Sentinel scene" },
                }
              : { state: "none", authority: { ...authority } }
          case "withdraw":
            offers.length = 0
            return { outcome: "withdrawn" }
          default:
            return status()
        }
      },
    }
  }

  it("fails an output that contains an advertised value", async () => {
    const report = await runSentinelScenario(leakyTarget("hosts"))
    assert.match(report.errors.join("\n"), /hosts: the output contains the offer_key/)
  })

  it("fails a plugin that copies the host's document label and application version", async () => {
    const errors = (await runSentinelScenario(leakyTarget("hello"))).errors.join("\n")
    assert.match(errors, /hosts: the output contains the script_handle/)
    assert.match(errors, /hosts: the output contains the port/)
  })

  it("fails an event that carries a payload", async () => {
    const errors = (await runSentinelScenario(leakyTarget("event"))).errors.join("\n")
    assert.match(errors, /event hosts_changed: contains the script_handle/)
  })

  it("fails an exact-session binding label that contains an advertised secret", async () => {
    const errors = (await runSentinelScenario(leakyTarget("binding"))).errors.join("\n")
    assert.match(errors, /bindingRead attached: the output contains the offer_key/)
    assert.match(errors, /expected the exact attached offer and safe label/)
  })
})
