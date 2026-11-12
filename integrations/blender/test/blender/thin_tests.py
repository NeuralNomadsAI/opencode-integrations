"""The thin typed layer of OpenCode Link inside a background Blender >= 5.3.0 (run by thin.test.ts).

The compact context (`scene` method) and edit batches through `apply`: typed operations on named objects,
checked whole before anything changes, one undo step, before/after of every changed field, a failure part-way
restored field by field (quarantine when that cannot be proven), command_id replay; and, on a film-size
document, the cost against the 0.3.7 catalog path. Prints MEASURE <json> lines and one RESULT <json> line.
Usage: blender -b --factory-startup --python thin_tests.py -- <folder of film_scene.py>
"""

import importlib
import json
import math
import statistics
import sys
import time
import traceback

import addon_utils
import bpy
from mathutils import Euler, Vector

MODULE = "bl_ext.user_default.opencode_link"
sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import film_scene  # noqa: E402

addon_utils.enable(MODULE, default_set=True, handle_error=None)
addon = importlib.import_module(MODULE + ".addon")
wire = importlib.import_module(MODULE + ".wire")
session = addon.session()
core, app = session.core, session.app
session.stop()

results = {"passed": [], "failed": [], "measures": {}}
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


def code_of(fn):
    try:
        fn()
    except wire.HostFailure as failure:
        return failure.code, failure.message
    return None, None


def edit(*ops, **params):
    counter[0] += 1
    request = {"script_handle": app.script_handle(), "if_revision": app.revision(), "command_id": "thin-%d" % counter[0],
               "operations": [{"op": op, "args": args} for op, args in ops]}
    request.update(params)
    return core.handle("apply", request)


def applied(*ops, **params):
    out = edit(*ops, **params)
    check(out["status"] == "applied", "applied: %s" % {k: out.get(k) for k in ("status", "message", "restoration")})
    return out


def scene_read(**query):
    return core.handle("scene", dict(query, script_handle=app.script_handle()))


def rig(name, bones=("Root", "Arm"), scale=1.0):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.scale = (scale,) * 3
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    previous = None
    for index, bone in enumerate(bones):
        edit_bone = data.edit_bones.new(bone)
        edit_bone.head, edit_bone.tail = (0, 0, index), (0, 0, index + 1)
        if previous is not None:
            edit_bone.parent = previous
        previous = edit_bone
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def mesh_object(name, vertices):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], [tuple(range(len(vertices)))])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def small_scene():
    empty()
    character = rig("OC_Rig", ("Root", "Spine", "Head"))
    prop = mesh_object("OC_Prop", [(-1, -1, -3.5), (1, -1, -3.5), (1, 1, 3.5), (-1, 1, 3.5)])
    prop.rotation_mode = "QUATERNION"
    camera = bpy.data.objects.new("OC_Cam", bpy.data.cameras.new("OC_Cam"))
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.view_layer.update()
    return character, prop, camera


# -- compact context --------------------------------------------------------------------------------
@test
def the_compact_context_reads_the_scene_and_one_object_in_detail():
    small_scene()
    out = scene_read()
    check(out["revision"] == app.revision() and out["script_handle"] == app.script_handle() and out["mode"] == "OBJECT", "state: %s" % out)
    names = {item["name"]: item for item in out["objects"]}
    check(set(names) == {"OC_Rig", "OC_Prop", "OC_Cam"} and out["objects_total"] == 3, "objects: %s" % list(names))
    check(names["OC_Prop"]["rotation"] == {"mode": "QUATERNION", "quaternion": [1.0, 0.0, 0.0, 0.0]}, "rotation in its mode: %s" % names["OC_Prop"])
    check(names["OC_Rig"]["bones"] == 3 and "pose_bones" not in names["OC_Rig"], "bones counted, not listed")
    check([group["name"] for group in out["collections"]] == ["Scene Collection"], "collections: %s" % out["collections"])
    detail = scene_read(ref="OC_Rig")
    bones = detail["objects"][0]["pose_bones"]
    check([bone["name"] for bone in bones] == ["Root", "Spine", "Head"] and bones[2]["dir"] == [0.0, 0.0, 1.0], "pose bones: %s" % bones)
    bounds = scene_read(ref="OC_Prop")["objects"][0]["bounds"]
    check(bounds["size"] == [2.0, 2.0, 7.0] and bounds["min"][2] == -3.5, "evaluated bounds: %s" % bounds)
    page = scene_read(limit=2)
    check(len(page["objects"]) == 2 and page["next_offset"] == 2 and len(scene_read(offset=2)["objects"]) == 1, "pages")
    check([item["name"] for item in scene_read(match="OC_C*")["objects"]] == ["OC_Cam"], "match")
    check([item["name"] for item in scene_read(types=["ARMATURE"])["objects"]] == ["OC_Rig"], "types")
    for bad in ({"limit": 0}, {"nope": 1}, {"ref": "missing"}, {"include": ["everything"]}):
        code, message = code_of(lambda bad=bad: scene_read(**bad))
        check(code == "invalid_args", "refused %s: %s %s" % (bad, code, message))


# -- edit batches -----------------------------------------------------------------------------------
@test
def a_batch_changes_what_it_names_reports_before_and_after_and_is_one_undo_step():
    character, prop, camera = small_scene()
    app.undo_push("test: setup")  # the interface's last step before OpenCode's batch
    revision = app.revision()
    out = applied(
        ("set_transform", {"ref": "OC_Prop", "rotation": {"euler_deg": [0, 0, 90]}, "location": [1, 2, 3]}),
        ("fit_height", {"ref": "OC_Prop", "height": 1.8}),
        ("pose_bone", {"ref": "OC_Rig", "bone": "Spine", "rotation": {"euler_deg": [30, 0, 0]}}),
        ("aim_bone", {"ref": "OC_Rig", "bone": "Head", "target": {"direction": [1, 0, 0]}}),
        ("insert_key", {"ref": "OC_Rig", "bone": "Head", "paths": ["rotation"], "frame": 12}),
        ("create_collection", {"name": "OC_Shots"}),
        ("create_object", {"kind": "camera", "name": "OC_Close", "as": "close", "collection": "OC_Shots", "location": [0, -5, 1.6],
                           "lens": 85}),
        ("create_object", {"kind": "empty", "name": "OC_Target", "as": "target"}),
        ("add_constraint", {"ref": "$target", "type": "COPY_LOCATION", "target": "OC_Rig", "subtarget": "Head"}),
        ("add_constraint", {"ref": "$close", "type": "TRACK_TO", "target": "$target"}),
        ("add_marker", {"name": "Close", "frame": 24, "camera": "$close"}),
        ("set_camera", {"ref": "$close"}),
        ("link", {"ref": "OC_Prop", "collection": "OC_Shots"}),
        ("set_parent", {"ref": "OC_Cam", "parent": "OC_Rig", "bone": "Head"}),
        ("rename", {"ref": "OC_Cam", "name": "OC_Cam_Head"}),
        summary="shot setup",
    )
    check(out["undo_step"] == "OpenCode: shot setup" and app.revision() == revision + 1, "one named step, one revision: %s" % out["undo_step"])
    fields = [(change["ref"], change["field"]) for change in out["changes"]]
    check(("OC_Prop", "rotation_quaternion") in fields and ("OC_Prop", "location") in fields, "quaternion written: %s" % fields)
    rotation = next(change for change in out["changes"] if change["field"] == "rotation_quaternion")
    check(rotation["before"] == [1.0, 0.0, 0.0, 0.0] and near(rotation["after"], [math.sqrt(0.5), 0, 0, math.sqrt(0.5)]), "before/after: %s" % rotation)
    bounds = next(change for change in out["changes"] if change["field"] == "bounds")["after"]
    check(abs(bounds["size"][2] - 1.8) < 1e-4 and abs(bounds["min"][2]) < 1e-4, "1.8 m, on the ground: %s" % bounds)
    aim = next(change for change in out["changes"] if change["field"].startswith("aim"))
    check(aim["error_deg"] is not None and aim["error_deg"] < 1e-3, "aimed: %s" % aim)
    check([entry["name"] for entry in out["created"]] == ["OC_Shots", "OC_Close", "OC_Target", "Close"], "created: %s" % out["created"])
    check(bpy.context.scene.camera.name == "OC_Close" and bpy.data.objects["OC_Cam_Head"].parent_bone == "Head", "applied in Blender")
    head = character.pose.bones["Head"]
    check(head.id_data.animation_data.action is not None, "keyed")
    world = bpy.data.objects["OC_Cam_Head"].matrix_world.translation.copy()
    check(near(world, camera.matrix_world.translation), "keep_transform")
    # One step: undoing once returns to the step before the batch.
    app.undo()
    check(bpy.data.objects.get("OC_Close") is None and bpy.data.collections.get("OC_Shots") is None and "OC_Cam" in bpy.data.objects,
          "undone in one step")
    prop = bpy.data.objects["OC_Prop"]
    check(near(prop.rotation_quaternion, (1, 0, 0, 0)) and near(prop.location, (0, 0, 0)) and not bpy.context.scene.timeline_markers,
          "every change undone")


@test
def rotations_are_written_in_each_holders_own_mode():
    empty()
    turned = {}
    for mode in ("QUATERNION", "AXIS_ANGLE", "XYZ", "ZXY"):
        obj = bpy.data.objects.new("OC_%s" % mode, None)
        bpy.context.scene.collection.objects.link(obj)
        obj.rotation_mode = mode
        turned[mode] = obj
    applied(*[("set_transform", {"ref": obj.name, "rotation": {"euler_deg": [10, 20, 30], "order": "XYZ"}}) for obj in turned.values()])
    bpy.context.view_layer.update()
    expected = Euler([math.radians(a) for a in (10, 20, 30)], "XYZ").to_matrix()
    for mode, obj in turned.items():
        got = obj.matrix_world.to_3x3()
        check(all(abs(got[i][j] - expected[i][j]) < 1e-5 for i in range(3) for j in range(3)), "%s: the same orientation" % mode)
    quaternion = applied(("set_transform", {"ref": "OC_XYZ", "rotation": {"quaternion": [0, 0, 0, 1]}}))["changes"][0]
    check(quaternion["field"] == "rotation_euler_deg (XYZ)" and abs(abs(quaternion["after"][2]) - 180) < 1e-3, "a quaternion into Euler: %s" % quaternion)


@test
def aims_report_their_error_and_bones_that_cannot_turn_are_refused_first():
    empty()
    character = rig("OC_Rig", ("Root", "Arm", "Locked", "Held"), scale=0.0026)  # a Mixamo-like armature scale
    character.rotation_euler = (0, 0, math.radians(37))
    character.pose.bones["Locked"].lock_rotation = (True, False, False)
    character.pose.bones["Held"].constraints.new("DAMPED_TRACK")
    point = bpy.data.objects.new("OC_Point", None)
    point.location = (1, 2, 0)
    bpy.context.scene.collection.objects.link(point)
    bpy.context.view_layer.update()
    # The parent first: turning a parent afterwards would move the child it carries.
    out = applied(("aim_bone", {"ref": "OC_Rig", "bone": "Root", "target": {"point": [0, 0, -1]}, "axis": "-Z"}),
                  ("aim_bone", {"ref": "OC_Rig", "bone": "Arm", "target": {"object": "OC_Point"}}))
    for change in out["changes"]:
        check(change["error_deg"] < 1e-3, "aim error: %s" % change)
    arm = character.pose.bones["Arm"]
    direction = ((character.matrix_world @ arm.tail) - (character.matrix_world @ arm.head)).normalized()
    wanted = (point.matrix_world.translation - character.matrix_world @ arm.head).normalized()
    check(direction.angle(wanted) < 1e-4, "points at the object")
    for bone, reason in (("Locked", "locked rotation"), ("Held", "active constraints")):
        revision = app.revision()
        code, message = code_of(lambda bone=bone: edit(("aim_bone", {"ref": "OC_Rig", "bone": bone, "target": {"direction": [1, 0, 0]}})))
        check(code == "invalid_args" and reason in message and app.revision() == revision, "%s refused: %s" % (bone, message))


@test
def a_failure_part_way_restores_every_field_it_changed():
    character, prop, camera = small_scene()
    group = bpy.data.collections.new("OC_Other")
    bpy.context.scene.collection.children.link(group)
    before = {"location": tuple(prop.location), "rotation": tuple(prop.rotation_quaternion), "scale": tuple(prop.scale),
              "spine": tuple(character.pose.bones["Spine"].rotation_quaternion), "camera": bpy.context.scene.camera,
              "objects": len(bpy.data.objects), "collections": len(bpy.data.collections), "parent": camera.parent}
    revision = app.revision()
    out = edit(
        ("set_transform", {"ref": "OC_Prop", "location": [5, 5, 5], "rotation": {"euler_deg": [0, 90, 0]}}),
        # Turned 90 degrees about Y, the prop is tilted: scaling its own Z alone would not set its height.
        ("fit_height", {"ref": "OC_Prop", "height": 2, "scale_axes": "uniform"}),
        ("pose_bone", {"ref": "OC_Rig", "bone": "Spine", "rotation": {"quaternion": [0.9, 0.1, 0, 0]}}),
        ("insert_key", {"ref": "OC_Prop", "paths": ["location", "rotation"], "frame": 5}),
        ("create_object", {"kind": "light", "name": "OC_Lamp", "as": "lamp", "light_type": "SUN"}),
        ("create_collection", {"name": "OC_New"}),
        ("link", {"ref": "OC_Prop", "collection": "OC_New"}),
        ("set_parent", {"ref": "OC_Cam", "parent": "$lamp"}),
        ("add_marker", {"name": "M", "frame": 3}),
        ("set_camera", {"ref": "OC_Cam"}),
        ("rename", {"ref": "OC_Rig", "name": "OC_Renamed"}),
        ("unlink", {"ref": "OC_Prop", "collection": "OC_Other"}),  # not in OC_Other: fails while applying
    )
    check(out["status"] == "failed" and out["failed_operation"] == 11 and out["restored"] is True and not core.quarantined,
          "restored: %s" % {k: out.get(k) for k in ("status", "message", "restored", "restoration")})
    proofs = out["restoration"]
    check(proofs["inverses"] == proofs["ran"] == proofs["proved"] > 10, "every inverse ran and was proved: %s" % proofs)
    check(near(prop.location, before["location"]) and near(prop.rotation_quaternion, before["rotation"]) and near(prop.scale, before["scale"]),
          "transform back")
    check(near(character.pose.bones["Spine"].rotation_quaternion, before["spine"]) and character.name == "OC_Rig", "pose and name back")
    check(prop.animation_data is None or prop.animation_data.action is None, "keys and the new action gone")
    check(len(bpy.data.objects) == before["objects"] and len(bpy.data.collections) == before["collections"]
          and not bpy.context.scene.timeline_markers and bpy.context.scene.camera == before["camera"] and camera.parent is None,
          "creations, marker, camera and parent back")
    check(out["undone"] and app.revision() == revision + 1, "reported and a new revision")
    # A restoration that cannot be proven quarantines.
    rollback = app.thin_rollback
    app.thin_rollback = lambda batch: (rollback(batch)[0] and False, {"inverses": 1, "ran": 0, "proved": 0})
    try:
        out = edit(("set_transform", {"ref": "OC_Prop", "location": [1, 1, 1]}), ("unlink", {"ref": "OC_Prop", "collection": "OC_Other"}))
    finally:
        app.thin_rollback = rollback
    check(out["restored"] is False and core.quarantined, "quarantined: %s" % out)
    code, _ = code_of(lambda: edit(("set_transform", {"ref": "OC_Prop", "location": [0, 0, 0]})))
    check(code == "quarantined", "no change while quarantined")
    core.quarantined = False


@test
def refusals_change_nothing():
    small_scene()
    cases = [
        ([("set_transform", {"ref": "OC_Prop"})], "nothing to set"),
        ([("set_transform", {"ref": "OC_Prop", "location": [1, 2]})], "expected 3 numbers"),
        ([("set_transform", {"ref": "OC_Prop", "location": [1, 2, float("nan")]})], "finite"),
        ([("set_transform", {"ref": "OC_Prop", "spin": 1})], "unknown field spin"),
        ([("pose_bone", {"ref": "OC_Rig", "bone": "Tail", "location": [0, 0, 0]})], "has no bone"),
        ([("pose_bone", {"ref": "OC_Prop", "bone": "Head", "location": [0, 0, 0]})], "is not an armature"),
        ([("set_parent", {"ref": "$later"}), ("create_object", {"kind": "empty", "name": "E", "as": "later"})], "before the create_object"),
        ([("insert_key", {"ref": "OC_Prop", "paths": ["location"], "frame": 2000000})], "frame from"),
        ([("link", {"ref": "OC_Prop", "collection": "Nowhere"})], "no collection"),
        ([("create_collection", {"name": "Twice"}), ("create_collection", {"name": "Twice"})], "exists"),
        ([("delete", {"refs": ["OC_Prop", "OC_Prop"]})], "listed twice"),
        ([("create_object", {"kind": "empty", "name": "E", "lens": 50})], "does not apply"),
        ([("set_transform", {"ref": "OC_Prop", "location": [0, 0, 0]}), ("scene.delete_object", {"name": "OC_Prop"})], "edit operations only"),
    ]
    for ops, reason in cases:
        revision, objects = app.revision(), len(bpy.data.objects)
        code, message = code_of(lambda ops=ops: edit(*ops))
        check(code == "invalid_args" and reason in (message or ""), "%s: %s %s" % (reason, code, message))
        check(app.revision() == revision and len(bpy.data.objects) == objects, "nothing changed for %s" % reason)
    code, message = code_of(lambda: edit(("set_transform", {"ref": "OC_Prop", "location": [0, 0, 1]}), if_revision=app.revision() - 1))
    check(code == "stale_revision", "a stale revision: %s" % message)
    code, message = code_of(lambda: edit(("set_transform", {"ref": "OC_Prop", "location": [0, 0, 1]}),
                                         expect=[{"ref": "object:OC_Prop", "predicate": "absent"}]))
    check(code == "expectation_failed", "expect: %s" % message)


@test
def a_replayed_command_id_applies_nothing_twice():
    small_scene()
    request = {"script_handle": app.script_handle(), "if_revision": app.revision(), "command_id": "thin-replay",
               "operations": [{"op": "set_transform", "args": {"ref": "OC_Prop", "location": [0, 0, 1], "delta": True}}]}
    first = core.handle("apply", dict(request))
    again = core.handle("apply", dict(request, if_revision=app.revision()))
    check(first["status"] == "applied" and again.get("replayed") is True, "replayed: %s" % again)
    check(near(bpy.data.objects["OC_Prop"].location, (0, 0, 1)), "moved once")
    code, message = code_of(lambda: core.handle("apply", dict(request, operations=[{"op": "set_transform", "args": {"ref": "OC_Prop",
                                                                                                                    "location": [0, 0, 9]}}])))
    check(code == "invalid_args" and "already used" in message, "another change under the same id: %s" % message)


@test
def delete_runs_last_and_old_catalog_names_still_apply():
    small_scene()
    out = applied(("set_transform", {"ref": "OC_Prop", "location": [0, 0, 2]}), ("delete", {"refs": ["OC_Cam"]}))
    check("OC_Cam" not in bpy.data.objects and out["changes"][-1] == {"op": 1, "ref": "OC_Cam", "field": "deleted"}, "deleted: %s" % out["changes"])
    counter[0] += 1
    old = core.handle("apply", {"script_handle": app.script_handle(), "if_revision": app.revision(), "command_id": "thin-old-%d" % counter[0],
                                "operations": [{"op": "scene.transform_object", "args": {"name": "OC_Prop", "location": [0, 0, 3]}}]})
    check(old["status"] == "applied" and near(bpy.data.objects["OC_Prop"].location, (0, 0, 3)), "the 0.3.7 name still works")


# -- a film-size document: cost against the 0.3.7 catalog path ---------------------------------------
def timed(fn, repeat=5):
    times, value = [], None
    for _ in range(repeat):
        started = time.perf_counter()
        value = fn()
        times.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(times), 2), value


def size(value):
    return len(json.dumps(value))


@test
def on_a_film_size_document_the_thin_layer_touches_only_what_it_names():
    empty()
    made = film_scene.build()
    structural = importlib.import_module(MODULE + ".native_scene").structural_count()
    check(structural >= 5000, "film-size: %d entities + edges" % structural)
    heads = [(name, bone, direction) for name in made["rigs"]
             for bone, direction in (("B57", [1, 0, 0.2]), ("B40", [0, 1, 0]), ("Arm.L", [0.3, -1, -2]))]
    thin_ops = [("aim_bone", {"ref": name, "bone": bone, "target": {"direction": direction}}) for name, bone, direction in heads]
    thin_ops += [("insert_key", {"ref": name, "bone": bone, "paths": ["rotation"], "frame": 12}) for name, bone, _ in heads]

    def thin_apply():
        out = applied(*thin_ops)
        check(all(change["error_deg"] < 1e-3 for change in out["changes"] if "error_deg" in change), "aimed")
        return out

    def catalog_apply():
        counter[0] += 1
        out = core.handle("apply", {"script_handle": app.script_handle(), "if_revision": app.revision(), "command_id": "film-%d" % counter[0],
                                    "operations": [{"op": "animation.aim_bone", "args": {"armature": name, "bone": bone, "direction": direction,
                                                                                         "frame": 12}} for name, bone, direction in heads]})
        check(out["status"] == "applied", "catalog: %s" % out.get("message"))
        return out

    def failing():
        out = edit(*(thin_ops[:6] + [("unlink", {"ref": "OC_Building_00", "collection": "Scene Collection"})]))
        check(out["status"] == "failed" and out["restored"], "restored: %s" % out.get("restoration"))
        return out

    measures = {"structural_elements": structural, "objects": made["objects"]}
    measures["thin_apply_ms"], thin = timed(thin_apply)
    measures["thin_apply_bytes"] = size(thin)
    measures["thin_in_blender_ms"] = thin["ms"]
    measures["catalog_apply_ms"], catalog = timed(catalog_apply)
    measures["catalog_apply_bytes"] = size(catalog)
    measures["catalog_verification"] = catalog.get("verification")
    measures["thin_failure_restored_ms"], _ = timed(failing)
    measures["compact_context_ms"], compact = timed(lambda: scene_read())
    measures["compact_context_bytes"] = size(compact)
    measures["rig_detail_ms"], detail = timed(lambda: scene_read(ref=made["rigs"][0]))
    measures["rig_detail_bytes"] = size(detail)
    measures["native_query_ms"], native = timed(lambda: core.handle("context", {"script_handle": app.script_handle(),
                                                                                "query": {"limit": 100}}))
    measures["native_query_bytes"] = size(native)
    measures["scene_info_read_ms"], info = timed(lambda: core.handle("context", {"script_handle": app.script_handle(),
                                                                                  "reads": [{"name": "scene.get_scene_info"}]}))
    measures["scene_info_read_bytes"] = size(info)
    results["measures"]["film"] = measures
    print("MEASURE " + json.dumps(measures), flush=True)
    check(measures["thin_apply_ms"] < measures["catalog_apply_ms"], "the thin path is cheaper: %s" % measures)


# -- a subset of the journal, one call per task ------------------------------------------------------
@test
def journal_tasks_take_one_call_each():
    empty()
    made = film_scene.build()
    cowboy, outlaw = made["rigs"]
    calls = {}
    # 7: an import 7 m tall and half under the ground → 1.8 m, feet at z = 0 (journal: 3 attempts).
    out = applied(("fit_height", {"ref": "OC_Import", "height": 1.8}))
    bounds = out["changes"][-1]["after"]
    check(abs(bounds["size"][2] - 1.8) < 1e-4 and abs(bounds["min"][2]) < 1e-4, "7: %s" % bounds)
    calls["7 scale to 1.8 m, feet on the ground"] = 1
    # 10: turn an object in quaternion mode (journal: 3 attempts, rotation_euler silently ignored).
    out = applied(("set_transform", {"ref": "OC_Import", "rotation": {"euler_deg": [0, 0, 180]}}))
    check(out["changes"][0]["field"] == "rotation_quaternion", "10: %s" % out["changes"])
    calls["10 turn an object in quaternion mode"] = 1
    # 9/16: arms along the body, keyed (journal: a numeric axis/angle search per bone).
    out = applied(*[("aim_bone", {"ref": name, "bone": bone, "target": {"direction": [0, 0, -1]}}) for name in (cowboy, outlaw)
                    for bone in ("Arm.L", "Arm.R")],
                  *[("insert_key", {"ref": name, "bone": bone, "paths": ["rotation"], "frame": 1}) for name in (cowboy, outlaw)
                    for bone in ("Arm.L", "Arm.R")])
    check(all(change["error_deg"] < 1e-3 for change in out["changes"] if "error_deg" in change), "9/16")
    calls["9/16 arms down and keyed"] = 1
    # 14: cameras with targets on a bone, shot markers (journal: 2 attempts, targets placed wrong at 0.0026 scale).
    ops = [("create_collection", {"name": "OC_Shots"}),
           ("create_object", {"kind": "empty", "name": "OC_HeadTarget", "as": "target", "collection": "OC_Shots"}),
           ("add_constraint", {"ref": "$target", "type": "COPY_LOCATION", "target": outlaw, "subtarget": "B57"})]
    for index, (name, location) in enumerate((("OC_Cam_Close", [2, -4, 1.7]), ("OC_Cam_Over", [-5, -3, 2]), ("OC_Cam_Low", [0, -6, 0.3]))):
        alias = "cam%d" % index
        ops += [("create_object", {"kind": "camera", "name": name, "as": alias, "collection": "OC_Shots", "location": location}),
                ("add_constraint", {"ref": "$" + alias, "type": "TRACK_TO", "target": "$target"}),
                ("add_marker", {"name": name[7:], "frame": 1 + 24 * index, "camera": "$" + alias})]
    ops.append(("set_camera", {"ref": "$cam0"}))
    out = applied(*ops)
    bpy.context.view_layer.update()
    target = bpy.data.objects["OC_HeadTarget"].matrix_world.translation
    outlaw_obj = bpy.data.objects[outlaw]
    head = outlaw_obj.matrix_world @ outlaw_obj.pose.bones["B57"].head
    check((target - head).length < 1e-4 and len(bpy.context.scene.timeline_markers) == 3, "14: target on the head, 3 markers")
    calls["14 cameras, bone targets and markers"] = 1
    # 35: six head and arm aims on the film document (0.3.6: refused by the structural budget).
    out = applied(*[("aim_bone", {"ref": name, "bone": bone, "target": {"object": "OC_Cam_Close"}}) for name in (cowboy, outlaw)
                    for bone in ("B57", "B56", "B55")])
    check(all(change["error_deg"] < 1e-3 for change in out["changes"] if "error_deg" in change), "35")
    calls["35 six aims on the film document"] = 1
    results["measures"]["journal_calls"] = calls
    print("MEASURE " + json.dumps({"journal_calls": calls}), flush=True)


for fn in TESTS:
    try:
        fn()
        results["passed"].append(fn.__name__)
    except Exception as error:  # noqa: BLE001
        results["failed"].append({"name": fn.__name__, "error": "%s: %s" % (type(error).__name__, error),
                                  "trace": traceback.format_exc()[-1500:]})
print("RESULT " + json.dumps(results), flush=True)
