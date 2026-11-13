"""The frictions of the western film's v7 (journal entries 47 to 62), each replayed as a test in a private background
Blender 5.3 (run by blender.test.ts): they fail on the 0.4.1 add-on and pass on 0.4.2. Prints RESULT <json>.
Usage: blender -b --factory-startup --python ops042_tests.py -- <scratch folder>
"""

import importlib
import json
import math
import os
import sys
import traceback

import addon_utils
import bpy
from mathutils import Matrix, Vector

MODULE = "bl_ext.user_default.opencode_link"
addon_utils.enable(MODULE, default_set=True, handle_error=None)
addon = importlib.import_module(MODULE + ".addon")
wire = importlib.import_module(MODULE + ".wire")
session = addon.session()
core, app = session.core, session.app
session.stop()
SCRATCH = sys.argv[sys.argv.index("--") + 1]

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
    a, b = list(a), list(b)
    return len(a) == len(b) and all(abs(x - y) <= tolerance for x, y in zip(a, b))


def empty():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    core.quarantined = False
    bpy.context.view_layer.update()
    addon.pump(0.05)


def edit(*ops, **params):
    counter[0] += 1
    request = {"script_handle": app.script_handle(), "if_revision": app.revision(), "command_id": "v7-%d" % counter[0],
               "operations": [{"op": op, "args": args} for op, args in ops]}
    request.update(params)
    return core.handle("apply", request)


def applied(*ops, **params):
    out = edit(*ops, **params)
    check(out["status"] == "applied", "applied: %s" % {k: out.get(k) for k in ("status", "message", "restoration", "restored")})
    return out


def refused(*ops):
    try:
        edit(*ops)
    except wire.HostFailure as failure:
        return failure.message
    return None


def run_code(text, read_only=False):
    params = {"script_handle": app.script_handle(), "code": text}
    if read_only:
        params["read_only"] = True
    return core.handle("execute_code", params)


def scene_read(**query):
    return core.handle("scene", dict(query, script_handle=app.script_handle()))


def obj(name):
    return bpy.data.objects[name]


def link(thing):
    bpy.context.scene.collection.objects.link(thing)
    return thing


def cube_mesh(name, offset=(0, 0, 0), size=1.0):
    import bmesh

    data = bpy.data.meshes.new(name)
    mesh = bmesh.new()
    bmesh.ops.create_cube(mesh, size=size, matrix=Matrix.Translation(offset))
    mesh.to_mesh(data)
    mesh.free()
    return data


# -- entries 47, 48: verify and read-only runs leave the revision alone ---------------------------------------
@test
def e47_48_a_read_only_run_that_puts_everything_back_is_no_change():
    empty()
    scene = bpy.context.scene
    camera = link(bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam")))
    scene.camera = camera
    rig = link(bpy.data.objects.new("Box", cube_mesh("Box")))
    rig.keyframe_insert("location", frame=1)
    rig.location.x = 3
    rig.keyframe_insert("location", frame=20)
    scene.frame_set(1)
    image = bpy.data.images.new("Skin", 8, 8)
    app.undo_push("setup")
    before = app.revision()
    # What blender_verify and the journal's probes do: another frame, a render, a temporary copy of a texture.
    probe = ("import bpy\nsc = bpy.context.scene\nold = sc.frame_current\nsc.frame_set(12)\n"
             "sc.render.engine = 'BLENDER_WORKBENCH'\nsc.render.resolution_x, sc.render.resolution_y = 32, 24\n"
             "sc.render.filepath = %r\nbpy.ops.render.render(write_still=True)\n"
             "copy = bpy.data.images['Skin'].copy()\nbpy.data.images.remove(copy)\n"
             "sc.render.engine = 'BLENDER_EEVEE'\nsc.render.resolution_x, sc.render.resolution_y = 1920, 1080\n"
             "sc.render.filepath = '/tmp/'\nsc.frame_set(old)\nresult = {'ok': True}") % os.path.join(SCRATCH, "probe.png")
    scene.render.engine, scene.render.filepath = "BLENDER_EEVEE", "/tmp/"
    for _ in range(3):
        out = run_code(probe, read_only=True)
        check(out["status"] == "executed", "ran: %s" % out.get("message"))
        check(out["changed"] is False and not out.get("warnings"), "no change said: %s %s" % (out["changed"], out.get("warnings")))
    check(app.revision() == before, "the revision stays: %s -> %s" % (before, app.revision()))
    check(image.name in bpy.data.images, "the image kept")
    moved = run_code("import bpy\nbpy.data.objects['Box'].location.z = 4", read_only=True)
    check(moved["changed"] is True and any("read_only" in w for w in moved.get("warnings", [])), "a real change is still said")
    check(app.revision() == before + 1, "and advances the revision")
    pose = run_code("import bpy\nm = bpy.data.meshes['Box']\nv = m.vertices[0].co.copy()\nm.vertices[0].co.z += 1\n"
                    "m.vertices[0].co = v", read_only=True)
    check(pose["changed"] is False, "geometry put back: %s" % pose.get("warnings"))
    shifted = run_code("import bpy\nbpy.data.meshes['Box'].vertices[0].co.z += 1", read_only=True)
    check(shifted["changed"] is True, "geometry changed is a change")


# -- entry 49: the loose parts of a mesh --------------------------------------------------------------------------
@test
def e49_a_mesh_s_loose_parts_with_their_box_material_and_vertex_group():
    empty()
    import bmesh

    data = bpy.data.meshes.new("Body")
    mesh = bmesh.new()
    bmesh.ops.create_cube(mesh, size=2.0)
    bmesh.ops.create_cube(mesh, size=0.2, matrix=Matrix.Translation((3, 0, 0)))
    bmesh.ops.create_cube(mesh, size=0.4, matrix=Matrix.Translation((0, 0, 5)))
    mesh.to_mesh(data)
    mesh.free()
    body = link(bpy.data.objects.new("Body", data))
    body.location = (10, 0, 0)
    gun = body.vertex_groups.new(name="Gun")
    gun.add([v.index for v in data.vertices if v.co.x > 2.5], 1.0, "REPLACE")
    steel = bpy.data.materials.new("Steel")
    data.materials.append(bpy.data.materials.new("Skin"))
    data.materials.append(steel)
    for polygon in data.polygons:
        polygon.material_index = 1 if polygon.center.x > 2.5 else 0
    bpy.context.view_layer.update()
    out = scene_read(ref="Body", include=["parts"])
    parts = out["objects"][0]["parts"]
    check(out["objects"][0]["parts_total"] == 3 and [p["vertices"] for p in parts] == [8, 8, 8], "three parts: %s" % parts)
    revolver = [p for p in parts if p.get("vertex_group") == "Gun"]
    check(len(revolver) == 1 and revolver[0]["material"] == "Steel", "the small part: %s" % parts)
    check(near(revolver[0]["center"], (13, 0, 0)) and near(revolver[0]["min"], (12.9, -0.1, -0.1)), "its world box")
    found = scene_read(ref="Body", include=["parts"], region={"min": [12.5, -1, -1], "max": [14, 1, 1]})
    check(len(found["objects"][0]["parts"]) == 1 and found["objects"][0]["parts"][0].get("vertex_group") == "Gun",
          "region keeps the gun: %s" % found["objects"][0]["parts"])


# -- entry 50: delete a collection ---------------------------------------------------------------------------------
@test
def e50_delete_removes_an_empty_collection_and_refuses_a_full_one():
    empty()
    applied(("create_collection", {"name": "_OCTest"}), ("create_collection", {"name": "Props"}),
            ("create_object", {"kind": "empty", "name": "Barrel", "collection": "Props"}))
    out = applied(("delete", {"collections": ["_OCTest"]}))
    check("_OCTest" not in bpy.data.collections, "deleted")
    check(any(c["field"] == "deleted collection" and c["ref"] == "_OCTest" for c in out["changes"]), "said: %s" % out["changes"])
    failed = edit(("delete", {"collections": ["Props"]}))
    check(failed["status"] == "failed" and "Barrel" in failed["message"] and failed["restored"], "a full one refused: %s" % failed)
    check("Props" in bpy.data.collections, "Props kept")
    applied(("delete", {"refs": ["Barrel"], "collections": ["Props"]}))
    check("Props" not in bpy.data.collections and "Barrel" not in bpy.data.objects, "with its object, in one operation")
    check(refused(("delete", {"collections": ["Nowhere"]})) is not None, "an unknown collection is refused before running")


# -- entry 60: a cylinder (and a cone) with diameter and height apart -----------------------------------------------
@test
def e60_a_barrel_is_one_cylinder_with_size_and_depth():
    empty()
    applied(("create_object", {"kind": "mesh", "primitive": "cylinder", "name": "Barrel", "size": 0.6, "depth": 0.9}),
            ("create_object", {"kind": "mesh", "primitive": "cone", "name": "Roof", "size": 2, "depth": 0.5}),
            ("create_object", {"kind": "mesh", "primitive": "cylinder", "name": "Post", "size": 0.2}))
    bpy.context.view_layer.update()
    check(near(obj("Barrel").dimensions, (0.6, 0.6, 0.9)), "barrel: %s" % list(obj("Barrel").dimensions))
    check(near(obj("Roof").dimensions, (2, 2, 0.5)), "cone: %s" % list(obj("Roof").dimensions))
    check(near(obj("Post").dimensions, (0.2, 0.2, 0.2)), "size alone is still diameter and height")
    message = refused(("create_object", {"kind": "mesh", "primitive": "cube", "name": "C", "depth": 1}))
    check(message and "cylinder or cone" in message, "depth on a cube: %s" % message)


# A runtime failure (Blender clamps 40000 fps to 32767): the operations before it must be undone.
FAIL = ("scene_settings", {"fps": 40000})


def failed_and_restored(*ops):
    out = edit(*ops)
    check(out["status"] == "failed" and out["restored"] is True and not core.quarantined,
          "failed and restored: %s" % {k: out.get(k) for k in ("status", "message", "restoration", "restored")})
    return out


def rig_with_hand():
    """An armature whose Hand bone moves along X from frame 1 (x 0) to frame 101 (x 10), and a Hips bone that stays."""
    data = bpy.data.armatures.new("Rig")
    rig = link(bpy.data.objects.new("Rig", data))
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for name, head, tail in (("Hips", (0.3, 0, 1), (0.3, 0, 1.2)), ("Hand", (0, -0.5, 1.2), (0, -0.5, 1.3))):
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
    bpy.ops.object.mode_set(mode="OBJECT")
    hand = rig.pose.bones["Hand"]
    for frame, x in ((1, 0.0), (101, 10.0)):
        hand.location = (x, 0, 0)  # the bone's local axes: X is world X here (no roll)
        hand.keyframe_insert("location", frame=frame)
    for curve in rig.animation_data.action.layers[0].strips[0].channelbags[0].fcurves:
        for point in curve.keyframe_points:
            point.interpolation = "LINEAR"
    return rig


def world_at(name, frame):
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()
    return obj(name).matrix_world.translation.copy()


# -- entry 53: change the revolver's parent at given frames, without a jump ------------------------------------------
@test
def e53_the_revolver_goes_holster_hand_ground_without_a_jump():
    empty()
    rig_with_hand()
    gun = link(bpy.data.objects.new("Revolver", cube_mesh("Revolver", size=0.2)))
    gun.location = (0.35, 0.1, 0.95)
    bpy.context.scene.frame_set(1)
    app.undo_push("setup")
    out = applied(("switch_parent", {"ref": "Revolver", "parent": "Rig", "bone": "Hips", "frame": 10}),
                  ("switch_parent", {"ref": "Revolver", "parent": "Rig", "bone": "Hand", "frame": 30}),
                  ("switch_parent", {"ref": "Revolver", "frame": 60}))
    check(all(c.get("jump", 1) < 1e-4 for c in out["changes"] if c["field"].startswith("parent from")), "no jump: %s" % out["changes"])
    check(bpy.context.scene.frame_current == 1, "the current frame stays")
    for frame in (9, 10, 29):
        check(near(world_at("Revolver", frame), (0.35, 0.1, 0.95)), "holstered at %d: %s" % (frame, list(world_at("Revolver", frame))))
    at30, at40, at59 = world_at("Revolver", 30), world_at("Revolver", 40), world_at("Revolver", 59)
    check(near(at30, (0.35, 0.1, 0.95)), "taken without a jump: %s" % list(at30))
    check(near(at40 - at30, (1.0, 0, 0), 1e-3), "follows the hand (1 m in 10 frames): %s" % list(at40 - at30))
    check(near(world_at("Revolver", 60), at59 + Vector((0.1, 0, 0)), 1e-3), "dropped where the hand left it")
    check(near(world_at("Revolver", 90), world_at("Revolver", 60)), "then stays on the ground while the hand moves on")
    constraints = [c.name for c in gun.constraints]
    check(len(constraints) == 3 and all(n.startswith("OC parent ") for n in constraints), "three switches: %s" % constraints)
    # One more switch undone with a failing batch: its anchor, constraint and keys gone, the others intact.
    bpy.context.scene.frame_set(1)
    objects = len(bpy.data.objects)
    failed_and_restored(("switch_parent", {"ref": "Revolver", "parent": "Rig", "bone": "Hand", "frame": 80}), FAIL)
    check(len(bpy.data.objects) == objects and len(gun.constraints) == 3, "restored")
    check(near(world_at("Revolver", 90), world_at("Revolver", 60)), "the ground switch still holds")
    message = refused(("switch_parent", {"ref": "Revolver", "parent": "Rig", "bone": "Nope", "frame": 5}))
    check(message and "no bone" in message, "an unknown bone: %s" % message)


@test
def e53_copy_transforms_with_an_influence():
    empty()
    target = link(bpy.data.objects.new("Target", None))
    target.location = (2, 0, 0)
    link(bpy.data.objects.new("Follower", None))
    applied(("add_constraint", {"ref": "Follower", "type": "COPY_TRANSFORMS", "target": "Target", "name": "Grab",
                                "influence": 0.5}))
    check(near(world_at("Follower", 1), (1, 0, 0)), "half way: %s" % list(world_at("Follower", 1)))


# -- entries 53, 59, 61: key and delete keys on any animatable path ---------------------------------------------------
@test
def e59_61_shape_key_influence_and_property_keys_and_one_deleted_key():
    empty()
    head = link(bpy.data.objects.new("Head", cube_mesh("Head")))
    head.shape_key_add(name="Basis")
    head.shape_key_add(name="Blink")
    holder = link(bpy.data.objects.new("Holder", None))
    head.constraints.new("COPY_LOCATION").name = "Grab"
    head.constraints["Grab"].target = holder
    head["fear"] = 0.0
    camera = link(bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam")))
    blink = 'key_blocks["Blink"].value'
    out = applied(*[("insert_key", {"ref": "Head", "owner": "shape_keys", "path": blink, "value": v, "frame": f})
                    for f, v in ((5, 0.0), (10, 1.0), (15, 0.0), (16, 0.0))],
                  ("insert_key", {"ref": "Head", "path": 'constraints["Grab"].influence', "value": 0, "frame": 1,
                                  "interpolation": "CONSTANT"}),
                  ("insert_key", {"ref": "Head", "path": 'constraints["Grab"].influence', "value": 1, "frame": 20,
                                  "interpolation": "CONSTANT"}),
                  ("insert_key", {"ref": "Head", "path": '["fear"]', "value": 0.8, "frame": 12}),
                  ("insert_key", {"ref": "Cam", "owner": "data", "path": "lens", "value": 85, "frame": 12}),
                  ("insert_key", {"ref": "Head", "path": "location", "index": 2, "value": 0.5, "frame": 12}))
    keys = head.data.shape_keys
    scene = bpy.context.scene
    scene.frame_set(10)
    check(abs(keys.key_blocks["Blink"].value - 1) < 1e-6, "blink keyed: %s" % keys.key_blocks["Blink"].value)
    scene.frame_set(19)
    check(head.constraints["Grab"].influence == 0, "influence held at 0 until 20")
    scene.frame_set(20)
    check(head.constraints["Grab"].influence == 1, "and 1 from 20")
    check(abs(head["fear"] - 0.8) < 1e-6 and abs(camera.data.lens - 85) < 1e-4, "custom property and lens keyed")
    location = [c for c in _curves_of(head) if c.data_path == "location"]
    check([c.array_index for c in location] == [2], "only location[2] keyed: %s" % [c.array_index for c in location])
    check(len(out["changes"]) >= 9, "listed: %s" % out["changes"])
    # Entry 61: one duplicate key goes, the right one, the others stay.
    applied(("delete_keys", {"ref": "Head", "owner": "shape_keys", "path": blink, "frames": [16, 16]}))
    frames = [p.co[0] for c in _curves_of(keys) for p in c.keyframe_points]
    check(frames == [5, 10, 15], "the right key deleted: %s" % frames)
    applied(("delete_keys", {"ref": "Head", "path": 'constraints["Grab"].influence'}))
    check(not any(c.data_path.startswith("constraints") for c in _curves_of(head)), "an emptied curve goes")
    # Undone with a failing batch: keys back, value back.
    failed_and_restored(("delete_keys", {"ref": "Head", "owner": "shape_keys", "path": blink}),
                        ("insert_key", {"ref": "Head", "path": '["fear"]', "value": 0.1, "frame": 40}), FAIL)
    check([p.co[0] for c in _curves_of(keys) for p in c.keyframe_points] == [5, 10, 15], "keys restored")
    message = refused(("insert_key", {"ref": "Head", "path": "location", "paths": ["location"], "frame": 1}))
    check(message and "one of them" in message, "paths and path: %s" % message)
    out = edit(("insert_key", {"ref": "Head", "path": "not_a_property", "frame": 1}))
    check(out["status"] == "failed" and "no property" in out["message"] and out["restored"], "unknown path: %s" % out.get("message"))


# -- entry 54: ground a falling body at its key frames, on the deformed pose ------------------------------------------
def falling_body():
    """A rig whose Spine bone (deforming a body) sinks 0.5 m between frames 1 and 20, keyed at frame 1."""
    data = bpy.data.armatures.new("Outlaw")
    rig = link(bpy.data.objects.new("Outlaw", data))
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    spine = data.edit_bones.new("Spine")
    spine.head, spine.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    body = link(bpy.data.objects.new("Outlaw_Body", cube_mesh("Outlaw_Body", offset=(0, 0, 0.9), size=1.8)))
    body.parent = rig
    body.vertex_groups.new(name="Spine").add(list(range(len(body.data.vertices))), 1.0, "REPLACE")
    body.modifiers.new("Armature", "ARMATURE").object = rig
    bone = rig.pose.bones["Spine"]
    for frame, y in ((1, 0.0), (20, -0.5)):
        bone.location = (0, y, 0)  # the bone's Y is world Z: it sinks
        bone.keyframe_insert("location", frame=frame)
    rig.keyframe_insert("location", frame=1)
    bpy.context.scene.frame_set(1)
    return rig, body


def lowest(name, frame):
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj(name).evaluated_get(graph)
    mesh = evaluated.to_mesh()
    low = min((evaluated.matrix_world @ v.co).z for v in mesh.vertices)
    evaluated.to_mesh_clear()
    return low


@test
def e54_fit_height_grounds_the_deformed_pose_at_a_frame_and_keys_it():
    empty()
    falling_body()
    check(abs(lowest("Outlaw_Body", 20) + 0.5) < 1e-4, "sunk 0.5 m at frame 20")
    bpy.context.scene.frame_set(1)
    out = applied(("fit_height", {"ref": "Outlaw", "ground": True, "frame": 20, "key": True}))
    check(bpy.context.scene.frame_current == 1, "the current frame stays")
    check(abs(lowest("Outlaw_Body", 20)) < 1e-4, "on the ground at 20: %s" % lowest("Outlaw_Body", 20))
    check(abs(lowest("Outlaw_Body", 1)) < 1e-4, "still on the ground at 1 (no jump): %s" % lowest("Outlaw_Body", 1))
    bounds = next(c for c in out["changes"] if c["field"] == "bounds")
    check(bounds["frame"] == 20 and abs(bounds["after"]["min"][2]) < 1e-4, "measured at 20: %s" % bounds)
    check(not out.get("warnings"), "keyed, so nothing to warn: %s" % out.get("warnings"))
    # A failing batch: the key and the location undone, frame 1 back.
    bpy.context.scene.frame_set(1)
    falling = failed_and_restored(("fit_height", {"ref": "Outlaw", "ground": True, "ground_z": 1, "frame": 10, "key": True}), FAIL)
    keys = sorted({p.co[0] for c in _curves_of(obj("Outlaw")) if c.data_path == "location" for p in c.keyframe_points})
    check(bpy.context.scene.frame_current == 1 and keys == [1.0, 20.0], "restored: frame %d, keys %s" % (
        bpy.context.scene.frame_current, keys))
    check(abs(lowest("Outlaw_Body", 20)) < 1e-4, "the earlier key holds: %s" % falling.get("message"))


def _curves_of(owner):
    from bpy_extras import anim_utils

    data = owner.animation_data
    bag = anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot) if data and data.action else None
    return list(bag.fcurves) if bag else []


for fn in TESTS:
    try:
        fn()
        results["passed"].append(fn.__name__)
    except Exception as error:  # noqa: BLE001
        results["failed"].append({"name": fn.__name__, "error": "%s: %s" % (type(error).__name__, error),
                                  "trace": traceback.format_exc()[-1500:]})
print("RESULT " + json.dumps(results), flush=True)
