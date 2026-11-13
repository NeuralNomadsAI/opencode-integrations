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


for fn in TESTS:
    try:
        fn()
        results["passed"].append(fn.__name__)
    except Exception as error:  # noqa: BLE001
        results["failed"].append({"name": fn.__name__, "error": "%s: %s" % (type(error).__name__, error),
                                  "trace": traceback.format_exc()[-1500:]})
print("RESULT " + json.dumps(results), flush=True)
