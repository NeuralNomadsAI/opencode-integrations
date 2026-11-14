"""The 0.4.3 exercises' findings (QA journal, Luna section): errors that say what is valid, operator calls that
close Blender 5.3 from a script, the crash report's folder in hello. Each fails on the 0.4.3 add-on and passes on
0.4.4, in a private background Blender 5.3 (run by blender.test.ts). Prints RESULT <json>.
Usage: blender -b --factory-startup --python ops044_tests.py -- <scratch folder>
"""

import importlib
import json
import os
import sys
import traceback

import addon_utils
import bpy

MODULE = "bl_ext.user_default.opencode_link"
addon_utils.enable(MODULE, default_set=True, handle_error=None)
addon = importlib.import_module(MODULE + ".addon")
session = addon.session()
core, app = session.core, session.app
session.stop()
SCRATCH = sys.argv[sys.argv.index("--") + 1]

results = {"passed": [], "failed": [], "host_version": core.version}
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def empty():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    core.quarantined = False
    core.quarantine = None
    bpy.context.view_layer.update()
    addon.pump(0.05)


def run_code(text, read_only=False):
    params = {"script_handle": app.script_handle(), "code": text}
    if read_only:
        params["read_only"] = True
    return core.handle("execute_code", params)


def hint_of(text):
    out = run_code(text)
    check(out["status"] == "failed", "the code fails: %s" % out)
    check("hint" in out, "the failure carries a hint: %s" % json.dumps(out)[:600])
    return out, out["hint"]


# -- (4) first, while this background Blender has pushed no undo step: no undo stack yet --------------------------
@test
def c_a_sculpt_operator_without_interface_or_undo_step_no_longer_closes_blender():
    # read_only: no barrier step either. On 0.4.3 the sculpt undo step of the fill closes Blender here.
    out = run_code("import bpy\nbpy.ops.mesh.primitive_uv_sphere_add()\nbpy.ops.object.mode_set(mode='SCULPT')\n"
                   "bpy.ops.paint.mask_flood_fill(mode='VALUE', value=0.7)\nbpy.ops.object.mode_set(mode='OBJECT')\n"
                   "result = {'mode': bpy.context.mode}", read_only=True)
    check(out["status"] == "executed" and out["result"] == {"mode": "OBJECT"}, "runs: %s" % out.get("message"))
    check(any("first undo step" in w for w in out.get("warnings", [])), "and says why: %s" % out.get("warnings"))


# -- (1) errors that say what is valid, read from this Blender ---------------------------------------------------
@test
def e02_an_unknown_property_names_the_closest_ones_of_its_type():
    empty()
    out, hint = hint_of("import bpy\nbpy.context.scene.tool_settings.sculpt.detail_type = 'RELATIVE'")
    check(hint["type"] == "Sculpt" and hint["missing"] == "detail_type", "the type and the name: %s" % hint)
    closest = [c["id"] for c in hint["closest"]]
    check("detail_type_method" in closest, "the real property is proposed: %s" % closest)
    method = next(c for c in hint["closest"] if c["id"] == "detail_type_method")
    check(method["type"] == "ENUM" and len(method["enum"]) >= 2, "with its enumeration: %s" % method)
    check("detail_size" in hint["properties"]["all"], "the type's properties are listed")


@test
def e_an_operator_argument_gets_the_operators_real_signature():
    empty()
    out, hint = hint_of("import bpy\nbpy.ops.mesh.primitive_uv_sphere_add(segmentz=8)")
    check(hint["operator"] == "mesh.primitive_uv_sphere_add" and hint["unknown"] == "segmentz", "named: %s" % hint)
    check(hint["closest"][0] == "segments", "closest: %s" % hint["closest"])
    check(any(p["id"] == "ring_count" for p in hint["signature"]), "the signature lists every parameter")


@test
def e_a_collection_element_names_every_property_it_needs():
    empty()
    out, hint = hint_of("import bpy\nbpy.ops.wm.append(directory='/nowhere/', files=[{'nam': 'x'}])")
    files = next(p for p in hint["signature"] if p["id"] == "files")
    check(files["element"]["type"] == "OperatorFileListElement", "the element type: %s" % files)
    check([e["id"] for e in files["element"]["properties"]] == ["name"], "its properties: %s" % files["element"])
    check(hint.get("missing") == "name", "the missing key: %s" % hint)


@test
def e_an_invalid_enumeration_value_gets_the_closest_items():
    empty()
    out, hint = hint_of("import bpy\nbpy.ops.mesh.primitive_cube_add()\nbpy.ops.object.mode_set(mode='SCULPTING')")
    check(hint["invalid"] == "SCULPTING" and hint["closest"][0] == "SCULPT", "closest: %s" % hint)
    out, hint = hint_of("import bpy\nbpy.ops.mesh.primitive_cube_add()\nbpy.context.object.display_type = 'WIREFRAME'")
    check("WIRE" in hint["closest"], "a property's enumeration too: %s" % hint)


@test
def e04_a_missing_socket_lists_the_nodes_real_sockets():
    empty()
    out, hint = hint_of("import bpy\nm = bpy.data.materials.new('M')\nm.use_nodes = True\n"
                        "node = m.node_tree.nodes['Principled BSDF']\nnode.inputs['Base Colour'].default_value = (1, 0, 0, 1)")
    check(hint["missing"] == "Base Colour" and hint["closest"][0] == "Base Color", "closest socket: %s" % hint)
    check("node.inputs" == hint["collection"] and "Roughness" in hint["keys"]["all"], "the collection read: %s" % hint)
    check(not bpy.data.materials, "the failed run is undone after the hint was read")


@test
def e08_an_unknown_node_type_lists_what_the_tree_creates():
    empty()
    out, hint = hint_of("import bpy\ng = bpy.data.node_groups.new('C', 'CompositorNodeTree')\n"
                        "g.nodes.new('CompositorNodeComposite')")
    check(hint["missing"] == "CompositorNodeComposite" and hint["tree_type"] == "CompositorNodeTree", "named: %s" % hint)
    check(isinstance(hint["closest_creatable"], list), "creatable node types listed: %s" % hint)
    out, hint = hint_of("import bpy\nm = bpy.data.materials.new('M')\nm.use_nodes = True\n"
                        "m.node_tree.nodes.new('ShaderNodeBsdfPrincipalled')")
    check(hint["closest_creatable"][0]["type"] == "ShaderNodeBsdfPrincipled", "closest: %s" % hint["closest_creatable"][:2])
    check("Base Color" in hint["closest_creatable"][0]["inputs"], "with its sockets")


@test
def e_an_unknown_operator_names_the_closest_ones():
    empty()
    out, hint = hint_of("import bpy\nbpy.ops.object.mode_sett(mode='OBJECT')")
    check("object.mode_set" in hint["closest"], "closest: %s" % hint)


@test
def e09_a_failed_poll_shows_blenders_reason_and_the_context_it_had():
    empty()
    out, hint = hint_of("import bpy\nbpy.ops.ed.lib_id_generate_preview()")
    check(hint["operator"] == "ed.lib_id_generate_preview" and hint["context"]["id"] is None, "context: %s" % hint)
    check("temp_override" in hint["give_context"], "how to give it: %s" % hint)


@test
def e_watched_operators_behave_as_blenders_own():
    empty()
    out = run_code("import bpy\nop = bpy.ops.mesh.primitive_cube_add\n"
                   "result = {'poll': op.poll(), 'rna': op.get_rna_type().identifier, 'idname': op.idname(),"
                   " 'run': sorted(op(size=2)), 'repr': repr(op)[:28]}")
    check(out["status"] == "executed", "runs: %s" % out)
    check(out["result"] == {"poll": True, "rna": "MESH_OT_primitive_cube_add", "idname": "MESH_OT_primitive_cube_add",
                            "run": ["FINISHED"], "repr": "bpy.ops.mesh.primitive_cube_"}, "same results: %s" % out["result"])
    check(type(bpy.ops.mesh.primitive_cube_add).__name__ == "BPyOpFunction", "bpy.ops restored after the run")


# -- (4) calls that close Blender 5.3 from a script ---------------------------------------------------------------
@test
def c02_a_scripted_mask_stroke_on_multires_gets_its_mask_layer_first():
    """The decision of the guard, its window-only part replayed with a recorder (strokes need a window)."""
    empty()
    watch_module = importlib.import_module(MODULE + ".op_watch")
    bpy.ops.mesh.primitive_uv_sphere_add()
    obj = bpy.context.object
    obj.modifiers.new("Multires", "MULTIRES")
    bpy.ops.object.multires_subdivide(modifier="Multires", mode="CATMULL_CLARK")
    bpy.ops.object.mode_set(mode="SCULPT")
    calls = []

    class Windowed(object):
        background = False

    real = watch_module.bpy
    proxy = type("Bpy", (), {"app": Windowed, "context": bpy.context, "ops": bpy.ops})
    watch_module.bpy = proxy
    try:
        watch = watch_module._Watch(lambda module, name: (lambda **kw: calls.append((module, name, kw))))
        watch.before("sculpt.brush_stroke", (), {"brush_toggle": "None"})
        check(calls == [], "a draw stroke needs nothing: %s" % calls)
        watch.before("sculpt.brush_stroke", ("INVOKE_DEFAULT",), {"brush_toggle": "MASK"})
        check(calls == [], "an invoked stroke prepares itself")
        watch.before("sculpt.brush_stroke", (), {"brush_toggle": "MASK"})
        check(calls == [("paint", "mask_flood_fill", {"mode": "INVERT"})] * 2, "the layer made, mask kept: %s" % calls)
        watch.before("sculpt.brush_stroke", (), {"brush_toggle": "MASK"})
        check(len(calls) == 2, "once per object and run")
        check(watch.notes and "mask layer" in watch.notes[0], "and said: %s" % watch.notes)
        obj.modifiers.remove(obj.modifiers[0])
        other = watch_module._Watch(lambda module, name: (lambda **kw: calls.append((module, name, kw))))
        other.before("sculpt.brush_stroke", (), {"brush_toggle": "MASK"})
        check(len(calls) == 2, "without multires nothing is needed")
    finally:
        watch_module.bpy = real
        bpy.ops.object.mode_set(mode="OBJECT")


@test
def c_hello_names_the_process_and_the_folder_of_its_crash_report():
    hello = core.handle("hello", {})
    process = hello.get("process") or {}
    check(process.get("pid") == os.getpid(), "the pid: %s" % hello)
    check(os.path.isdir(process.get("temp_dir", "")), "an existing folder: %s" % process)
    check(os.path.normpath(bpy.app.tempdir).startswith(os.path.normpath(process["temp_dir"])), "the session folder's parent")


for fn in TESTS:
    try:
        fn()
        results["passed"].append(fn.__name__)
    except Exception as error:  # noqa: BLE001
        results["failed"].append({"name": fn.__name__, "error": "%s: %s" % (type(error).__name__, error),
                                  "trace": traceback.format_exc()[-1500:]})
print("RESULT " + json.dumps(results), flush=True)
