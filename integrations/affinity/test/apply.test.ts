import assert from "node:assert/strict"
import { test } from "node:test"
import type { Plugin } from "@opencode/plugin"
import { createCommandLog, OPERATIONS, validateApply } from "../plugin/src/apply.ts"
import { applyScript, SHAPES } from "../plugin/src/apply-script.ts"
import { contextScript } from "../plugin/src/context-script.ts"
import { setupAffinity } from "../plugin/src/plugin.ts"
import { startFakeAffinity } from "./fake-affinity.ts"

const DOC = "16A43946-2DBC-41D4-9425-CAFE608E1682"
const box = { x: 0, y: 0, width: 10, height: 10 }
const sample = [
  {
    op: "create_shape",
    shape: "ellipse",
    box,
    fill: {
      gradient: {
        angle: 90,
        stops: [
          { colour: "#ffffff", position: 0 },
          { colour: "#000000ff", position: 1 },
        ],
      },
    },
  },
  { op: "create_text", text: "Hi", position: { x: 1, y: 2 }, size: 12, colour: "#0088ff" },
  { op: "set_fill", refs: ["1"], fill: "none" },
  { op: "set_stroke", refs: ["1"], colour: "none", width: 2 },
  { op: "set_opacity", refs: ["1"], opacity: 0.5 },
  { op: "move", refs: ["1"], dx: 5 },
  { op: "set_box", ref: "2", box },
  { op: "rotate", refs: ["3"], degrees: 15 },
  { op: "align", refs: ["4"], horizontal: "center", to: "page" },
  { op: "rename", refs: ["4"], name: "Logo" },
  { op: "set_visible", refs: ["4"], visible: false },
  { op: "set_text", ref: "5", text: "New" },
  { op: "boolean", refs: ["6", "7"], mode: "union" },
  { op: "group", refs: ["8", "9"] },
  { op: "ungroup", refs: ["10"] },
  { op: "delete", refs: ["11"] },
  { op: "select", refs: ["12"] },
]

test("every operation validates and normalizes; the sample covers all of them", () => {
  assert.deepEqual([...new Set(sample.map((op) => op.op))].sort(), [...OPERATIONS].sort())
  const { plan, commandId } = validateApply({
    document: DOC,
    if_revision: "h3-4",
    command_id: "logo-1",
    operations: sample,
    expect: [{ ref: "1", exists: true, type: "ShapeNode" }],
  })
  assert.equal(commandId, "logo-1")
  assert.equal(plan.if_revision, "h3-4")
  const ops = plan.operations as Record<string, unknown>[]
  assert.deepEqual(ops[1], {
    op: "create_text",
    text: "Hi",
    position: { x: 1, y: 2 },
    size: 12,
    fill: { colour: "#0088ff" },
  })
  assert.deepEqual(ops[5], { op: "move", refs: ["1"], dx: 5, dy: 0 })
  assert.equal(ops[8]?.to, "page")
})

test("invalid plans are refused before reaching Affinity", () => {
  const bad: [unknown, RegExp][] = [
    [{ operations: [] }, /1 to 50 operations/],
    [{ operations: Array.from({ length: 51 }, () => ({ op: "select", refs: ["1"] })) }, /1 to 50/],
    [{ operations: [{ op: "explode", refs: ["1"] }] }, /expected one of/],
    [{ operations: [{ op: "create_shape", shape: "dragon", box }] }, /operations\[0\]\.shape/],
    [{ operations: [{ op: "create_shape", shape: "star", box: { ...box, width: 0 } }] }, /box\.width/],
    [{ operations: [{ op: "set_fill", refs: ["1"], fill: { colour: "red" } }] }, /fill\.colour/],
    [
      {
        operations: [
          { op: "set_fill", refs: ["1"], fill: { gradient: { stops: [{ colour: "#ffffff", position: 0 }] } } },
        ],
      },
      /2 to 16 stops/,
    ],
    [{ operations: [{ op: "move", refs: ["1", "1"], dx: 1 }] }, /duplicate ref/],
    [{ operations: [{ op: "move", refs: ["1; alert(1)"], dx: 1 }] }, /invalid string/],
    [{ operations: [{ op: "align", refs: ["1"], horizontal: "left" }] }, /2 to 200 refs/],
    [{ operations: [{ op: "align", refs: ["1", "2"] }] }, /horizontal or vertical/],
    [{ operations: [{ op: "boolean", refs: ["1"], mode: "union" }] }, /2 to 200 refs/],
    [{ operations: [{ op: "set_opacity", refs: ["1"], opacity: 2 }] }, /opacity/],
    [{ operations: [{ op: "rename", refs: ["1"], name: "a", extra: 1 }] }, /unknown field extra/],
    [{ document: "current", operations: [{ op: "select", refs: ["1"] }] }, /document/],
    [{ if_revision: "anything", operations: [{ op: "select", refs: ["1"] }] }, /if_revision/],
    [{ expect: [{ ref: "1", exists: "yes" }], operations: [{ op: "select", refs: ["1"] }] }, /exists/],
  ]
  for (const [input, message] of bad) assert.throws(() => validateApply(input as Record<string, unknown>), message)
})

test("generated programs are valid JavaScript and embed plans as data only", () => {
  const { plan } = validateApply({ operations: sample })
  const script = applyScript({
    ...plan,
    operations: [...(plan.operations as unknown[]), { op: "rename", refs: ["1"], name: "'); evil(); ('" }],
  })
  assert.doesNotThrow(() => new Function("require", "console", script))
  assert.ok(script.includes(JSON.stringify("'); evil(); ('")))
  assert.doesNotThrow(() => new Function("require", "console", contextScript({ depth: 2, nodes: 10 })))
  for (const shape of Object.values(SHAPES)) assert.match(shape, /^Shape[A-Za-z]+$/)
})

test("command log keeps the most recent results within its limit", () => {
  const log = createCommandLog(2)
  log.set("a", "1")
  log.set("b", "2")
  log.set("c", "3")
  assert.equal(log.get("a"), undefined)
  assert.equal(log.get("c"), "3")
})

function harness(url: URL) {
  const tools = new Map<string, { execute: (input: unknown, context: unknown) => Promise<{ content: unknown }> }>()
  const ctx = {
    options: { url: String(url) },
    tool: {
      transform: async (callback: (editor: unknown) => void) =>
        callback({
          namespace: () => {},
          add: (tool: { name: string; execute: (input: unknown, context: unknown) => Promise<{ content: unknown }> }) =>
            tools.set(tool.name, tool),
        }),
    },
    skill: {
      transform: async (callback: (editor: unknown) => void) => callback({ get: () => undefined, add: () => {} }),
    },
    permission: { assert: async () => {} },
    rpc: { register: async () => ({ events: { emit: async () => {} } }) },
  }
  const context = { sessionID: "s", agent: "build", messageID: "m", id: "c", signal: new AbortController().signal }
  return {
    ctx: ctx as unknown as Plugin.Context,
    apply: async (input: unknown) => (await tools.get("apply")?.execute(input, context))?.content as string,
  }
}

test("apply runs one fixed program, replays command IDs and reports refusals and unknown outcomes", async () => {
  const outcomes = [
    { applied: true, revision: "h2-2", undo: "one_step", created: [{ ref: "9" }], changed: [], removed: [] },
    { applied: false, error: "expectation_failed: 1 is missing" },
    { applied: "unknown", error: "affinity_error: boom" },
  ]
  let scripts = 0
  const fake = await startFakeAffinity({
    onScript: (script) => {
      scripts++
      assert.ok(script.includes("const PLAN = "))
      return { text: `${JSON.stringify(outcomes.shift())}\n` }
    },
  })
  const h = harness(fake.url)
  const cleanup = await setupAffinity(h.ctx)
  try {
    const input = { command_id: "c1", operations: [{ op: "create_shape", shape: "star", box }] }
    const first = JSON.parse(await h.apply(input))
    assert.equal(first.undo, "one_step")
    const again = JSON.parse(await h.apply(input))
    assert.equal(again.replayed, true)
    assert.equal(scripts, 1, "a replayed command ID applies nothing")
    await assert.rejects(
      () => h.apply({ operations: [{ op: "delete", refs: ["1"] }] }),
      /Nothing was changed: expectation_failed/,
    )
    await assert.rejects(() => h.apply({ operations: [{ op: "delete", refs: ["1"] }] }), /may be applied/)
    await assert.rejects(() => h.apply({ operations: [{ op: "nope" }] }), /expected one of/)
    assert.equal(scripts, 3, "invalid plans never reach Affinity")
  } finally {
    cleanup()
    await fake.close()
  }
})
