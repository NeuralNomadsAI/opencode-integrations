"""The six defects of the 0.3.8 typed layer that agents met in real missions, each as a test (run by blender.test.ts in
a private background Blender 5.3): they failed on the 0.3.8 add-on and pass since 0.3.9. Prints RESULT <json>.
Usage: blender -b --factory-startup --python defects_tests.py -- <scratch folder>
"""

import importlib
import json
import math
import traceback

import addon_utils
import bpy
from mathutils import Vector

MODULE = "bl_ext.user_default.opencode_link"
addon_utils.enable(MODULE, default_set=True, handle_error=None)
addon = importlib.import_module(MODULE + ".addon")
wire = importlib.import_module(MODULE + ".wire")
session = addon.session()
core, app = session.core, session.app
session.stop()

results = {"passed": [], "failed": [], "host_version": core.version}
TESTS = []
counter = [0]


def test(fn):
    TESTS.append(fn)
    return fn


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def near(a, b, tolerance=1e-4):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def empty():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    core.quarantined = False
    bpy.context.view_layer.update()
    addon.pump(0.05)


def code_of(fn):
    try:
        fn()
    except wire.HostFailure as failure:
        return failure.code, failure.message
    return None, None


def edit(*ops, **params):
    counter[0] += 1
    request = {"script_handle": app.script_handle(), "if_revision": app.revision(), "command_id": "m-%d" % counter[0],
               "operations": [{"op": op, "args": args} for op, args in ops]}
    request.update(params)
    return core.handle("apply", request)


def applied(*ops, **params):
    out = edit(*ops, **params)
    check(out["status"] == "applied", "applied: %s" % {k: out.get(k) for k in ("status", "message", "restoration", "restored")})
    return out


def run_code(text, read_only=False):
    params = {"script_handle": app.script_handle(), "code": text, "code_execution": True}
    if read_only:
        params["read_only"] = True
    return core.handle("execute_code", params)


# (1) set_parent keep_transform after a creation or transform in the same batch ----------------------
@test
def d1_parent_keeps_the_world_transform_of_objects_created_and_moved_in_the_batch():
    empty()
    out = applied(
        ("create_object", {"kind": "mesh", "primitive": "cylinder", "name": "Lampadaire", "as": "pot", "size": 1, "location": [-6, -4, 1.5]}),
        ("set_transform", {"ref": "$pot", "scale": [0.08, 0.08, 1.5]}),
        ("create_object", {"kind": "mesh", "primitive": "uv_sphere", "name": "Lampadaire_Tete", "as": "tete", "size": 1, "location": [-6, -4, 3.2]}),
        ("set_transform", {"ref": "$tete", "scale": [0.25, 0.25, 0.25]}),
        ("set_parent", {"ref": "$tete", "parent": "$pot", "keep_transform": True}),
    )
    bpy.context.view_layer.update()
    head = bpy.data.objects["Lampadaire_Tete"]
    world = head.matrix_world
    check(near(world.translation, (-6, -4, 3.2)), "the head stays where it was created: %s" % list(world.translation))
    check(near(world.to_scale(), (0.25, 0.25, 0.25)), "and keeps its size: %s" % list(world.to_scale()))
    check(near(head.scale, (0.25, 0.25, 0.25)), "its own scale is unchanged (as Blender's Ctrl+P): %s" % list(head.scale))
    del out


# (2) a full turn can be keyed ------------------------------------------------------------------------
@test
def d2_a_full_turn_is_written_and_keyed_as_360_degrees():
    empty()
    out = applied(
        ("create_object", {"kind": "mesh", "primitive": "cube", "name": "Enseigne", "as": "e", "size": 1}),
        ("set_transform", {"ref": "$e", "rotation": {"euler_deg": [0, 0, 0]}}),
        ("insert_key", {"ref": "$e", "paths": ["rotation"], "frame": 1}),
        ("set_transform", {"ref": "$e", "rotation": {"euler_deg": [0, 0, 360]}}),
        ("insert_key", {"ref": "$e", "paths": ["rotation"], "frame": 48}),
    )
    sign = bpy.data.objects["Enseigne"]
    check(abs(math.degrees(sign.rotation_euler.z) - 360) < 1e-3, "360 degrees written: %s" % list(sign.rotation_euler))
    bpy.context.scene.frame_set(24)
    check(90 < math.degrees(sign.rotation_euler.z) < 270, "about half a turn at frame 24 (keys ease in and out): %s" % math.degrees(sign.rotation_euler.z))
    keys = [change for change in out["changes"] if change["field"].startswith("key")]
    check(abs(keys[-1]["value"][2] - 2 * math.pi) < 1e-4, "the key holds 2 pi: %s" % keys[-1])
    # Continuity: another Euler order is converted next to the current angles (360 degrees at frame 48).
    bpy.context.scene.frame_set(48)
    applied(("set_transform", {"ref": "Enseigne", "rotation": {"euler_deg": [0, 0, 370], "order": "ZXY"}}))
    check(abs(math.degrees(sign.rotation_euler.z) - 370) < 1e-3, "continuous with the current value: %s" % math.degrees(sign.rotation_euler.z))
    # A quaternion holder cannot hold a full turn in one key: said, not silent.
    prop = bpy.data.objects.new("Q", None)
    bpy.context.scene.collection.objects.link(prop)
    prop.rotation_mode = "QUATERNION"
    quaternion = applied(("set_transform", {"ref": "Q", "rotation": {"euler_deg": [0, 0, 360]}}))
    check(any("quaternion" in text.lower() and "180" in text for text in quaternion.get("warnings", [])), "warned: %s" % quaternion.get("warnings"))


# (3) fit_height stretches the height only unless told otherwise ----------------------------------------
@test
def d3_fit_height_scales_z_only_by_default_and_says_what_it_scaled():
    empty()
    out = applied(("create_object", {"kind": "mesh", "primitive": "cylinder", "name": "Poteau", "as": "p", "size": 0.2}),
                  ("fit_height", {"ref": "$p", "height": 4}))
    pole = bpy.data.objects["Poteau"]
    check(near(pole.scale, (1, 1, 20)), "Z only: %s" % list(pole.scale))
    scaled = next(change for change in out["changes"] if change["field"] == "scale")
    check(scaled.get("axes") == "z" and abs(scaled.get("factor", 0) - 20) < 1e-4, "stated: %s" % scaled)
    bounds = next(change for change in out["changes"] if change["field"] == "bounds")["after"]
    check(abs(bounds["size"][2] - 4) < 1e-4 and abs(bounds["size"][0] - 0.2) < 1e-4, "4 m tall, 0.2 m wide: %s" % bounds)
    uniform = applied(("fit_height", {"ref": "Poteau", "height": 2, "scale_axes": "uniform"}))
    check(near(pole.scale, (0.5, 0.5, 10)), "uniform when asked: %s" % list(pole.scale))
    check(any("X and Y" in text for text in uniform.get("warnings", [])), "the footprint change is stated: %s" % uniform.get("warnings"))
    grounded = applied(("set_transform", {"ref": "Poteau", "location": [0, 0, 5]}), ("fit_height", {"ref": "Poteau"}))
    check(near(pole.scale, (0.5, 0.5, 10)), "no height: no scale at all")
    del grounded


# (4) a value error is no reason for quarantine ------------------------------------------------------------
@test
def d4_a_material_name_is_accepted_and_a_value_error_restores_without_quarantine():
    empty()
    obj = bpy.data.objects.new("Sol", bpy.data.meshes.new("Sol"))
    bpy.context.scene.collection.objects.link(obj)
    bpy.data.materials.new("Mat_Bois")
    tree = bpy.data.node_groups.new("Dispersion", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    tree.nodes.new("NodeGroupOutput")
    obj.modifiers.new("Dispersion", "NODES").node_group = tree
    app.undo_push("test setup")
    add = {"tree_name": "Dispersion", "node_type": "GeometryNodeSetMaterial", "name": "MatCaisse"}
    named = applied(("geometry_nodes.add_geometry_node", add),
                    ("geometry_nodes.set_node_input", {"tree_name": "Dispersion", "node_name": "MatCaisse", "input_name": "Material",
                                                        "value": "Mat_Bois"}))
    check(tree.nodes["MatCaisse"].inputs["Material"].default_value.name == "Mat_Bois", "the material by name: %s" % named["results"])
    failed = edit(("geometry_nodes.add_geometry_node", dict(add, name="Other")),
                  ("geometry_nodes.set_node_input", {"tree_name": "Dispersion", "node_name": "Other", "input_name": "Material",
                                                      "value": "No_Such_Material"}))
    check(failed["status"] == "failed" and failed["restored"] is True and not core.quarantined,
          "restored, no quarantine: %s" % {k: failed.get(k) for k in ("message", "restored", "verification")})
    tree = bpy.data.node_groups["Dispersion"]  # the undo replaced the Python wrappers
    check("Other" not in tree.nodes and "MatCaisse" in tree.nodes and "No_Such_Material" in failed["message"], "the batch undone, the reason named")
    code, _ = code_of(lambda: edit(("geometry_nodes.set_node_input", {"tree_name": "Dispersion", "node_name": "MatCaisse",
                                                                       "input_name": "Material", "value": "Mat_Bois"})))
    check(code is None, "changes still accepted")


# (5) parent null, renames seen by the next operations, the alias suggested ------------------------------------
@test
def d5_parent_null_renames_in_the_batch_and_the_alias_suggestion():
    empty()
    applied(("create_object", {"kind": "empty", "name": "A.001", "as": "a"}),
            ("create_object", {"kind": "empty", "name": "B.001", "as": "b", "parent": "$a"}))
    out = applied(("rename", {"ref": "A.001", "name": "A"}),
                  ("rename", {"ref": "B.001", "name": "B"}),
                  ("set_parent", {"ref": "B", "parent": None, "keep_transform": False}),
                  ("set_transform", {"ref": "B", "location": [1, 2, 3]}))
    b = bpy.data.objects["B"]
    check(b.parent is None and near(b.location, (1, 2, 3)), "renamed, unparented, moved: %s" % out["changes"])
    code, message = code_of(lambda: edit(("rename", {"ref": "A", "name": "A2"}), ("set_transform", {"ref": "A", "location": [0, 0, 1]})))
    check(code == "invalid_args" and "renamed to 'A2'" in message, "an old name after its rename: %s" % message)
    code, message = code_of(lambda: edit(("create_object", {"kind": "empty", "name": "Lamp", "as": "lamp"}),
                                         ("set_transform", {"ref": "Lamp", "location": [0, 0, 1]})))
    check(code == "invalid_args" and "$lamp" in message, "the alias suggested: %s" % message)
    code, message = code_of(lambda: edit(("create_object", {"kind": "empty", "name": "Lamp2"}),
                                         ("set_transform", {"ref": "Lamp2", "location": [0, 0, 1]})))
    check(code == "invalid_args" and "as" in message and "operations[0]" in message, "how to name it: %s" % message)


# (6) code that changes scene settings or keys is a change -------------------------------------------------------
@test
def d6_code_changing_scene_settings_or_keys_is_a_change_and_advances_the_revision():
    empty()
    obj = bpy.data.objects.new("K", None)
    bpy.context.scene.collection.objects.link(obj)
    app.undo_push("test setup")
    for label, text in (("frame range", "import bpy\nbpy.context.scene.frame_start = 10\nbpy.context.scene.frame_end = 77"),
                        ("render setting", "import bpy\nbpy.context.scene.render.resolution_x = 640"),
                        ("fps", "import bpy\nbpy.context.scene.render.fps = 30"),
                        ("Cycles samples", "import bpy\nbpy.context.scene.cycles.samples = 12"),
                        ("a key", "import bpy\nbpy.data.objects['K'].keyframe_insert('location', frame=5)"),
                        ("a moved key", "import bpy\nfrom bpy_extras import anim_utils\no = bpy.data.objects['K']\n"
                         "bag = anim_utils.action_get_channelbag_for_slot(o.animation_data.action, o.animation_data.action_slot)\n"
                         "bag.fcurves[0].keyframe_points[0].co.y = 9"),
                        ("a new object", "import bpy\nbpy.context.scene.collection.objects.link(bpy.data.objects.new('N', None))")):
        before = app.revision()
        out = run_code(text)
        check(out["status"] == "executed" and out["changed"] is True, "%s: changed %s" % (label, out.get("changed")))
        check(out["revision_after"] == before + 1 == app.revision(), "%s: revision %s -> %s" % (label, before, out["revision_after"]))
        check(out.get("undo_step") == "OpenCode: code", "%s: one undo step" % label)
    same = run_code("import bpy\nx = bpy.context.scene.frame_end\nresult = {'end': x}")
    check(same["changed"] is False and same.get("undo_step") is None, "a read is not a change: %s" % same)
    read_only = run_code("import bpy\nbpy.context.scene.frame_end = 90", read_only=True)
    check(read_only["changed"] is True and any("read_only" in text for text in read_only.get("warnings", [])),
          "a read_only run that changes the scene says so: %s" % read_only)


for fn in TESTS:
    try:
        fn()
        results["passed"].append(fn.__name__)
    except Exception as error:  # noqa: BLE001
        results["failed"].append({"name": fn.__name__, "error": "%s: %s" % (type(error).__name__, error),
                                  "trace": traceback.format_exc()[-1200:]})
print("RESULT " + json.dumps(results), flush=True)
