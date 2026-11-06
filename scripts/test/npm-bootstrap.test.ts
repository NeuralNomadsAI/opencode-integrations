import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"
import { bootstrap, commandPlan, packages, parseArgs, type Run, supportsTrust } from "../npm-bootstrap.ts"

const fresh = { name: "@neuralnomads/opencode-new", dir: "integrations/new/plugin" }
const existing = { name: "@neuralnomads/opencode-old", dir: "integrations/old/plugin" }
const absent = { code: 1, output: '{"error":{"code":"E404"}}' }
const ok = { code: 0, output: "" }

test("the actual CLI refuses non-TTY execution before starting npm", () => {
  const result = spawnSync(process.execPath, [fileURLToPath(new URL("../npm-bootstrap.ts", import.meta.url))], {
    encoding: "utf8",
  })
  assert.equal(result.status, 1)
  assert.match(result.stderr, /An interactive TTY is required/)
  assert.equal(result.stdout, "")
})

test("arguments reject unknown options; trust needs a stable npm >=11.15", () => {
  assert.deepEqual(parseArgs([]), { dryRun: false, trustOnly: false })
  assert.deepEqual(parseArgs(["--trust-only", "--dry-run"]), { dryRun: true, trustOnly: true })
  assert.throws(() => parseArgs(["--provenance"]))
  for (const version of ["11.15.0", "12.0.0", "11.21.2\n"]) assert.equal(supportsTrust(version), true)
  for (const version of ["10.9.1", "11.14.9", "11.15.0-pre", "garbage"]) assert.equal(supportsTrust(version), false)
})

test("discovery selects public integration plugins only", () => {
  const root = mkdtempSync(join(tmpdir(), "npm-bootstrap-"))
  try {
    for (const [dir, manifest] of [
      ["integrations/new/plugin", { name: fresh.name }],
      ["integrations/private/plugin", { name: "private", private: true }],
      ["packages/shared", { name: "shared" }],
    ] as const) {
      mkdirSync(join(root, dir), { recursive: true })
      writeFileSync(join(root, dir, "package.json"), JSON.stringify(manifest))
    }
    mkdirSync(join(root, "integrations/host-only/host"), { recursive: true })
    assert.deepEqual(packages(root), [fresh])
    writeFileSync(join(root, fresh.dir, "package.json"), '{"name":"bad&command"}')
    assert.throws(() => packages(root), /Invalid integration package/)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test("command plan publishes then trusts then enforces MFA; safe reruns", () => {
  assert.deepEqual(commandPlan(fresh, false, false, false), [
    ["publish", "--workspace", fresh.dir, "--access", "public", "--tag", "latest"],
    [
      "trust",
      "github",
      fresh.name,
      "--file",
      "publish.yml",
      "--repo",
      "NeuralNomadsAI/opencode-integrations",
      "--env",
      "npm-publish",
      "--allow-publish",
      "--yes",
    ],
    ["access", "set", "mfa=publish", fresh.name],
  ])
  assert.deepEqual(commandPlan(existing, true, false, false), [])
  assert.deepEqual(commandPlan(fresh, false, false, true), [])
  assert.deepEqual(commandPlan(existing, true, true, true), [["access", "set", "mfa=publish", existing.name]])
})

function mock(respond: (args: string[]) => { code: number; output: string } = () => ok) {
  const calls: string[][] = []
  const logs: string[] = []
  let pauses = 0
  const run: Run = async (args) => {
    calls.push(args)
    return respond(args)
  }
  return {
    calls,
    logs,
    run,
    log: (text: string) => logs.push(text),
    pause: async () => {
      pauses++
    },
    pauses: () => pauses,
  }
}

test("new batch runs checks once, skips published packages, and spaces every registry call", async () => {
  const m = mock((args) => (args[0] === "view" && args[1] === fresh.name ? absent : ok))
  assert.equal(await bootstrap([fresh, existing], parseArgs([]), m.run, m.log, m.pause), true)
  assert.deepEqual(
    m.calls.slice(0, 6).map((args) => args[0]),
    ["whoami", "ci", "run", "run", "view", "view"],
  )
  assert.deepEqual(m.calls.slice(6), commandPlan(fresh, false, false, false))
  assert.equal(m.pauses(), 5)
  assert.match(m.logs[0] ?? "", /skip two-factor authentication for the next 5 minutes/)
  assert.match(m.logs.join("\n"), /Published: 1/)
  assert.match(m.logs.join("\n"), /Trusted: 1/)
})

test("dry run only queries and prints planned mutations", async () => {
  const m = mock((args) => (args[0] === "view" ? absent : ok))
  await bootstrap([fresh], parseArgs(["--dry-run"]), m.run, m.log, m.pause)
  assert.deepEqual(
    m.calls.map((args) => args[0]),
    ["whoami", "view"],
  )
  assert.match(m.logs.join("\n"), /Would run: npm publish/)
})

test("login failure stops before builds or package queries", async () => {
  const m = mock(() => ({ code: 1, output: "" }))
  await assert.rejects(bootstrap([fresh], parseArgs([]), m.run, m.log, m.pause), /npm login/)
  assert.deepEqual(m.calls, [["whoami"]])
})

test("query errors fail closed; one bad package does not stop the rest", async () => {
  const m = mock((args) =>
    args[0] === "view" ? (args[1] === fresh.name ? { code: 1, output: '{"error":{"code":"E401"}}' } : absent) : ok,
  )
  assert.equal(await bootstrap([fresh, existing], parseArgs([]), m.run, m.log, m.pause), false)
  assert.equal(
    m.calls.some((args) => args[0] === "publish" && args.includes(fresh.dir)),
    false,
  )
  assert.match(m.logs.join("\n"), /Failed: 1[\s\S]*E401/)
  assert.match(m.logs.join("\n"), /Published: 1/)
})

test("trust-only skips missing packages, preserves existing trust and repairs MFA", async () => {
  const m = mock((args) => {
    if (args[0] === "view" && args[1] === fresh.name) return absent
    if (args[0] === "trust") return { code: 0, output: '{"type":"github","id":"existing-config"}' }
    return ok
  })
  await bootstrap([fresh, existing], parseArgs(["--trust-only"]), m.run, m.log, m.pause)
  assert.equal(
    m.calls.some((args) => args[0] === "publish" || args[1] === "github"),
    false,
  )
  assert.deepEqual(m.calls.at(-1), ["access", "set", "mfa=publish", existing.name])
})

test("trust-only creates missing trust; malformed/failed list cannot overwrite it", async () => {
  for (const output of ["", "[]", "{}"]) {
    const m = mock((args) => (args[0] === "trust" && args[1] === "list" ? { code: 0, output } : ok))
    const success = await bootstrap([existing], parseArgs(["--trust-only"]), m.run, m.log, m.pause)
    assert.equal(success, output !== "{}")
    assert.equal(
      m.calls.some((args) => args[1] === "github"),
      output !== "{}",
    )
  }
  const m = mock((args) => (args[0] === "trust" ? { code: 1, output: "" } : ok))
  assert.equal(await bootstrap([existing], parseArgs(["--trust-only"]), m.run, m.log, m.pause), false)
  assert.equal(
    m.calls.some((args) => args[1] === "github"),
    false,
  )
})

test("trust-only accepts npm's multiple-publisher JSON stream without replacing trust", async () => {
  for (const separator of ["\n", "\n\n", "\r\n\r\n"]) {
    const configs = [
      { type: "github", id: "one" },
      { type: "gitlab", id: "two" },
    ]
    const output = configs.map((config) => JSON.stringify(config, null, 2)).join(separator)
    const m = mock((args) => (args[0] === "trust" ? { code: 0, output } : ok))
    assert.equal(await bootstrap([existing], parseArgs(["--trust-only"]), m.run, m.log, m.pause), true)
    assert.equal(
      m.calls.some((args) => args[1] === "github"),
      false,
    )
    assert.deepEqual(m.calls.at(-1), ["access", "set", "mfa=publish", existing.name])
  }
})

test("trust-list authentication inherits the terminal before a captured JSON read", async () => {
  const calls: { args: string[]; capture: boolean }[] = []
  const run: Run = async (args, capture = false) => {
    calls.push({ args, capture })
    return ok
  }
  assert.equal(
    await bootstrap(
      [existing],
      parseArgs(["--trust-only", "--dry-run"]),
      run,
      () => {},
      async () => {},
    ),
    true,
  )
  assert.deepEqual(
    calls.filter(({ args }) => args[0] === "trust"),
    [
      { args: ["trust", "list", existing.name], capture: false },
      { args: ["trust", "list", existing.name, "--json"], capture: true },
    ],
  )
})

test("publish/trust/access failures report exact stage and stop dependent steps", async () => {
  for (const stage of ["publish", "trust", "access"]) {
    const m = mock((args) => (args[0] === "view" ? absent : args[0] === stage ? { code: 7, output: "" } : ok))
    assert.equal(await bootstrap([fresh], parseArgs([]), m.run, m.log, m.pause), false)
    assert.equal(m.calls.at(-1)?.[0], stage)
    assert.match(m.logs.join("\n"), new RegExp(`${stage} .*failed \\(exit 7\\)`))
  }
})

test("failed validation never publishes", async () => {
  const m = mock((args) => (args[0] === "view" ? absent : args[0] === "ci" ? { code: 1, output: "" } : ok))
  assert.equal(await bootstrap([fresh], parseArgs([]), m.run, m.log, m.pause), false)
  assert.match(m.logs.join("\n"), /Failed: 1[\s\S]*Preparation: ci failed/)
  assert.equal(
    m.calls.some((args) => args[0] === "publish"),
    false,
  )
})
