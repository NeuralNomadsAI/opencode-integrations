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
// The tests of ops044_tests.py: the 0.4.3 exercises' findings (QA journal, Luna section).
const OPS044_TESTS = 12
// The tests of ops045_tests.py: the 0.4.4 exercises' findings (QA journal, Luna section).
const OPS045_TESTS = 8
// The tests of ops046_tests.py: the 0.4.5 exercises' findings (QA journal, Luna section).
const OPS046_TESTS = 3
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

  it("passes the tests of the 0.4.3 exercises' findings (0.4.4)", async () => {
    assert.equal((await hostScript("ops044_tests.py", (scratch) => [scratch], 400_000)).passed.length, OPS044_TESTS)
  })

  it("blender_inspect finds the API by words and shows elements, sockets and names beyond its limit (0.4.4)", async () => {
    const { plugin, stop, json } = await served()
    try {
      const inspect = async (args: Record<string, unknown>) => json(await plugin.tool("blender_inspect", args))
      const paths = (found: { search: { results: Array<{ path: string }> } }) => found.search.results.map((r) => r.path)
      // Luna 07 (light linking on Object) and 08 (the 5.3 compositing tree on the scene).
      assert.ok(paths(await inspect({ search: "light linking" })).includes("Object.light_linking"))
      assert.ok(paths(await inspect({ search: "compositing node group" })).includes("Scene.compositing_node_group"))
      // Luna 02: the stroke's elements, and the poll with Blender's reason.
      const stroke = (await inspect({ operator: "sculpt.brush_stroke" })).operator
      const elements = stroke.properties.find((p: { id: string }) => p.id === "stroke").element
      assert.equal(elements.type, "OperatorStrokeElement")
      assert.ok(elements.properties.some((p: { id: string }) => p.id === "x_tilt"))
      assert.equal(stroke.poll.in_this_context, false)
      assert.match(stroke.poll.blender_says, /poll\(\)/)
      // Luna 04 and 08: a node type made in the tree named, or what that tree makes instead.
      const simulation = (await inspect({ type: "GeometryNodeSimulationInput", tree: "geometry" })).type
      assert.equal(simulation.tree_type, "GeometryNodeTree")
      assert.ok(
        simulation.outputs.some((s: { name: string }) => s.name === "Delta Time"),
        JSON.stringify(simulation),
      )
      const composite = (await inspect({ type: "CompositorNodeComposite", tree: "compositing" })).type
      assert.ok(Array.isArray(composite.creatable_in_CompositorNodeTree), JSON.stringify(composite).slice(0, 800))
      // Luna 05: entries beyond the limit are named, not cut.
      const fluid = (await inspect({ type: "FluidDomainSettings", limit: 40 })).type
      assert.equal(fluid.properties.length, 40)
      assert.ok(fluid.properties_more.count > 0 && fluid.properties_more.names.length === fluid.properties_more.count)
      assert.ok((await inspect({ type: "FluidDomainSettings", match: "cache" })).type.properties.length > 0)
    } finally {
      await stop()
    }
  })

  it("blender_verify measures the agent's criteria and says why an object is not seen (0.4.4)", async () => {
    const { plugin, stop, json } = await served()
    try {
      const built = json(
        await plugin.tool("blender_execute_code", {
          code: [
            "import bpy, math",
            "sc = bpy.context.scene",
            "bpy.data.objects.remove(bpy.data.objects['Cube'])",
            "bpy.ops.object.camera_add(location=(0, -10, 1), rotation=(math.radians(90), 0, 0))",
            "sc.camera = bpy.context.object",
            "bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 1.38))",
            "t = bpy.context.object; t.name = 'Tissue'",
            "t.keyframe_insert('rotation_euler', frame=1)",
            "t.rotation_euler.z = math.radians(90); t.keyframe_insert('rotation_euler', frame=10)",
            "bpy.ops.mesh.primitive_cube_add(size=1, location=(1, 0, 1)); bpy.context.object.name = 'Hidden'",
            "bpy.context.object.hide_render = True",
            "bpy.ops.mesh.primitive_cube_add(size=1, location=(100, 0, 1)); bpy.context.object.name = 'Away'",
            "c = bpy.data.collections.new('Props'); sc.collection.children.link(c)",
            "o = bpy.data.objects.new('Crate', bpy.data.meshes['Cube']); c.objects.link(o)",
            "bpy.context.view_layer.layer_collection.children['Props'].exclude = True",
            "sc.frame_set(1)",
          ].join("\n"),
        }),
      )
      assert.equal(built.status, "executed", JSON.stringify(built).slice(0, 1500))
      const verify = async (args: Record<string, unknown>) => {
        const out = await plugin.tool("blender_verify", args)
        assert.equal(typeof out.content, "string", "no render without render or view")
        return json(out)
      }
      // Luna 06: z-min 0.88 against a "< 0.7" threshold is a failed check, measured.
      const checked = await verify({
        criteria: [
          { label: "tissue under 0.7", expr: "bounds('Tissue').min.z", op: "<", value: 0.7 },
          { label: "top above 1", path: "objects['Tissue'].location.z", op: ">", value: 1 },
          // Luna 09: the pose is measured at its frames, not assumed.
          {
            label: "turned",
            expr: "ev('Tissue').rotation_euler.z",
            op: "approx",
            value: 1.5708,
            tol: 0.001,
            frames: [1, 10],
          },
        ],
      })
      assert.equal(checked.all_passed, false)
      assert.deepEqual(checked.failed, ["tissue under 0.7", "turned"])
      const [under, top, turned] = checked.criteria
      assert.equal(under.measured, 0.88)
      assert.equal(top.passed, true)
      assert.deepEqual(
        turned.frames.map((f: { frame: number; passed: boolean }) => [f.frame, f.passed]),
        [
          [1, false],
          [10, true],
        ],
      )
      const seen = async (object: string) => (await verify({ visible: { object, frame: 1 } })).visible
      assert.equal((await seen("Tissue")).visible, true)
      assert.match((await seen("Hidden")).reasons.join(" "), /hidden in render/)
      assert.match((await seen("Away")).reasons.join(" "), /out of frame/)
      assert.match((await seen("Crate")).reasons.join(" "), /excluded from view layer/)
      await assert.rejects(
        plugin.tool("blender_verify", { criteria: [{ expr: "bpy.ops.render.render()", op: "==", value: 1 }] }),
        /not a read/,
      )
    } finally {
      await stop()
    }
  })

  it("a call after Blender closed says so, with the crash report Blender wrote (0.4.4)", async () => {
    const iso = isolatedRoot("blender-closed-")
    const stopFile = join(iso.root, "stop")
    installTestAddon(iso)
    const run = runBlender(iso, join(folder, "serve.py"), [
      "--listen-only",
      "--seconds",
      "120",
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
      await plugin.rpc("advertise", announcement({ port, token }, script_handle))
      assert.equal(typeof JSON.parse(String((await plugin.tool("blender_context")).content)).revision, "number")
      writeFileSync(stopFile, "")
      await run.exited
      await assert.rejects(plugin.tool("blender_context"), /Blender closed.*has ended, with no crash report in /)
      // Where Blender writes its own report when it crashes (an unsaved file: blender.crash.txt).
      const report = join(iso.root, "tmp", "blender.crash.txt")
      writeFileSync(report, "# Blender 5.3.0\n")
      await assert.rejects(plugin.tool("blender_context"), /Blender closed.*Crash report: .*blender\.crash\.txt/)
    } finally {
      writeFileSync(stopFile, "")
      await run.exited
      await plugin.cleanup()
      iso.cleanup()
    }
  })

  it("passes the tests of the 0.4.4 exercises' findings (0.4.5)", async () => {
    assert.equal((await hostScript("ops045_tests.py", (scratch) => [scratch], 400_000)).passed.length, OPS045_TESTS)
  })

  it("results say what the saved file lacks, blender_save what it wrote, blender_verify the stale caches (0.4.5)", async () => {
    const { iso, plugin, stop, json } = await served()
    try {
      const code = async (text: string) => {
        const out = json(await plugin.tool("blender_execute_code", { code: text }))
        assert.equal(out.status, "executed", JSON.stringify(out).slice(0, 1200))
        return out
      }
      const built = await code(
        "import bpy\nbpy.ops.mesh.primitive_cube_add(location=(0, 0, 3))\nbpy.context.object.name = 'Brick'\n" +
          "bpy.ops.rigidbody.object_add()",
      )
      assert.equal(built.unsaved.never_saved, true)
      const file = join(iso.root, "ex06.blend")
      const saved = json(await plugin.tool("blender_save", { path: file }))
      assert.ok(saved.wrote_steps.includes("OpenCode: code"), JSON.stringify(saved))
      assert.equal(saved.unsaved, undefined)
      // Luna 06: the cache is checked at the frames verified; never baked here.
      const checks = { criteria: [{ expr: "world('Brick').z", op: "<", value: 3, frame: 40 }] }
      const fresh = json(await plugin.tool("blender_verify", checks))
      assert.equal(fresh.unsaved, undefined)
      assert.deepEqual(
        fresh.caches.map((c: { cache: string; baked: boolean }) => [c.cache, c.baked]),
        [["rigid body world", false]],
      )
      // Baked, then a change: the bake is outdated, and the file lacks the change.
      await code(
        "import bpy\npc = bpy.context.scene.rigidbody_world.point_cache\npc.frame_end = 60\n" +
          "with bpy.context.temp_override(scene=bpy.context.scene, point_cache=pc):\n    bpy.ops.ptcache.bake(bake=True)",
      )
      json(await plugin.tool("blender_save", {}))
      const moved = await code("import bpy\nbpy.data.objects['Brick'].location.x = 2")
      assert.deepEqual(moved.unsaved, { file: "ex06.blend", opencode_steps: ["OpenCode: code"] })
      assert.equal(json(await plugin.tool("blender_context")).unsaved.file, "ex06.blend")
      const stale = json(
        await plugin.tool("blender_verify", { criteria: [{ ...checks.criteria[0], frames: [40, 90] }] }),
      )
      assert.equal(stale.unsaved.file, "ex06.blend")
      assert.equal(stale.caches[0].outdated, true, JSON.stringify(stale.caches))
      assert.deepEqual(stale.caches[0].checked_frames_outside, [90])
      // A copy leaves the open file as it was.
      const copy = json(await plugin.tool("blender_save", { path: join(iso.root, "copy.blend"), copy: true }))
      assert.equal(copy.unsaved.file, "ex06.blend", JSON.stringify(copy))
      assert.equal(copy.wrote_steps, undefined)
      // A save with nothing pending from OpenCode says so (the trial's second save of the library).
      assert.deepEqual(json(await plugin.tool("blender_save", {})).wrote_steps, ["OpenCode: code"])
      assert.deepEqual(json(await plugin.tool("blender_save", {})).wrote_steps, [])
    } finally {
      await stop()
    }
  })

  it("passes the tests of the 0.4.5 exercises' findings (0.4.6)", async () => {
    assert.equal((await hostScript("ops046_tests.py", (scratch) => [scratch], 400_000)).passed.length, OPS046_TESTS)
  })

  it("blender_inspect says what lets a refused operator run; blender_verify reads the saved file (0.4.6)", async () => {
    const { iso, plugin, stop, json } = await served()
    try {
      const code = async (text: string) => {
        const out = json(await plugin.tool("blender_execute_code", { code: text }))
        assert.equal(out.status, "executed", JSON.stringify(out).slice(0, 1200))
        return out
      }
      await code(
        "import bpy\nbpy.ops.mesh.primitive_plane_add()\nbpy.context.object.name = 'Sheet'\n" +
          "bpy.context.object.modifiers.new('Cloth', 'CLOTH')",
      )
      const poll = json(await plugin.tool("blender_inspect", { operator: "ptcache.bake" })).operator.poll
      assert.equal(poll.in_this_context, false)
      assert.ok(poll.same_group_passing.includes("ptcache.bake_all"), JSON.stringify(poll))
      assert.deepEqual(poll.passes_with[0], {
        temp_override: { point_cache: "bpy.context.active_object.modifiers['Cloth'].point_cache" },
      })
      // ex09: the open document moved the sheet after the save; the file still has it where it was.
      const file = join(iso.root, "shot.blend")
      await code(
        `import bpy\nbpy.data.objects['Sheet'].location.z = 1\nbpy.ops.wm.save_as_mainfile(filepath=${JSON.stringify(file)})`,
      )
      await code("import bpy\nbpy.data.objects['Sheet'].location.z = 5")
      const criteria = [{ label: "sheet height", expr: "world('Sheet').z", op: "==", value: 5 }]
      const live = json(await plugin.tool("blender_verify", { criteria }))
      assert.equal(live.criteria[0].measured, 5)
      assert.equal(live.read, undefined)
      const saved = json(await plugin.tool("blender_verify", { criteria, saved: true }))
      assert.equal(saved.criteria[0].measured, 1, JSON.stringify(saved).slice(0, 1500))
      assert.equal(saved.criteria[0].passed, false)
      assert.equal(saved.read.file, file)
      assert.equal(saved.unsaved.file, "shot.blend", "and the open document still differs from it")
      await assert.rejects(plugin.tool("blender_verify", { criteria, saved: join(iso.root, "none.blend") }), /no file/)
    } finally {
      await stop()
    }
  })

  it("blender_verify states the geometry health of what is in view and the black areas of its render (0.4.6)", async () => {
    const { plugin, stop, json } = await served()
    try {
      const built = json(
        await plugin.tool("blender_execute_code", {
          code: [
            "import bpy, math",
            "sc = bpy.context.scene",
            "bpy.data.objects.remove(bpy.data.objects['Cube'])",
            "sc.render.engine = 'BLENDER_WORKBENCH'",
            "sc.display.shading.light = 'FLAT'",
            "sc.display.shading.color_type = 'MATERIAL'",
            "cam = bpy.data.objects['Camera']",
            "cam.location, cam.rotation_euler = (0, -10, 1), (math.radians(90), 0, 0)",
            "def box(name, loc, scale):",
            "    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)",
            "    o = bpy.context.object; o.name = name; o.scale = scale",
            "    bpy.ops.object.transform_apply(scale=True)",
            "    return o",
            // the archviz case: two wall pieces overlapping, their room-side faces on one plane
            "box('Wall A', (2, 2, 1), (2, 0.2, 1)); box('Wall B', (2.5, 2, 1), (2, 0.2, 1))",
            "box('Inside out', (-2.5, 2, 1), (1, 1, 1)).data.flip_normals()",
            "bpy.ops.mesh.primitive_uv_sphere_add(location=(-2.5, 2, 2.6), radius=0.5); s = bpy.context.object; s.name = 'Holed'",
            "import bmesh; bm = bmesh.new(); bm.from_mesh(s.data); bm.faces.ensure_lookup_table(); bmesh.ops.delete(bm, geom=[bm.faces[0]], context='FACES_ONLY'); bm.to_mesh(s.data); bm.free()",
            "me = bpy.data.meshes.new('Sliver'); me.from_pydata([(0, 2, 0), (0.5, 2, 0), (1, 2, 0)], [], [(0, 1, 2)])",
            "sc.collection.objects.link(bpy.data.objects.new('Sliver', me))",
            "bpy.ops.mesh.primitive_plane_add(size=2, location=(0, 1, 1), rotation=(math.radians(90), 0, 0)); b = bpy.context.object; b.name = 'Board'",
            "m = bpy.data.materials.new('Black'); m.diffuse_color = (0, 0, 0, 1); b.data.materials.append(m)",
          ].join("\n"),
        }),
      )
      assert.equal(built.status, "executed", JSON.stringify(built).slice(0, 1500))
      const out = await plugin.tool("blender_verify", { render: true, size: 320 })
      const checked = JSON.parse(String((out.content as ReadonlyArray<{ text?: string }>)[0]?.text))
      const geometry = checked.geometry
      assert.deepEqual(geometry.coplanar_overlaps[0].objects, ["Wall A", "Wall B"], JSON.stringify(geometry))
      assert.ok(geometry.coplanar_overlaps[0].area_m2 > 1.4)
      assert.deepEqual(geometry.inward_normals, ["Inside out"])
      assert.equal(geometry.open_edges.Holed > 0, true)
      assert.equal(geometry.zero_area_faces.Sliver, 1)
      const regions = checked.facts.image.black_regions
      assert.equal(regions[0].object, "Board", JSON.stringify(regions))
    } finally {
      await stop()
    }
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
