import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, describe, it } from "node:test"

import {
  buildPluginTools,
  buildToolInput,
  integrationIdFromPackageName,
  loadMethodRegistry,
  type MethodManifest,
  pluginIdFor,
  registerPluginTools,
  runCoherenceCheck,
  validateAndNormalizeArgs,
} from "../src/index.ts"

const sceneList: MethodManifest = {
  name: "demo_scene_list",
  domain: "scene",
  description: "List objects.",
  kind: "host-backed",
  risk: "read",
  execution: { strategy: "host_method", method: "scene.list" },
  args: {
    limit: { type: "integer", default: 20, description: "Maximum objects." },
    kind: { type: "string", enum: ["mesh", "light"] },
    names: { type: "array", items: { type: "string" } },
  },
}

const objectRename: MethodManifest = {
  name: "demo_object_rename",
  domain: "object",
  description: "Rename an object.",
  kind: "host-backed",
  risk: "write",
  execution: { strategy: "host_method", method: "object.rename" },
  verify: { method: "demo_scene_list" },
  args: { name: { type: "string", required: true }, to: { type: "string", required: true } },
}

const work = mkdtempSync(join(tmpdir(), "integration-kit-"))
after(() => rmSync(work, { recursive: true, force: true }))

function fakeContext() {
  return { signal: new AbortController().signal } as never
}

describe("validateAndNormalizeArgs", () => {
  it("applies defaults and validates types", () => {
    assert.deepEqual(validateAndNormalizeArgs(sceneList, {}), { limit: 20 })
    assert.deepEqual(validateAndNormalizeArgs(sceneList, { kind: "mesh", names: ["a"] }), {
      limit: 20,
      kind: "mesh",
      names: ["a"],
    })
    assert.throws(() => validateAndNormalizeArgs(sceneList, { limit: 1.5 }), /Expected integer/)
    assert.throws(() => validateAndNormalizeArgs(sceneList, { kind: "camera" }), /Expected one of: mesh, light/)
    assert.throws(() => validateAndNormalizeArgs(sceneList, { names: [1] }), /params\.names\[0\]/)
  })

  it("rejects unknown and missing arguments", () => {
    assert.throws(() => validateAndNormalizeArgs(sceneList, { extra: 1 }), /Unexpected argument/)
    assert.throws(() => validateAndNormalizeArgs(objectRename, { name: "Cube" }), /Missing required argument/)
  })
})

describe("buildToolInput", () => {
  it("produces a closed JSON Schema object", () => {
    const schema = buildToolInput(objectRename.args)
    assert.deepEqual(schema, {
      type: "object",
      properties: { name: { type: "string" }, to: { type: "string" } },
      required: ["name", "to"],
      additionalProperties: false,
    })
  })
})

describe("buildPluginTools", () => {
  it("strips the namespace prefix and validates input before executing", async () => {
    const calls: unknown[] = []
    const tools = buildPluginTools([sceneList, objectRename], "demo", async (manifest, args) => {
      calls.push([manifest.name, args])
      return "ok"
    })
    assert.deepEqual(
      tools.map((tool) => tool.name),
      ["scene_list", "object_rename"],
    )
    assert.deepEqual(tools[0]?.options, { namespace: "demo", codemode: true })
    const result = await tools[0]?.execute({}, fakeContext())
    assert.deepEqual(result, { content: "ok" })
    assert.deepEqual(calls, [["demo_scene_list", { limit: 20 }]])
    await assert.rejects(async () => tools[1]?.execute({ name: "Cube" }, fakeContext()), /Missing required/)
  })

  it("rejects names outside the namespace and duplicates", () => {
    const run = async () => ""
    assert.throws(() => buildPluginTools([{ ...sceneList, name: "other_list" }], "demo", run), /Invalid or duplicate/)
    assert.throws(() => buildPluginTools([sceneList, sceneList], "demo", run), /Invalid or duplicate/)
  })

  it("does not execute after cancellation", async () => {
    let executed = false
    const [tool] = buildPluginTools([sceneList], "demo", async () => {
      executed = true
      return ""
    })
    const controller = new AbortController()
    controller.abort()
    await assert.rejects(async () => tool?.execute({}, { signal: controller.signal } as never))
    assert.equal(executed, false)
  })

  it("registers the namespace and every tool", () => {
    const added: string[] = []
    const namespaces: unknown[] = []
    const editor = {
      namespace: (value: unknown) => namespaces.push(value),
      add: (tool: { name: string }) => added.push(tool.name),
    }
    const tools = buildPluginTools([sceneList], "demo", async () => "")
    registerPluginTools(editor as never, "demo", "Demo tools", tools)
    assert.deepEqual(namespaces, [{ name: "demo", description: "Demo tools" }])
    assert.deepEqual(added, ["scene_list"])
  })
})

describe("loadMethodRegistry and runCoherenceCheck", () => {
  const methods = join(work, "methods")
  const skills = join(work, "skills")
  mkdirSync(join(methods, "scene"), { recursive: true })
  mkdirSync(join(skills, "demo-scene"), { recursive: true })
  writeFileSync(join(methods, "scene", "list.json"), JSON.stringify(sceneList))
  writeFileSync(join(methods, "rename.json"), JSON.stringify(objectRename))
  writeFileSync(
    join(skills, "demo-scene", "SKILL.md"),
    "---\nname: demo-scene\n---\nUse `demo_scene_list` then `demo_object_rename`.\n",
  )

  it("loads manifests recursively in name order", () => {
    const registry = loadMethodRegistry(methods)
    assert.deepEqual(
      registry.list().map((method) => method.name),
      ["demo_object_rename", "demo_scene_list"],
    )
    assert.equal(registry.has("demo_scene_list"), true)
    assert.equal(registry.get("missing"), null)
  })

  it("passes a coherent integration and flags unknown skill tools", () => {
    const registry = loadMethodRegistry(methods)
    const ok = runCoherenceCheck({ integration: "demo", toolPrefix: "demo_", skillsDir: skills, registry })
    assert.equal(ok.ok, true, JSON.stringify(ok.errors))

    writeFileSync(join(skills, "demo-scene", "SKILL.md"), "Use `demo_missing`.\n")
    const bad = runCoherenceCheck({ integration: "demo", toolPrefix: "demo_", skillsDir: skills, registry })
    assert.equal(bad.ok, false)
    assert.match(bad.errors.join("\n"), /unknown or non-public tool demo_missing/)
  })
})

describe("marker helpers", () => {
  it("derives the integration ID from the package name", () => {
    assert.equal(integrationIdFromPackageName("@neuralnomads/opencode-blender"), "blender")
    assert.equal(integrationIdFromPackageName("@neuralnomads/opencode-Blender"), null)
    assert.equal(integrationIdFromPackageName("opencode-blender"), null)
    assert.equal(pluginIdFor("blender"), "neuralnomads.blender")
  })
})
