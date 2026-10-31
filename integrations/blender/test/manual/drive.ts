// Driver of the interface protocol (MANUAL.md, AC-B3 interface part, AC-B4, AC-B6 interface part).
// It starts Blender 5.2 WITH its interface in an isolated profile, stands in for an OpenCode service
// (a fake registration in the profile's XDG_STATE_HOME receives the add-on's announcement), and sends
// context and apply requests straight to the add-on's host.
//
//   node integrations/blender/test/manual/drive.ts          # guided: you tick the consent and check the UI
//   node integrations/blender/test/manual/drive.ts --auto   # unattended: consent and checks through the
//                                                           # dangerous method, enabled for this profile only
//
// BLENDER_TEST_BIN names the Blender executable. Nothing touches your Blender profile or temporary folder.

import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync } from "node:fs"
import { createServer, type IncomingMessage } from "node:http"
import type net from "node:net"
import { join } from "node:path"
import { stdin, stdout } from "node:process"
import { createInterface } from "node:readline/promises"

import { HostError, requestHost } from "../../plugin/src/kit.ts"
import { blenderBin, installAddon, isolatedRoot } from "../helpers/blender.ts"

const auto = process.argv.includes("--auto")
if (!blenderBin) throw new Error("Set BLENDER_TEST_BIN to a Blender 5.2 executable")

type Offer = { port: number; token: string; script_handle: string }
const iso = isolatedRoot("blender-manual-")
installAddon(iso)
const results: Array<{ step: string; ok: boolean; note?: string }> = []
const record = (step: string, ok: boolean, note?: string) => {
  results.push({ step, ok, ...(note ? { note } : {}) })
  console.log(`${ok ? "PASS" : "FAIL"} ${step}${note ? ` (${note})` : ""}`)
}

// The fake service: answers /api/info and records advertise and withdraw.
let offer: Offer | undefined
let withdrawn = false
const read = async (request: IncomingMessage) => {
  let text = ""
  for await (const chunk of request) text += chunk
  return text ? JSON.parse(text) : {}
}
const service = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1")
  const send = (body: unknown) => {
    response.writeHead(200, { "content-type": "application/json" })
    response.end(JSON.stringify(body))
  }
  if (url.pathname === "/api/info") return send({ pid: process.pid, version: "2.2.5" })
  const body = await read(request)
  if (url.pathname.endsWith("/advertise")) {
    offer = body.input as Offer
    return send({ output: { outcome: "renewed" } })
  }
  if (url.pathname.endsWith("/withdraw")) {
    withdrawn = true
    return send({ output: { outcome: "withdrawn" } })
  }
  send({})
})
await new Promise<void>((resolve) => service.listen(0, "127.0.0.1", () => resolve()))
const registrations = join(iso.env.XDG_STATE_HOME as string, "opencode")
mkdirSync(registrations, { recursive: true })
writeFileSync(
  join(registrations, "service.json"),
  JSON.stringify({
    url: `http://127.0.0.1:${(service.address() as net.AddressInfo).port}`,
    pid: process.pid,
    version: "2.2.5",
  }),
)

const startup = [
  "import addon_utils, bpy",
  'addon_utils.enable("bl_ext.user_default.opencode_link", default_set=True, handle_error=None)',
  ...(auto
    ? [
        'prefs = bpy.context.preferences.addons["bl_ext.user_default.opencode_link"].preferences',
        "prefs.allow_code_execution = True",
        "bpy.app.timers.register(lambda: setattr(prefs, 'offer_session', True), first_interval=2.0)",
      ]
    : []),
].join("\n")
const blender = spawn(
  blenderBin,
  ["--factory-startup", "--no-window-focus", "-p", "40", "40", "1280", "800", "--python-expr", startup],
  {
    env: iso.env,
    windowsHide: false,
    stdio: "ignore",
  },
)

const prompt = createInterface({ input: stdin, output: stdout })
const ask = async (text: string) => {
  if (auto) return true
  const answer = (await prompt.question(`${text} [y/n] `)).trim().toLowerCase()
  return answer === "" || answer.startsWith("y")
}
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const host = async (method: string, params: Record<string, unknown> = {}) => {
  if (!offer) throw new Error("no offer yet")
  return (await requestHost({ host: "127.0.0.1", port: offer.port, token: offer.token }, method, {
    script_handle: offer.script_handle,
    ...params,
  })) as Record<string, unknown>
}
const code = async (source: string) =>
  ((await host("execute_code", { code: source, code_execution: true })) as { stdout: string }).stdout.trim()
const state = async () => (await host("context")) as { revision: number; script_handle: string }
let commands = 0
const apply = async (operations: unknown[], summary: string) => {
  const current = await state()
  commands += 1
  return await host("apply", { if_revision: current.revision, command_id: `manual-${commands}`, summary, operations })
}
const objects = async () =>
  JSON.parse(await code("import json; print(json.dumps(sorted(o.name for o in bpy.data.objects)))")) as string[]
const UNDO = [
  "w = bpy.context.window_manager.windows[0]",
  "a = next(x for x in w.screen.areas if x.type == 'VIEW_3D')",
  "with bpy.context.temp_override(window=w, screen=w.screen, area=a):",
  "    print(bpy.ops.ed.%s())",
].join("\n")

try {
  if (!auto)
    console.log(
      "In Blender: Preferences ▸ Add-ons is already enabled for OpenCode Link. Tick 'Offer this session to OpenCode' in the 3D Viewport sidebar (N) ▸ OpenCode.",
    )
  for (let attempt = 0; attempt < 240 && !offer; attempt += 1) await wait(500)
  record("consent: the add-on offers the session after the tick (HST-10)", Boolean(offer))
  if (!offer) throw new Error("no announcement")

  const hello = (await requestHost({ host: "127.0.0.1", port: offer.port, token: offer.token }, "hello", {})) as {
    capabilities: { background: boolean }
  }
  record("hello: interface mode", hello.capabilities.background === false)

  // AC-B4: one named undo step for several undoable operations.
  const before = auto ? await objects() : []
  const three = await apply(
    ["A", "B", "C"].map((name) => ({
      op: "scene.create_object",
      args: { primitive: "CUBE", name: `Manual_${name}`, location: [0, 0, 0] },
    })),
    "three cubes",
  )
  record("apply of three operations", three.status === "applied", String(three.undo_step))
  if (auto) {
    await code(UNDO.replace("%s", "undo"))
    const afterUndo = await objects()
    record("one Ctrl+Z removes the whole apply", JSON.stringify(afterUndo) === JSON.stringify(before))
    await code(UNDO.replace("%s", "redo"))
    const afterRedo = await objects()
    record(
      "one redo restores it",
      ["A", "B", "C"].every((name) => afterRedo.includes(`Manual_${name}`)),
    )
  } else {
    record(
      "Edit ▸ Undo History shows ONE step 'OpenCode: three cubes'; one Ctrl+Z removes the three cubes; Ctrl+Shift+Z brings them back",
      await ask("Is that what you see?"),
    )
  }

  // AC-B4: a failure during the apply restores the state.
  const beforeFailure = auto ? await objects() : []
  const failed = await apply(
    [
      { op: "scene.create_object", args: { primitive: "CUBE", name: "Manual_Partial" } },
      { op: "scene.delete_object", args: { name: "Manual_Missing" } },
    ],
    "failing change",
  )
  record(
    "a failing apply reports a restoration",
    failed.status === "failed" && failed.restored === true,
    String(failed.message),
  )
  if (auto) record("the state is restored", JSON.stringify(await objects()) === JSON.stringify(beforeFailure))
  else record("No 'Manual_Partial' object; your earlier undo steps are intact", await ask("Correct?"))

  // AC-B3 (interface): Object mode only.
  if (auto) {
    await code(
      [
        "o = bpy.data.objects['Manual_A']",
        "bpy.context.view_layer.objects.active = o",
        "w = bpy.context.window_manager.windows[0]",
        "a = next(x for x in w.screen.areas if x.type == 'VIEW_3D')",
        "with bpy.context.temp_override(window=w, screen=w.screen, area=a, active_object=o):",
        "    bpy.ops.object.mode_set(mode='EDIT')",
      ].join("\n"),
    )
  } else await ask("Select Manual_A and press Tab to enter Edit mode, then answer y")
  try {
    await apply([{ op: "scene.create_object", args: { primitive: "EMPTY", name: "Manual_Edit" } }], "in edit mode")
    record("mutation in Edit mode is refused", false)
  } catch (error) {
    record(
      "mutation in Edit mode is refused (mode_conflict)",
      error instanceof HostError && error.code === "mode_conflict",
    )
  }
  if (auto) {
    await code(
      "w = bpy.context.window_manager.windows[0]\na = next(x for x in w.screen.areas if x.type == 'VIEW_3D')\nwith bpy.context.temp_override(window=w, screen=w.screen, area=a):\n    bpy.ops.object.mode_set(mode='OBJECT')",
    )
  } else await ask("Press Tab again to go back to Object mode, then answer y")

  // AC-B3 (interface): the exact revision and command replay.
  const current = await state()
  if (auto) await code("bpy.data.objects['Manual_A'].location.x += 1.0")
  else await ask("Move Manual_A a little (G, mouse, click), then answer y")
  await wait(300)
  try {
    await host("apply", {
      if_revision: current.revision,
      command_id: "manual-stale",
      operations: [{ op: "scene.delete_object", args: { name: "Manual_A" } }],
    })
    record("an apply on an old revision is refused", false)
  } catch (error) {
    record(
      "an apply on an old revision is refused (stale_revision)",
      error instanceof HostError && error.code === "stale_revision",
    )
  }
  const fresh = await state()
  const replayParams = {
    if_revision: fresh.revision,
    command_id: "manual-replay",
    operations: [{ op: "scene.create_object", args: { primitive: "EMPTY", name: "Manual_Once" } }],
  }
  await host("apply", replayParams)
  const replay = await host("apply", replayParams)
  record("replaying a command_id applies nothing", replay.replayed === true)

  // AC-B6 (interface): a real viewport capture.
  const captured = (await host("context", {
    reads: [{ name: "scene.get_viewport_screenshot", args: { max_size: 400 } }],
  })) as {
    results: Array<{ ok: boolean; result?: { image?: { mime: string; data: string } }; error?: string }>
  }
  const image = captured.results[0]?.result?.image
  const png = image ? Buffer.from(image.data, "base64") : Buffer.alloc(0)
  if (image) writeFileSync(join(iso.root, "viewport.png"), png)
  record(
    "viewport capture returns a PNG",
    png.subarray(0, 4).toString("hex") === "89504e47",
    captured.results[0]?.error,
  )

  // HST-10: unticking the consent withdraws the offer and closes the listener.
  if (auto)
    await code("bpy.context.preferences.addons['bl_ext.user_default.opencode_link'].preferences.offer_session = False")
  else await ask("Untick 'Offer this session to OpenCode', then answer y")
  for (let attempt = 0; attempt < 30 && !withdrawn; attempt += 1) await wait(500)
  record("unticking withdraws the offer", withdrawn)
  try {
    await requestHost({ host: "127.0.0.1", port: offer.port, token: offer.token }, "hello", {}, { timeoutMs: 2_000 })
    record("the listener is closed", false)
  } catch (error) {
    record("the listener is closed", error instanceof HostError)
  }
} finally {
  prompt.close()
  blender.kill()
  await new Promise((resolve) => blender.once("exit", resolve))
  service.close()
  console.log(JSON.stringify({ blender: blenderBin, results }, null, 2))
  iso.cleanup()
  process.exitCode = results.every((result) => result.ok) ? 0 : 1
}
