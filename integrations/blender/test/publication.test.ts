import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { test } from "node:test"

test("only Blender is publishable, with public access and trusted-publishing metadata", () => {
  const plugin = new URL("../plugin/", import.meta.url)
  const pkg = JSON.parse(readFileSync(new URL("package.json", plugin), "utf8"))
  assert.equal(pkg.name, "@neuralnomads/opencode-blender")
  assert.equal(pkg.version, "0.4.2")
  assert.equal(pkg.private, undefined)
  assert.equal(pkg.publishConfig.access, "public")
  assert.equal(pkg.repository.url, "https://github.com/NeuralNomadsAI/opencode-integrations")
  assert.equal(pkg.repository.directory, "integrations/blender/plugin")
  assert.ok(pkg.files.includes("host"))
  assert.ok(existsSync(new URL(pkg.opencodeIntegration.host.payload, plugin)))

  for (const path of [
    "../../../package.json",
    "../../../packages/testing/package.json",
    "../../../packages/integration-kit/package.json",
  ]) {
    assert.equal(JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8")).private, true, path)
  }
})
