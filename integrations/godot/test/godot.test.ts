// OpenCode Link in a real Godot editor (GODOT_TEST_BIN, run --headless with private settings): installed by the
// plugin, offered to a private OpenCode service bound to the plugin's RPC, then every tool against it. Skipped
// without GODOT_TEST_BIN.

import assert from "node:assert/strict"
import type { ChildProcess } from "node:child_process"
import { existsSync, mkdtempSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { after, before, describe, it } from "node:test"

import { requestHost } from "../../../packages/integration-kit/src/index.ts"
import { installHost } from "../plugin/src/host-install.ts"
import { readPackage } from "../plugin/src/integration.ts"
import {
  content,
  fakeService,
  type LoadedPlugin,
  loadPlugin,
  makeProject,
  privateEnv,
  removeAll,
  startEditor,
  until,
} from "./helpers.ts"

const godot = process.env.GODOT_TEST_BIN
const SCENE = `[gd_scene format=3]

[node name="Main" type="Node3D"]

[node name="Box" type="MeshInstance3D" parent="."]
`
const MOVER = "extends Node3D\n\nfunc _process(_delta):\n\tposition.x += 1.0\n"
const CRASHER = 'extends Node\n\nfunc _ready():\n\tOS.crash("test crash")\n'
const CRASH_SCENE = `[gd_scene format=3]

[ext_resource type="Script" path="res://crasher.gd" id="1"]

[node name="Crash" type="Node"]
script = ExtResource("1")
`
const norm = (path: string) => path.replaceAll("\\", "/").replace(/\/$/, "").toLowerCase()

describe("OpenCode Link in a real Godot editor", { skip: !godot, concurrency: false }, () => {
  let root: string
  let project: string
  let plugin: LoadedPlugin
  let service: Awaited<ReturnType<typeof fakeService>>
  let editor: ChildProcess
  let log: string
  const code = async (source: string, extra: object = {}) =>
    content(await plugin.tool("godot_execute_code", { code: source, ...extra }))

  before(async () => {
    root = mkdtempSync(join(tmpdir(), "godot-real-"))
    const env = privateEnv(root)
    env.NEURALNOMADS_GODOT_NODE = process.execPath
    project = makeProject("Trial", {
      "main.tscn": SCENE,
      "mover.gd": MOVER,
      "crasher.gd": CRASHER,
      "crash.tscn": CRASH_SCENE,
    })
    assert.equal(installHost(readPackage().opencodeIntegration, project).outcome, "installed")
    plugin = await loadPlugin({ directory: project })
    const global = norm(join(root, "config", "opencode"))
    service = await fakeService(plugin, root, (directory) => norm(directory) === global)
    log = join(root, "editor.log")
    editor = startEditor(godot as string, project, env, log)
    await until(() => plugin.registry.hosts().offers[0], 120_000, "the editor's offer")
  })

  after(async () => {
    if (editor && editor.exitCode === null) editor.kill()
    await plugin?.cleanup()
    await service?.close()
    await new Promise((done) => setTimeout(done, 500))
    removeAll(project, root)
  })

  it("offers the project from the editor's start, labelled with the project and its scene", async () => {
    const offer = plugin.registry.hosts().offers[0]
    assert.equal(offer?.label, "Godot: Trial — no scene")
    assert.match(String(offer?.appVersion), /^4\.\d+/)
    const hosts = content(await plugin.tool("godot_hosts"))
    assert.equal(hosts.editors.length, 1)
    assert.equal(hosts.host.state, "ok")
    // The advertise went to the global configuration Location, where the plugin is loaded.
    assert.ok(service.calls.some((call) => call.method === "advertise"))
  })

  it("runs GDScript in the editor; a change is one undo action", async () => {
    await code('EditorInterface.open_scene_from_path("res://main.tscn")')
    await until(() => plugin.registry.hosts().offers[0]?.label === "Godot: Trial — main.tscn", 15_000, "the new label")
    const context = content(await plugin.tool("godot_context"))
    assert.equal(context.scene.path, "res://main.tscn")
    assert.deepEqual(
      context.nodes.map((node: { path: string }) => node.path),
      [".", "Box"],
    )
    const changed = await code(
      'var crate := MeshInstance3D.new()\ncrate.name = "Crate"\nscene.add_child(crate)\ncrate.owner = scene\nscene.get_node("Box").position = Vector3(1, 2, 3)\nprint("added")\nresult = {"children": scene.get_child_count()}',
    )
    assert.equal(changed.status, "executed")
    assert.equal(changed.undo_step, "OpenCode: code")
    assert.deepEqual(changed.changes, { properties: 1, added: ["Crate"] })
    assert.deepEqual(changed.result, { children: 2 })
    assert.equal(changed.stdout, "added\n")
    // The editor's own undo puts both back, and redo brings them again.
    const undone = await code(
      'var history := EditorInterface.get_editor_undo_redo().get_history_undo_redo(EditorInterface.get_editor_undo_redo().get_object_history_id(scene))\nresult.action = history.get_current_action_name()\nresult.undone = history.undo()\nresult.crate = scene.has_node("Crate")\nresult.box = scene.get_node("Box").position\nhistory.redo()\nresult.again = scene.has_node("Crate") and scene.get_node("Box").position == Vector3(1, 2, 3)',
      { read_only: true },
    )
    assert.deepEqual(undone.result, {
      action: "OpenCode: code",
      undone: true,
      crate: false,
      box: [0, 0, 0],
      again: true,
    })
  })

  it("a failed run is put back, with the line and what Godot knows", async () => {
    const failed = await code(
      'scene.get_node("Box").position = Vector3(5, 5, 5)\nscene.add_child(Node3D.new())\nscene.get_node("Box").positon = Vector3.ZERO',
    )
    assert.equal(failed.status, "failed")
    assert.equal(failed.line, 3)
    assert.equal(failed.restored, true)
    assert.match(failed.message, /positon/)
    assert.match(failed.hint, /position/)
    const box = content(await plugin.tool("godot_context", { properties: ["position"], match: "Box" }))
    assert.deepEqual(box.nodes[0].values.position, [1, 2, 3])
    assert.equal(box.nodes.length, 1)

    const parse = await code("var a = Vectr3(1, 2, 3)\nresult.a = a")

    assert.equal(parse.status, "failed")
    assert.equal(parse.line, 1)
    assert.match(parse.hint, /Vector3/)
    const method = await code("scene.add_chlid(Node.new())")
    assert.match(method.hint, /add_child\(node: Node/)
    const missing = await code('scene.get_node("Bx").position = Vector3.ZERO')
    assert.match(missing.hint, /No child Bx under Main; its children: Box/)
  })

  it("quarantines a failed run it cannot prove undone, until the agent releases it", async () => {
    const failed = await code('scene.get_node("Crate").free()\nvar missing = null\nmissing.call("nothing")')
    assert.equal(failed.status, "failed")
    assert.equal(failed.restored, false)
    assert.deepEqual(failed.quarantine.freed, ["Crate"])
    await assert.rejects(plugin.tool("godot_execute_code", { code: "pass" }), /quarantine/)
    const read = content(await plugin.tool("godot_context"))
    assert.equal(read.quarantined, true)
    const released = content(await plugin.tool("godot_context", { release_quarantine: read.revision }))
    assert.equal(released.released, true)
    assert.equal((await code("pass")).status, "executed")
  })

  it("answers busy within 10 s while a run holds the main loop, and hello meanwhile", async () => {
    const long = plugin.tool("godot_execute_code", { code: "OS.delay_msec(13000)", read_only: true })
    await new Promise((done) => setTimeout(done, 500))
    const offer = plugin.registry.hosts().offers[0]
    const bound = plugin.registry.binding("ses_test")
    assert.ok(offer && bound)
    const started = Date.now()
    await requestHost(bound.endpoint, "hello", {}, { timeoutMs: 2_000 })
    assert.ok(Date.now() - started < 2_000)
    await assert.rejects(plugin.tool("godot_context"), /still running execute_code/)
    assert.ok(Date.now() - started < 12_000)
    assert.equal(content(await long).status, "executed")
  })

  it("inspects THIS Godot: search, a class with real signatures, a live node, the tree", async () => {
    const found = content(await plugin.tool("godot_inspect", { search: "rigid body", limit: 10 }))
    assert.match(found.godot, /^4\./)
    assert.ok(found.search.results.some((item: { class: string }) => item.class === "RigidBody3D"))
    const node3d = content(await plugin.tool("godot_inspect", { class: "Node3D", match: "look_at" }))
    assert.ok(node3d.class.methods.some((method: string) => method.startsWith("look_at(target: Vector3")))
    assert.match(node3d.class.docs, /class_node3d\.html$/)
    const box = content(await plugin.tool("godot_inspect", { node: "Box", tree: true }))
    assert.equal(box.node.class, "MeshInstance3D")
    assert.ok(box.tree.some((line: string) => line.includes("Box (MeshInstance3D)")))
    const typo = content(await plugin.tool("godot_inspect", { class: "Node3d" }))
    assert.ok(typo.class.closest.includes("Node3D"))
  })

  it("verifies the open scene, saves it, and reads the saved file in a background Godot", async () => {
    const criteria = [
      { expr: 'node("Box").position', op: "approx", value: [1, 2, 3], label: "box placed" },
      { expr: 'count("MeshInstance3D")', op: ">=", value: 1 },
    ]
    const open = content(await plugin.tool("godot_verify", { criteria }))
    assert.equal(open.passed, true, JSON.stringify(open))
    // Saving prints the editor's coloured progress: its output stays valid JSON.
    const saved = content(await plugin.tool("godot_save"))
    assert.equal(saved.path, "res://main.tscn")
    assert.ok(saved.bytes > 0)
    assert.equal(saved.unsaved, false)
    const read = content(await plugin.tool("godot_verify", { criteria, saved: true, timeout_s: 120 }))
    assert.equal(read.passed, true, JSON.stringify(read))
    assert.match(read.read.by, /file as written on disk/)
  })

  it("runs a working copy for frames to measure runtime values", async () => {
    await code('scene.get_node("Box").set_script(load("res://mover.gd"))')
    const ran = content(
      await plugin.tool("godot_verify", {
        criteria: [{ expr: 'node("Box").position.x', op: ">", value: 5 }],
        frames: 10,
        timeout_s: 120,
      }),
    )
    assert.equal(ran.passed, true, JSON.stringify(ran))
    assert.equal(ran.frames, 10)
  })

  it("runs scenes as tasks and says when the background Godot crashed", async () => {
    const run = content(await plugin.tool("godot_run", { frames: 20, wait_s: 120 }))
    assert.equal(run.state, "done", JSON.stringify(run))
    const crash = content(await plugin.tool("godot_run", { scene: "crash.tscn", frames: 20, wait_s: 120 }))
    assert.equal(crash.state, "failed")
    assert.match(crash.crash, /Program crashed/)
  })

  it("the visible dock starts its sidecar automatically", async () => {
    await code(
      'var dock = EditorInterface.get_base_control().find_child("OpenCode", true, false)\nresult.found = dock != null\ndock.reparent(EditorInterface.get_base_control())\ndock.show()',
    )
    await until(() => service.calls.some(({ method }) => method === "hosts"), 30_000, "the dock's offer lookup")
    const read = await code(
      'var dock = EditorInterface.get_base_control().find_child("OpenCode", true, false)\nresult.text = dock.transcript()\nresult.status = dock.state().status',
      { read_only: true },
    )
    assert.equal(read.result.status, "CONNECTING", read.result.text)
    assert.equal(read.result.text, "")
  })

  it("says that the editor crashed, with its log", async () => {
    assert.ok(existsSync(log))
    await assert.rejects(
      plugin.tool("godot_execute_code", { code: 'OS.crash("editor test crash")' }),
      (error: Error) => {
        assert.match(error.message, /^Godot crashed \(Godot: Trial — [^)]+\): crash report .*opencode-crash-\d+\.log/)
        return true
      },
    )
    assert.match(readFileSync(log, "utf8"), /Program crashed/)
  })
})

// The plugin declared in the project's own opencode.json only: the global configuration Location has no such RPC, so
// the editor offers itself at its project's folder.
describe("OpenCode Link offers itself at its project's folder when the plugin is loaded there only", {
  skip: !godot,
}, () => {
  it("advertises at the project's Location", async () => {
    const root = mkdtempSync(join(tmpdir(), "godot-local-"))
    const project = makeProject("Local", { "main.tscn": SCENE })
    installHost(readPackage().opencodeIntegration, project)
    const plugin = await loadPlugin({ directory: project })
    const service = await fakeService(plugin, root, (directory) => norm(directory) === norm(project))
    const editor = startEditor(godot as string, project, privateEnv(root), join(root, "editor.log"))
    try {
      await until(() => plugin.registry.hosts().offers[0], 120_000, "the editor's offer")
      const advertised = service.calls.filter((call) => call.method === "advertise")
      assert.ok(advertised.length > 0)
      assert.ok(advertised.every((call) => norm(String(call.directory)) === norm(project)))
      assert.ok(service.calls.some((call) => norm(String(call.directory)) === norm(join(root, "config", "opencode"))))
    } finally {
      editor.kill()
      await plugin.cleanup()
      await service.close()
      await new Promise((done) => setTimeout(done, 500))
      removeAll(project, root)
    }
  })
})
