// The add-on in a background Blender >= 5.3 (its own tests: host methods and Lab tools, the typed edit layer,
// the operations of 0.4.0 and the defects fixed in it), and the plugin's tools acting on that Blender's
// document. Skipped unless BLENDER_TEST_BIN names Blender.

import assert from "node:assert/strict"
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { describe, it } from "node:test"

import { requestHost } from "../plugin/src/kit.ts"
import { blenderBin, installAddon, installTestAddon, isolatedRoot, runBlender } from "./helpers/blender.ts"
import { announcement, loadPlugin } from "./helpers/plugin.ts"

const skip = blenderBin ? false : "BLENDER_TEST_BIN is not set"
// The tests of ops042_tests.py: one or more per friction of the journal's entries 47 to 62.
const OPS042_TESTS = 10
// The tests of ops043_tests.py: the blockers of the 0.4.2 exercises (QA journal, Luna section).
const OPS043_TESTS = 3
const folder = join(import.meta.dirname, "blender")

type Result = { passed: string[]; failed: Array<{ name: string; error: string; trace: string }> }

// Runs one of the add-on's test scripts in a private background Blender; returns its RESULT line.
async function hostScript<T extends Result>(script: string, args: (scratch: string) => string[], timeout = 240_000) {
  const iso = isolatedRoot("blender-host-")
  try {
    installTestAddon(iso)
    const scratch = join(iso.root, "scratch")
    mkdirSync(scratch)
    const run = runBlender(iso, join(folder, script), args(scratch), {}, { timeout })
    await run.exited
    const lines = run.output().split("\n")
    for (const line of lines.filter((entry) => entry.startsWith("MEASURE "))) console.log(line)
    const line = lines.find((entry) => entry.startsWith("RESULT "))
    assert.ok(line, run.output().slice(-3000))
    const result = JSON.parse(line.slice("RESULT ".length)) as T
    assert.deepEqual(result.failed, [], JSON.stringify(result.failed, null, 1))
    return result
  } finally {
    iso.cleanup()
  }
}

// One second of a 440 Hz tone, 16-bit mono WAV.
function toneWav() {
  const rate = 22_050
  const samples = Buffer.alloc(rate * 2)
  for (let i = 0; i < rate; i += 1)
    samples.writeInt16LE(Math.round(Math.sin((2 * Math.PI * 440 * i) / rate) * 8000), i * 2)
  const header = Buffer.alloc(44)
  header.write("RIFF", 0)
  header.writeUInt32LE(36 + samples.length, 4)
  header.write("WAVEfmt ", 8)
  header.writeUInt32LE(16, 16)
  header.writeUInt16LE(1, 20)
  header.writeUInt16LE(1, 22)
  header.writeUInt32LE(rate, 24)
  header.writeUInt32LE(rate * 2, 28)
  header.writeUInt16LE(2, 32)
  header.writeUInt16LE(16, 34)
  header.write("data", 36)
  header.writeUInt32LE(samples.length, 40)
  return Buffer.concat([header, samples])
}

// The add-on of these sources served by a private background Blender, and a plugin attached to it.
async function served(seconds = 240) {
  const iso = isolatedRoot("blender-tools-")
  const stopFile = join(iso.root, "stop")
  installTestAddon(iso)
  const run = runBlender(iso, join(folder, "serve.py"), [
    "--listen-only",
    "--seconds",
    String(seconds),
    "--stop-file",
    stopFile,
  ])
  const plugin = await loadPlugin()
  const stop = async () => {
    writeFileSync(stopFile, "")
    await run.exited
    await plugin.cleanup()
    iso.cleanup()
  }
  try {
    let line: string | undefined
    for (let attempt = 0; attempt < 600 && !line; attempt += 1) {
      line = run
        .output()
        .split("\n")
        .find((entry) => entry.startsWith("ENDPOINT "))
      if (!line) await new Promise((resolve) => setTimeout(resolve, 100))
    }
    assert.ok(line, run.output().slice(-2000))
    const { port, token, script_handle } = JSON.parse(line.slice("ENDPOINT ".length))
    assert.deepEqual(await plugin.rpc("advertise", announcement({ port, token }, script_handle)), {
      outcome: "accepted",
    })
  } catch (error) {
    await stop()
    throw error
  }
  const json = (result: { content?: unknown }) => JSON.parse(String(result.content))
  return { iso, plugin, stop, json }
}

describe("OpenCode Link in Blender (background)", { skip }, () => {
  it("passes the host tests and runs every bundled Lab tool", async () => {
    // Its renders and imports take minutes on a runner without a GPU.
    const result = await hostScript<Result & { coverage: Record<string, string>; lab: Record<string, string> }>(
      "host_tests.py",
      (scratch) => [scratch],
      600_000,
    )
    assert.equal(result.passed.length, 26)
    assert.equal(Object.keys(result.coverage).length, 87)
    // Every bundled Lab tool ran by name; those needing a window refused background mode cleanly.
    const labTools = JSON.parse(
      readFileSync(join(import.meta.dirname, "..", "host", "opencode_link", "lab", "tools.json"), "utf8"),
    ).tools as Array<{ name: string; interface: boolean }>
    assert.deepEqual(Object.keys(result.lab).sort(), labTools.map((tool) => tool.name).sort())
    for (const tool of labTools)
      assert.equal(result.lab[tool.name], tool.interface ? "refused in background (needs a window)" : "ran")
  })

  it("passes the typed layer's tests: one undo step, before and after, a failure restored field by field", async () => {
    assert.equal((await hostScript("thin_tests.py", () => [folder])).passed.length, 10)
  })

  it("passes the tests of the 0.4.0 operations and of the 0.3.9 fixes", async () => {
    const ops = join(import.meta.dirname, "..", "host", "ops.json")
    assert.equal((await hostScript("ops040_tests.py", (scratch) => [scratch, ops])).passed.length, 9)
    assert.equal((await hostScript("defects_tests.py", (scratch) => [scratch])).passed.length, 6)
  })

  it("passes the tests of the 0.4.2 exercises' blockers (0.4.3)", async () => {
    assert.equal((await hostScript("ops043_tests.py", (scratch) => [scratch], 400_000)).passed.length, OPS043_TESTS)
  })

  it("passes the tests of the western film's v7 frictions (0.4.2)", async () => {
    assert.equal((await hostScript("ops042_tests.py", (scratch) => [scratch], 400_000)).passed.length, OPS042_TESTS)
  })

  it("blender_verify renders a free viewpoint over several frames as one sheet (journal entries 47, 51)", async () => {
    const { plugin, stop, json } = await served()
    try {
      // Workbench renders on runners without a GPU.
      json(await plugin.tool("blender_execute_code", { code: "bpy.context.scene.render.engine = 'BLENDER_WORKBENCH'" }))
      const scene = json(await plugin.tool("blender_context"))
      // The film's set-up: a camera bound to a marker, an animated holster nobody frames.
      json(
        await plugin.tool("blender_apply", {
          operations: [
            { op: "create_object", kind: "camera", name: "Cam_Wide", as: "cam", location: [0, -30, 2] },
            { op: "add_marker", name: "Wide", frame: 1, camera: "$cam" },
            { op: "set_camera", ref: "$cam" },
            { op: "create_object", kind: "mesh", primitive: "cube", name: "Holster", as: "h", size: 0.3 },
            { op: "insert_key", ref: "$h", paths: ["location"], frame: 1 },
            { op: "create_object", kind: "light", light_type: "SUN", name: "Sun", energy: 3 },
          ],
        }),
      )
      json(
        await plugin.tool("blender_apply", {
          operations: [
            { op: "set_transform", ref: "Holster", location: [0, 0, 1] },
            { op: "insert_key", ref: "Holster", paths: ["location"], frame: 20 },
            // Evaluated from its keys: nothing unkeyed for a frame change to overwrite.
            { op: "scene_settings", frame_current: 1 },
          ],
        }),
      )
      const before = json(await plugin.tool("blender_context")).revision
      assert.ok(before > scene.revision)
      const result = (
        await plugin.tool("blender_verify", {
          view: { location: [1.5, -1.5, 1.2], target: "Holster", lens: 35 },
          frames: [1, 10, 20],
          size: 160,
        })
      ).content as ReadonlyArray<{ type: string; text?: string; uri?: string }>
      const facts = JSON.parse(String(result[0]?.text)).facts
      assert.equal(facts.frames.length, 3)
      assert.deepEqual(
        facts.frames.map((frame: { frame: number }) => frame.frame),
        [1, 10, 20],
      )
      assert.match(facts.frames[0].camera_used.via, /^view: from/)
      assert.ok(
        facts.frames.every((frame: { image: { black: boolean } }) => !frame.image.black),
        JSON.stringify(facts),
      )
      assert.deepEqual([facts.sheet.columns, facts.sheet.rows], [2, 2])
      assert.match(String(result[1]?.uri), /^data:image\/png;base64,iVBOR/)
      const after = json(await plugin.tool("blender_context"))
      assert.equal(after.revision, before, "verify leaves the revision")
      assert.equal(after.scene.camera, "Cam_Wide")
      assert.deepEqual(after.markers, [{ name: "Wide", frame: 1, camera: "Cam_Wide" }])
      assert.ok(!after.objects.some((object: { name: string }) => object.name.startsWith("OpenCode verify")))
      // The usual output still works, and if_revision from before the verifies still applies.
      const plain = (await plugin.tool("blender_verify", { frame: 1, size: 96 })).content as ReadonlyArray<{
        text?: string
      }>
      assert.equal(JSON.parse(String(plain[0]?.text)).facts.camera_used.camera, "Cam_Wide")
      const write = json(
        await plugin.tool("blender_apply", {
          if_revision: before,
          operations: [{ op: "rename", ref: "Sun", name: "Key light" }],
        }),
      )
      assert.equal(write.status, "applied", JSON.stringify(write).slice(0, 500))
    } finally {
      await stop()
    }
  })

  it("blender_execute_code runs a script file as written (journal entry 56)", async () => {
    const { iso, plugin, stop, json } = await served()
    try {
      // What Code Mode mangled: a backslash path and a newline escape inside Python strings.
      const script = join(iso.root, "fall.py")
      writeFileSync(
        script,
        [
          "import bpy",
          'path = "C:\\\\film\\\\sheet.png"',
          'lines = "head\\nfoot"',
          "result = {'path': path, 'chars': len(path), 'lines': lines.split('\\n'), 'file': __file__}",
          "",
        ].join("\n"),
      )
      const out = json(await plugin.tool("blender_execute_code", { path: script, read_only: true }))
      assert.equal(out.status, "executed", JSON.stringify(out))
      const result = JSON.parse(JSON.stringify(out.result))
      assert.equal(result.path, "C:\\film\\sheet.png")
      assert.equal(result.chars, 17)
      assert.deepEqual(result.lines, ["head", "foot"])
      assert.equal(result.file, script)
      const broken = join(iso.root, "broken.py")
      writeFileSync(broken, "x = 1\ny = 2\nraise ValueError('at the end')\n")
      const failed = json(await plugin.tool("blender_execute_code", { path: broken }))
      assert.equal(failed.status, "failed")
      assert.ok(failed.traceback.includes("broken.py") && failed.traceback.includes("line 3"), failed.traceback)
      assert.match(failed.traceback, /raise ValueError\('at the end'\)/)
    } finally {
      await stop()
    }
  })

  it("blender_import brings a model file in through the tool (journal entry 58)", async () => {
    const { iso, plugin, stop, json } = await served()
    try {
      const file = join(iso.root, "colt.glb")
      // A model on disk: a barrel exported as glTF, then removed from the scene.
      const exported = json(
        await plugin.tool("blender_execute_code", {
          code: [
            "import bpy",
            "bpy.ops.mesh.primitive_cylinder_add(radius=0.02, depth=0.2)",
            "bpy.context.object.name = 'Colt barrel'",
            `bpy.ops.export_scene.gltf(filepath=${JSON.stringify(file)}, use_selection=True)`,
            "bpy.data.objects.remove(bpy.data.objects['Colt barrel'])",
          ].join("\n"),
        }),
      )
      assert.equal(exported.status, "executed", JSON.stringify(exported).slice(0, 1500))
      assert.ok(existsSync(file))
      const out = json(await plugin.tool("blender_import", { source: "file", path: file, wait_s: 120 }))
      const task = out.task ?? out
      assert.equal(task.state, "done", JSON.stringify(out).slice(0, 1500))
      assert.ok(JSON.stringify(task.created).includes("Colt barrel"), JSON.stringify(task.created))
      const scene = json(await plugin.tool("blender_context", { match: "Colt*" }))
      assert.deepEqual(
        scene.objects.map((object: { name: string }) => object.name),
        ["Colt barrel"],
      )
      assert.deepEqual(
        plugin.permissions.map((request) => request.action),
        ["blender_execute_code", "blender_import"],
      )
    } finally {
      await stop()
    }
  })

  it("blender_verify says which scene it read, and finds the shot's camera under a text and a sound (Luna 08)", async () => {
    const { iso, plugin, stop, json } = await served()
    try {
      writeFileSync(join(iso.root, "tone.wav"), toneWav())
      // The exercise's layout: the active scene is a shot; the montage is another scene's sequencer.
      const built = json(
        await plugin.tool("blender_execute_code", {
          code: [
            "import bpy",
            "shot = bpy.context.scene",
            "shot.render.engine = 'BLENDER_WORKBENCH'",
            "cam = bpy.data.objects.new('Shot Camera', bpy.data.cameras.new('Shot Camera'))",
            "shot.collection.objects.link(cam)",
            "shot.camera = cam",
            "edit = bpy.data.scenes.new('Edit')",
            "edit.render.engine = 'BLENDER_WORKBENCH'",
            "ed = edit.sequence_editor_create()",
            "ed.strips.new_scene(name='Shot', scene=shot, channel=1, frame_start=1)",
            "title = ed.strips.new_effect(name='TITLE', type='TEXT', channel=2, frame_start=1, length=24)",
            "title.text = 'TITLE'",
            `ed.strips.new_sound(name='Tone', filepath=${JSON.stringify(join(iso.root, "tone.wav"))}, channel=3, frame_start=1)`,
          ].join("\n"),
        }),
      )
      assert.equal(built.status, "executed", JSON.stringify(built).slice(0, 1500))
      const facts = async (args: Record<string, unknown>) =>
        JSON.parse(
          String(
            (
              (await plugin.tool("blender_verify", { frame: 5, size: 96, ...args })).content as ReadonlyArray<{
                text?: string
              }>
            )[0]?.text,
          ),
        ).facts
      const active = await facts({})
      assert.equal(active.scene, "Scene")
      assert.match(active.scene_read, /active scene/)
      assert.deepEqual(active.scenes, ["Edit", "Scene"])
      assert.ok(
        active.warnings?.some((warning: string) => /shown by the montage of 'Edit'/.test(warning)),
        JSON.stringify(active.warnings),
      )
      const montage = await facts({ scene: "Edit" })
      assert.equal(montage.scene, "Edit")
      assert.match(montage.scene_read, /given/)
      assert.deepEqual(montage.camera_used, {
        camera: "Shot Camera",
        via: "strip 'Shot' (scene 'Scene') under text 'TITLE'",
      })
      assert.equal(montage.sequencer.used, true)
    } finally {
      await stop()
    }
  })

  it("blender_render encodes a montage's sound and reports the streams it wrote (Luna 08)", async () => {
    const { iso, plugin, stop, json } = await served()
    try {
      writeFileSync(join(iso.root, "tone.wav"), toneWav())
      const montage = join(iso.root, "montage.blend")
      const built = json(
        await plugin.tool("blender_execute_code", {
          code: [
            "import bpy",
            "sc = bpy.context.scene",
            "sc.render.engine = 'BLENDER_WORKBENCH'",
            "sc.render.resolution_x, sc.render.resolution_y, sc.render.fps = 160, 90, 24",
            "sc.frame_start, sc.frame_end = 1, 24",
            "ed = sc.sequence_editor_create()",
            "ed.strips.new_effect(name='Card', type='COLOR', channel=1, frame_start=1, length=24)",
            `ed.strips.new_sound(name='Tone', filepath=${JSON.stringify(join(iso.root, "tone.wav"))}, channel=2, frame_start=1)`,
            `bpy.ops.wm.save_as_mainfile(filepath=${JSON.stringify(montage)})`,
          ].join("\n"),
        }),
      )
      assert.equal(built.status, "executed", JSON.stringify(built).slice(0, 1500))
      const render = async (output: string) => {
        const started = json(await plugin.tool("blender_render", { output, unsaved: "save" }))
        return json(await plugin.tool("blender_render", { task: started.task, wait_s: 240 }))
      }
      const withSound = await render(join(iso.root, "montage.mp4"))
      assert.equal(withSound.state, "done", JSON.stringify(withSound).slice(0, 2000))
      assert.deepEqual(withSound.result.audio.sources, ["strip 'Tone'"])
      assert.equal(withSound.result.streams.video.frames, 24)
      assert.ok(withSound.result.streams.audio?.channels >= 1, JSON.stringify(withSound.result.streams))
      // Muted, nothing is audible: no audio stream, and it is said.
      json(
        await plugin.tool("blender_execute_code", {
          code: "import bpy\nbpy.context.scene.sequence_editor.strips['Tone'].mute = True",
        }),
      )
      const silent = await render(join(iso.root, "silent.mp4"))
      assert.equal(silent.state, "done", JSON.stringify(silent).slice(0, 2000))
      assert.equal(silent.result.streams.audio, null)
      assert.match(silent.result.audio.none, /nothing audible/)
    } finally {
      await stop()
    }
  })

  it("the plugin's tools act on the attached Blender document", async () => {
    const iso = isolatedRoot("blender-tools-")
    const stopFile = join(iso.root, "stop")
    installAddon(iso)
    const run = runBlender(iso, join(folder, "serve.py"), [
      "--listen-only",
      "--seconds",
      "180",
      "--stop-file",
      stopFile,
    ])
    const plugin = await loadPlugin()
    try {
      let line: string | undefined
      for (let attempt = 0; attempt < 600 && !line; attempt += 1) {
        line = run
          .output()
          .split("\n")
          .find((entry) => entry.startsWith("ENDPOINT "))
        if (!line) await new Promise((resolve) => setTimeout(resolve, 100))
      }
      assert.ok(line, run.output().slice(-2000))
      const { port, token, script_handle } = JSON.parse(line.slice("ENDPOINT ".length))
      const hello = (await requestHost({ host: "127.0.0.1", port, token }, "hello", {})) as {
        capabilities: { methods: string[] }
      }
      for (const method of ["scene", "apply", "execute_code", "lab_tool", "tasks"])
        assert.ok(hello.capabilities.methods.includes(method), `hello lists ${method}`)
      assert.deepEqual(await plugin.rpc("advertise", announcement({ port, token }, script_handle)), {
        outcome: "accepted",
      })
      const json = (result: { content?: unknown }) => JSON.parse(String(result.content))
      // The only offered Blender attaches itself at the first call.
      const scene = json(await plugin.tool("blender_context"))
      assert.equal(typeof scene.revision, "number")
      assert.ok(plugin.registry.binding("ses_test"))
      const applied = json(
        await plugin.tool("blender_apply", {
          if_revision: scene.revision,
          command_id: "e2e-1",
          summary: "street",
          operations: [
            { op: "create_object", kind: "empty", name: "Lamp", as: "lamp", location: [-6, -4, 0] },
            {
              op: "create_object",
              kind: "mesh",
              primitive: "cylinder",
              name: "Pole",
              as: "pole",
              parent: "$lamp",
              location: [-6, -4, 1.5],
            },
            { op: "set_material", ref: "$pole", material: "Metal", color: [0.6, 0.6, 0.62], metallic: 1 },
            { op: "duplicate", ref: "$lamp", suffix: "_2", offset: [4, 0, 0], linked: true },
            { op: "create_object", kind: "text", name: "Title", body: "HIGH NOON", size: 1.5, extrude: 0.1 },
            { op: "scene_settings", frame_start: 1, frame_end: 60, fps: 24 },
          ],
        }),
      )
      assert.equal(applied.status, "applied", JSON.stringify(applied).slice(0, 2000))
      assert.deepEqual(
        applied.created.map((entry: { name: string }) => entry.name),
        ["Lamp", "Pole", "Metal", "Lamp_2", "Pole_2", "Title"],
      )
      const after = json(await plugin.tool("blender_context", { match: "Pole*" }))
      assert.equal(after.scene.frames[1], 60)
      assert.deepEqual(
        after.objects.map((object: { name: string }) => object.name),
        ["Pole", "Pole_2"],
      )
      const inspected = json(await plugin.tool("blender_inspect", { type: "TextCurve", match: "align*" }))
      assert.match(JSON.stringify(inspected), /align_x/)
      const saved = join(iso.root, "street.blend")
      json(await plugin.tool("blender_save", { path: saved, copy: true }))
      assert.ok(existsSync(saved))
      assert.deepEqual(plugin.permissions, [], "attaching the only Blender and the fixed programs ask nothing")
    } finally {
      writeFileSync(stopFile, "")
      await run.exited
      await plugin.cleanup()
      iso.cleanup()
    }
  })
})
